"""Stateful NetworkManager stand-in for recovery and socket checks."""

from jittersim.networking.commands import CommandResult
from jittersim.networking.executor import CommandExecutionError, FakeExecutor


class WifiExecutor(FakeExecutor):
    def __init__(self):
        super().__init__()
        self.settings = {
            "connection.type": "802-11-wireless",
            "connection.interface-name": "wlan0",
            # The installer creates the access point without autoconnect:
            # jittersim-ap.service starts it once the management firewall is up.
            "connection.autoconnect": "no",
            "802-11-wireless.ssid": "Copied home network",
            "802-11-wireless-security.key-mgmt": "wpa-psk",
            "802-11-wireless-security.proto": "wpa,rsn",
            "802-11-wireless-security.pairwise": "tkip,ccmp",
            "802-11-wireless-security.group": "tkip,ccmp",
            "802-11-wireless-security.pmf": "0",
            "802-11-wireless-security.psk": "fixture-wifi-password",
        }
        self.backups = {}
        self.active = "jittersim-ap"
        self.exists = True
        self.failures = set()
        self.calls = {}
        self.on_up = None
        self.keep_inactive = False
        self.radio_overrides = {}
        # `nmcli device wifi list`: replies in order (text or an exception), then scan_output.
        self.scan_replies = []
        self.scan_output = ""
        self.hwaddr = "B8\\:27\\:EB\\:00\\:00\\:01"  # As `nmcli -g` escapes it.
        # `nmcli -g GENERAL.STATE device show wlan0`: replies in order, then device_state.
        self.device_states = []
        self.device_state = "30 (disconnected)"

    def run(self, command):
        result = super().run(command)
        if command.executable == "wpa_cli":
            args = command.arguments[2:]
            prefix = "802-11-wireless-security."
            cipher = self.settings[prefix + "pairwise"].split(",")[-1].upper()
            group = self.settings[prefix + "group"].split(",")[-1].upper()
            key = self.settings[prefix + "key-mgmt"]
            pmf = self.settings[prefix + "pmf"]
            keys = "SAE" if key == "sae" else "WPA-PSK SAE" if pmf == "2" else "WPA-PSK"
            protocols = " ".join("WPA" if item == "wpa" else "RSN"
                                 for item in self.settings[prefix + "proto"].split(","))
            responses = {
                "status": f"mode=AP\nwpa_state=COMPLETED\npairwise_cipher={cipher}\ngroup_cipher={group}\n",
                "list_networks": "network id / ssid / bssid / flags\n7\tJitterSim\tany\t[CURRENT]\n",
                "key_mgmt": keys,
                "ieee80211w": {"0": "0", "1": "0", "2": "1", "3": "2"}[pmf],
                "proto": protocols,
            }
            responses.update(self.radio_overrides)
            name = args[-1] if args[0] == "get_network" else args[0]
            return CommandResult(0, stdout=responses[name] + "\n")
        if command.executable != "nmcli":
            return result
        args = command.arguments
        if "wifi" in args and "list" in args:
            reply = self.scan_replies.pop(0) if self.scan_replies else self.scan_output
            if isinstance(reply, Exception):
                raise reply
            return CommandResult(0, stdout=reply)
        if ("-g" in args and args[args.index("-g") + 1] == "GENERAL.STATE"
                and "device" in args):
            reply = self.device_states.pop(0) if self.device_states else self.device_state
            if isinstance(reply, Exception):
                raise reply
            return CommandResult(0, stdout=reply + "\n")
        if "-g" in args and args[args.index("-g") + 1] == "GENERAL.HWADDR":
            return CommandResult(0, stdout=self.hwaddr + "\n")
        if "-g" in args:
            fields = args[args.index("-g") + 1].split(",")
            if command.sensitive and command.expected_stdout is not None:
                if self.settings[fields[0]] + "\n" != command.expected_stdout:
                    raise CommandExecutionError("private networking command failed")
                return CommandResult(0)
            if fields == ["GENERAL.CONNECTION"]:
                return CommandResult(0, stdout=(self.active if args[-1] == "wlan0" else "") + "\n")
            name = args[-1]
            if fields == ["connection.id"]:
                return CommandResult(0, stdout=name if name in self.backups else "")
            values = self.backups.get(name) if name != "jittersim-ap" else self.settings
            if values is None or (name == "jittersim-ap" and not self.exists):
                raise CommandExecutionError("missing fixture profile")
            return CommandResult(0, stdout="".join(values.get(field, "") + "\n" for field in fields))
        operation = next(name for name in ("modify", "down", "up", "clone", "delete", "edit") if name in args)
        self.calls[operation] = self.calls.get(operation, 0) + 1
        if (operation, self.calls[operation]) in self.failures:
            raise CommandExecutionError("fixture-wifi-password", stderr="fixture-wifi-password")
        if operation == "clone":
            source, target = args[-2:]
            original = self.settings if source == "jittersim-ap" else self.backups[source]
            if target == "jittersim-ap":
                self.settings = dict(original)
                self.exists = True
            else:
                self.backups[target] = dict(original)
        elif operation == "delete":
            if args[-1] == "jittersim-ap":
                self.exists = False
            else:
                self.backups.pop(args[-1], None)
        elif operation == "edit":
            self.settings["802-11-wireless-security.psk"] = command.stdin.splitlines()[1]
        elif operation == "modify":
            name = args[args.index("id") + 1]
            values = self.settings if name == "jittersim-ap" else self.backups[name]
            offset = args.index(name) + 1
            for index in range(offset, len(args), 2):
                values[args[index]] = args[index + 1]
        elif operation == "down":
            self.active = ""
        else:
            if self.on_up:
                self.on_up()
            self.active = "" if self.keep_inactive else "jittersim-ap"
        return result

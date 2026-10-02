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
        if "-g" in args:
            fields = args[args.index("-g") + 1].split(",")
            if fields == ["GENERAL.CONNECTION"]:
                return CommandResult(0, stdout=(self.active if args[-1] == "wlan0" else "") + "\n")
            name = args[-1]
            if fields == ["connection.id"]:
                return CommandResult(0, stdout=name if name in self.backups else "")
            values = self.backups.get(name) if name != "jittersim-ap" else self.settings
            if values is None or (name == "jittersim-ap" and not self.exists):
                raise CommandExecutionError("missing fixture profile")
            return CommandResult(0, stdout="".join(values.get(field, "") + "\n" for field in fields))
        operation = next(name for name in ("modify", "down", "up", "clone", "delete") if name in args)
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

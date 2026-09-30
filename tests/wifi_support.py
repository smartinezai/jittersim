"""Stateful NetworkManager stand-in for recovery and socket checks."""

from jittersim.networking.commands import CommandResult
from jittersim.networking.executor import CommandExecutionError, FakeExecutor


class WifiExecutor(FakeExecutor):
    def __init__(self):
        super().__init__()
        self.settings = {
            "connection.type": "802-11-wireless",
            "connection.interface-name": "wlan0",
            "802-11-wireless.ssid": "Copied home network",
            "802-11-wireless-security.key-mgmt": "wpa-psk",
            "802-11-wireless-security.proto": "wpa,rsn",
            "802-11-wireless-security.pairwise": "tkip,ccmp",
            "802-11-wireless-security.group": "tkip,ccmp",
            "802-11-wireless-security.pmf": "0",
            "802-11-wireless-security.psk": "fixture-wifi-password",
        }
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
            responses = {
                "status": "mode=AP\nwpa_state=COMPLETED\npairwise_cipher=CCMP\ngroup_cipher=CCMP\n",
                "list_networks": "network id / ssid / bssid / flags\n7\tJitterSim\tany\t[CURRENT]\n",
                "key_mgmt": "SAE\n",
                "ieee80211w": "2\n",
            }
            responses.update(self.radio_overrides)
            name = args[-1] if args[0] == "get_network" else args[0]
            return CommandResult(0, stdout=responses[name])
        if command.executable != "nmcli":
            return result
        args = command.arguments
        if "-g" in args:
            fields = args[args.index("-g") + 1].split(",")
            if fields == ["GENERAL.CONNECTION"]:
                active = self.active if args[-1] == "wlan0" else ""
                return CommandResult(0, stdout=active + "\n")
            if not self.exists:
                raise CommandExecutionError("missing fixture profile")
            return CommandResult(0, stdout="".join(self.settings.get(name, "") + "\n"
                                                   for name in fields))
        operation = next(name for name in ("modify", "down", "up") if name in args)
        self.calls[operation] = self.calls.get(operation, 0) + 1
        if (operation, self.calls[operation]) in self.failures:
            raise CommandExecutionError("fixture-wifi-password", stderr="fixture-wifi-password")
        if operation == "modify":
            offset = args.index("jittersim-ap") + 1
            for index in range(offset, len(args), 2):
                self.settings[args[index]] = args[index + 1]
        elif operation == "down":
            self.active = ""
        else:
            if self.on_up:
                self.on_up()
            self.active = "" if self.keep_inactive else "jittersim-ap"
        return result

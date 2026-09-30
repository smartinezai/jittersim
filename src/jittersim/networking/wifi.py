"""Fixed-profile Wi-Fi changes with bounded recovery."""

from typing import Dict, Mapping, Optional, Tuple

from .commands import Command
from .executor import CommandExecutionError, CommandExecutor

DEFAULT_WIFI_NAME = "JitterSim"
AP_CONNECTION = "jittersim-ap"
WPA3_SETTINGS = {
    "802-11-wireless-security.key-mgmt": "sae",
    "802-11-wireless-security.proto": "rsn",
    "802-11-wireless-security.pairwise": "ccmp",
    "802-11-wireless-security.group": "ccmp",
    "802-11-wireless-security.pmf": "3",
}
SSID_PROPERTY = "802-11-wireless.ssid"
PROFILE_PROPERTIES = ("connection.type", "connection.interface-name", SSID_PROPERTY,
                      *WPA3_SETTINGS)
MAX_SETTINGS_BYTES = 4096


class WifiError(RuntimeError):
    """A safe Wi-Fi failure message that contains no command output."""

    def __init__(self, message: str, *, reason: str = "wifi-failed") -> None:
        super().__init__(message)
        self.reason = reason


class WifiController:
    """Change only the installed test profile on its assigned interface."""

    def __init__(self, executor: CommandExecutor, interface: str) -> None:
        self.executor = executor
        self.interface = interface

    def _run(self, arguments: Tuple[str, ...], *, timeout: float = 3,
             ignore_failure: bool = False) -> str:
        return self.executor.run(Command(
            executable="nmcli", arguments=arguments,
            purpose="manage the local JitterSim Wi-Fi connection",
            timeout_seconds=timeout, ignore_failure=ignore_failure,
        )).stdout

    def _settings(self) -> Dict[str, str]:
        output = self._run(("--escape", "no", "-g", ",".join(PROFILE_PROPERTIES),
                            "connection", "show", "id", AP_CONNECTION))
        values = output.splitlines()
        if len(output.encode("utf-8")) > MAX_SETTINGS_BYTES or len(values) != len(PROFILE_PROPERTIES):
            raise WifiError("Could not read the Pi Wi-Fi settings. No Wi-Fi change was made.")
        settings = dict(zip(PROFILE_PROPERTIES, values))
        if (settings["connection.type"] != "802-11-wireless"
                or settings["connection.interface-name"] != self.interface):
            raise WifiError("The Pi Wi-Fi connection belongs to a different interface.")
        if (not 1 <= len(settings[SSID_PROPERTY].encode("utf-8")) <= 32
                or any(ord(char) < 32 for value in values for char in value)):
            raise WifiError("The saved Pi Wi-Fi settings are invalid.")
        return settings

    def _modify(self, settings: Mapping[str, str], password: Optional[str] = None) -> None:
        arguments = ["connection", "modify", "id", AP_CONNECTION]
        for name, value in settings.items():
            if name in (SSID_PROPERTY, *WPA3_SETTINGS):
                arguments.extend((name, value))
        if password is not None:
            arguments.extend(("802-11-wireless-security.psk", password))
        self._run(tuple(arguments))

    def _activate(self) -> None:
        self._run(("--wait", "2", "connection", "down", "id", AP_CONNECTION),
                  ignore_failure=True)
        self._run(("--wait", "10", "connection", "up", "id", AP_CONNECTION,
                   "ifname", self.interface), timeout=12)

    def _verify(self, expected: Mapping[str, str]) -> None:
        actual = self._settings()
        if any(actual[name] != value for name, value in expected.items()):
            raise WifiError("The Pi Wi-Fi settings did not match the requested change.")
        active = self._run(("-g", "GENERAL.CONNECTION", "device", "show",
                            self.interface)).strip()
        if active != AP_CONNECTION:
            raise WifiError("The Pi Wi-Fi connection did not become active.")
        if expected.get("802-11-wireless-security.key-mgmt") == "sae":
            self._verify_wpa3()

    def _radio(self, *arguments: str) -> str:
        output = self.executor.run(Command(
            executable="wpa_cli", arguments=("-i", self.interface, *arguments),
            purpose="verify the local JitterSim Wi-Fi authentication",
            timeout_seconds=3,
        )).stdout
        if len(output.encode("utf-8")) > MAX_SETTINGS_BYTES:
            raise WifiError("Could not verify the active Pi Wi-Fi security.")
        return output.strip()

    def _verify_wpa3(self) -> None:
        status = dict(line.split("=", 1) for line in self._radio("status").splitlines()
                      if "=" in line)
        if any(status.get(name) != value for name, value in (
            ("mode", "AP"), ("wpa_state", "COMPLETED"),
            ("pairwise_cipher", "CCMP"), ("group_cipher", "CCMP"),
        )):
            raise WifiError("The Pi Wi-Fi radio did not start with WPA3 and AES.")
        networks = self._radio("list_networks").splitlines()
        current = [line.split("\t")[0] for line in networks if "[CURRENT]" in line]
        if len(current) != 1 or not current[0].isascii() or not current[0].isdigit():
            raise WifiError("Could not identify the active Pi Wi-Fi network.")
        identifier = current[0]
        if (self._radio("get_network", identifier, "key_mgmt") != "SAE"
                or self._radio("get_network", identifier, "ieee80211w") != "2"):
            raise WifiError("The Pi Wi-Fi radio did not require WPA3 authentication and protection.")

    def configure(self, ssid: str, password: str) -> int:
        expected = {SSID_PROPERTY: ssid, **WPA3_SETTINGS}
        try:
            self._settings()
            self._modify(expected, password)
            self._activate()
            self._verify(expected)
        except (CommandExecutionError, WifiError):
            raise WifiError(
                "Pi Wi-Fi could not be updated. Check the saved connection through management access."
            ) from None
        return 10

    def reset_name(self) -> Dict[str, str]:
        try:
            previous = self._settings()
        except (CommandExecutionError, WifiError):
            raise WifiError(
                "Could not inspect the Pi Wi-Fi connection. Check that jittersim-ap exists "
                "on the configured test interface. No Wi-Fi change was made."
            ) from None
        expected = {SSID_PROPERTY: DEFAULT_WIFI_NAME, **WPA3_SETTINGS}
        try:
            self._modify(expected)
            self._activate()
            self._verify(expected)
        except (CommandExecutionError, WifiError):
            try:
                self._modify(previous)
                self._activate()
                self._verify(previous)
            except (CommandExecutionError, WifiError):
                raise WifiError(
                    "Wi-Fi name reset failed and the previous connection could not be restored. "
                    "Use management access to repair jittersim-ap.",
                    reason="wifi-restore-failed",
                ) from None
            raise WifiError(
                "Wi-Fi name reset failed. The previous Wi-Fi settings were restored.",
                reason="wifi-restored",
            ) from None
        return {"ssid": DEFAULT_WIFI_NAME, "security": "WPA3-SAE"}

"""Fixed-profile Wi-Fi changes with bounded recovery."""

from typing import Dict, Mapping, Optional, Tuple

from .commands import Command
from .executor import CommandExecutionError, CommandExecutor

DEFAULT_WIFI_NAME = "JitterSim"
AP_CONNECTION = "jittersim-ap"
WPA2_SETTINGS = {
    "802-11-wireless-security.key-mgmt": "wpa-psk",
    "802-11-wireless-security.proto": "rsn",
    "802-11-wireless-security.pairwise": "ccmp",
    "802-11-wireless-security.group": "ccmp",
}
SSID_PROPERTY = "802-11-wireless.ssid"
PROFILE_PROPERTIES = ("connection.type", "connection.interface-name", SSID_PROPERTY,
                      *WPA2_SETTINGS)
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
            if name in (SSID_PROPERTY, *WPA2_SETTINGS):
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

    def configure(self, ssid: str, password: str) -> int:
        expected = {SSID_PROPERTY: ssid, **WPA2_SETTINGS}
        try:
            self._settings()
            self._modify(expected, password)
            self._activate()
            self._verify(expected)
        except (CommandExecutionError, WifiError):
            raise WifiError(
                "Pi Wi-Fi could not be updated. Check the saved connection through management access."
            ) from None
        return 6

    def reset_name(self) -> Dict[str, str]:
        try:
            previous = self._settings()
        except (CommandExecutionError, WifiError):
            raise WifiError(
                "Could not inspect the Pi Wi-Fi connection. Check that jittersim-ap exists "
                "on the configured test interface. No Wi-Fi change was made."
            ) from None
        expected = {SSID_PROPERTY: DEFAULT_WIFI_NAME, **WPA2_SETTINGS}
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
        return {"ssid": DEFAULT_WIFI_NAME, "security": "WPA2-AES"}

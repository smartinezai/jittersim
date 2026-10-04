"""Fixed-profile Wi-Fi changes with bounded recovery."""

from typing import Dict, Mapping, Optional, Tuple

from .commands import Command
from .executor import CommandExecutionError, CommandExecutor
from .wifi_security import PREFIX, SECURITY_PROPERTIES, saved_security, security_settings

DEFAULT_WIFI_NAME = "TestNet"
AP_CONNECTION = "jittersim-ap"
RECOVERY_CONNECTION = "jittersim-ap-recovery"
WPA3_SETTINGS = security_settings("WPA3")
SSID_PROPERTY = "802-11-wireless.ssid"
PROFILE_PROPERTIES = ("connection.type", "connection.interface-name", SSID_PROPERTY,
                      *SECURITY_PROPERTIES)
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
        self.commands_run = 0

    def _run(self, arguments: Tuple[str, ...], *, timeout: float = 3,
             ignore_failure: bool = False) -> str:
        self.commands_run += 1
        return self.executor.run(Command(
            executable="nmcli", arguments=arguments,
            purpose="manage the local JitterSim Wi-Fi connection",
            timeout_seconds=timeout, ignore_failure=ignore_failure,
        )).stdout

    def _settings(self, connection: str = AP_CONNECTION) -> Dict[str, str]:
        output = self._run(("--escape", "no", "-g", ",".join(PROFILE_PROPERTIES),
                            "connection", "show", "id", connection))
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
            if name in (SSID_PROPERTY, *SECURITY_PROPERTIES):
                arguments.extend((name, value))
        self._run(tuple(arguments))
        if password is not None:
            self._save_password(password)

    def _save_password(self, password: str) -> None:
        """Save and check the PSK without placing it in process arguments."""

        self.commands_run += 1
        self.executor.run(Command(
            executable="nmcli",
            arguments=("--colors", "no", "connection", "edit", "id", AP_CONNECTION),
            purpose="save the local Wi-Fi password privately",
            stdin=f"set {PREFIX}psk\n{password}\nsave persistent\nquit\n",
            sensitive=True,
            timeout_seconds=3,
        ))
        self.commands_run += 1
        self.executor.run(Command(
            executable="nmcli",
            arguments=("--escape", "no", "--show-secrets", "-g", PREFIX + "psk",
                       "connection", "show", "id", AP_CONNECTION),
            purpose="check the saved Wi-Fi password privately",
            sensitive=True,
            expected_stdout=password + "\n",
            timeout_seconds=3,
        ))

    def _activate(self) -> None:
        self._run(("--wait", "2", "connection", "down", "id", AP_CONNECTION),
                  ignore_failure=True)
        self._run(("--wait", "10", "connection", "up", "id", AP_CONNECTION,
                   "ifname", self.interface), timeout=12)

    def _verify(self, expected: Mapping[str, str], *, radio: bool = True,
                security: Optional[str] = None) -> Optional[str]:
        actual = self._settings()
        for name, value in expected.items():
            if name in (PREFIX + "proto", PREFIX + "pairwise", PREFIX + "group"):
                matches = set(actual[name].split(",")) == set(value.split(","))
            else:
                matches = actual[name] == value
            if not matches:
                raise WifiError("The Pi Wi-Fi settings did not match the requested change.")
        active = self._run(("-g", "GENERAL.CONNECTION", "device", "show",
                            self.interface)).strip()
        if active != AP_CONNECTION:
            raise WifiError("The Pi Wi-Fi connection did not become active.")
        if radio:
            return self._verify_radio(expected, security=security)
        return None

    def _radio(self, *arguments: str) -> str:
        self.commands_run += 1
        output = self.executor.run(Command(
            executable="wpa_cli", arguments=("-i", self.interface, *arguments),
            purpose="verify the local JitterSim Wi-Fi authentication",
            timeout_seconds=3,
        )).stdout
        if len(output.encode("utf-8")) > MAX_SETTINGS_BYTES:
            raise WifiError("Could not verify the active Pi Wi-Fi security.")
        return output.strip()

    def _verify_radio(self, expected: Mapping[str, str], *, security: Optional[str] = None) -> str:
        status = dict(line.split("=", 1) for line in self._radio("status").splitlines()
                      if "=" in line)
        if status.get("mode") != "AP" or status.get("wpa_state") != "COMPLETED":
            raise WifiError("The Pi Wi-Fi radio did not start in hotspot mode.")
        for name, property_name in (("pairwise_cipher", "pairwise"), ("group_cipher", "group")):
            allowed = set(expected[PREFIX + property_name].upper().split(","))
            if "" in allowed:
                allowed = {"CCMP", "TKIP"}
            if status.get(name) not in allowed:
                raise WifiError("The Pi Wi-Fi radio did not start with the requested encryption.")
        current = [line.split("\t")[0] for line in self._radio("list_networks").splitlines()
                   if "[CURRENT]" in line]
        if len(current) != 1 or not current[0].isascii() or not current[0].isdigit():
            raise WifiError("Could not identify the active Pi Wi-Fi network.")
        identifier = current[0]
        keys = set(self._radio("get_network", identifier, "key_mgmt").split())
        protocols = set(self._radio("get_network", identifier, "proto").split())
        if keys == {"SAE"} and protocols == {"RSN"}:
            mode = "WPA3"
        elif "SAE" in keys and protocols == {"RSN"}:
            mode = "WPA2/WPA3"
        elif protocols == {"WPA", "RSN"}:
            mode = "WPA/WPA2"
        elif protocols == {"WPA"}:
            mode = "WPA"
        elif protocols == {"RSN"}:
            mode = "WPA2"
        else:
            raise WifiError("The running Pi Wi-Fi security type is unsupported.")
        allowed_keys = {"WPA-PSK", "WPA-PSK-SHA256"}
        if mode == "WPA3":
            valid_keys = keys == {"SAE"}
        elif mode == "WPA2/WPA3":
            valid_keys = {"SAE", "WPA-PSK"} <= keys <= allowed_keys | {"SAE"}
        else:
            valid_keys = "WPA-PSK" in keys and keys <= allowed_keys
        expected_protocols = {"WPA" if item == "wpa" else "RSN"
                              for item in expected[PREFIX + "proto"].split(",") if item}
        if not expected_protocols:
            expected_protocols = {"WPA", "RSN"}
        pmf = self._radio("get_network", identifier, "ieee80211w")
        expected_pmf = {"0": "0", "1": "0", "2": "1", "3": "2"}.get(expected[PREFIX + "pmf"])
        if (not valid_keys or protocols != expected_protocols or pmf != expected_pmf
                or (expected[PREFIX + "key-mgmt"] == "sae" and mode != "WPA3")
                or (expected[PREFIX + "key-mgmt"] == "wpa-psk" and mode == "WPA3")
                or (security is not None and mode != security)):
            raise WifiError("The Pi cannot offer the requested Wi-Fi security type. Previous settings will be restored.")
        return mode

    def _backup_exists(self) -> bool:
        name = self._run(("-g", "connection.id", "connection", "show", "id", RECOVERY_CONNECTION),
                         ignore_failure=True).strip()
        return name == RECOVERY_CONNECTION

    def recover_pending(self) -> bool:
        """Restore an interrupted import from a NetworkManager-owned secret copy."""
        if not self._backup_exists():
            return False
        previous = self._settings(RECOVERY_CONNECTION)
        self._run(("connection", "delete", "id", AP_CONNECTION), ignore_failure=True)
        self._run(("connection", "clone", RECOVERY_CONNECTION, AP_CONNECTION))
        # Like the installer: jittersim-ap.service starts the Wi-Fi at boot once the
        # management firewall is loaded. NetworkManager must not start it earlier.
        self._run(("connection", "modify", "id", AP_CONNECTION,
                   "connection.autoconnect", "no"))
        self._activate()
        self._verify(previous, radio=False)
        self._run(("connection", "delete", "id", RECOVERY_CONNECTION))
        return True

    def configure(self, ssid: str, password: str, security: str = "WPA3") -> int:
        if (not isinstance(password, str) or not 8 <= len(password) <= 63
                or not password.isascii()
                or any(ord(character) < 32 or ord(character) == 127 for character in password)):
            raise ValueError("password must contain 8 to 63 printable ASCII characters")
        expected = {SSID_PROPERTY: ssid, **security_settings(security)}
        try:
            self.recover_pending()
            self._settings()
            self._run(("connection", "clone", AP_CONNECTION, RECOVERY_CONNECTION))
        except (CommandExecutionError, WifiError, ValueError):
            raise WifiError("Could not preserve the previous Pi Wi-Fi. No new settings were applied.") from None
        try:
            self._run(("connection", "modify", "id", RECOVERY_CONNECTION,
                       "connection.autoconnect", "no"))
            self._modify(expected, password)
            self._activate()
            self._verify(expected, security=security)
            self._run(("connection", "delete", "id", RECOVERY_CONNECTION))
        except (CommandExecutionError, WifiError, ValueError):
            try:
                self.recover_pending()
            except (CommandExecutionError, WifiError, ValueError):
                raise WifiError("Wi-Fi copy failed and recovery failed. Use management access to repair jittersim-ap.",
                                reason="wifi-restore-failed") from None
            raise WifiError("Wi-Fi copy failed or its security is unsupported on this Pi. Previous settings were restored.",
                            reason="wifi-restored") from None
        return self.commands_run

    def reset_name(self) -> Dict[str, str]:
        try:
            self.recover_pending()
            previous = self._settings()
            saved_security(previous)
        except (CommandExecutionError, WifiError, ValueError):
            raise WifiError("Could not inspect the Pi Wi-Fi connection. No Wi-Fi change was made.") from None
        expected = {**previous, SSID_PROPERTY: DEFAULT_WIFI_NAME}
        try:
            mode = self._verify_radio(previous)
            self._modify({SSID_PROPERTY: DEFAULT_WIFI_NAME})
            self._activate()
            self._verify(expected, security=mode)
        except (CommandExecutionError, WifiError, ValueError):
            try:
                self._modify(previous)
                self._activate()
                self._verify(previous, radio=False)
            except (CommandExecutionError, WifiError):
                raise WifiError("Wi-Fi name reset failed and the previous connection could not be restored. Use management access to repair jittersim-ap.",
                                reason="wifi-restore-failed") from None
            raise WifiError("Wi-Fi name reset failed. The previous Wi-Fi settings were restored.",
                            reason="wifi-restored") from None
        return {"ssid": DEFAULT_WIFI_NAME, "security": mode}

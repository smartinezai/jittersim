import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

FAKE_NMCLI = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_file = Path(os.environ["WIFI_TEST_STATE"])
state = json.loads(state_file.read_text())
args = sys.argv[1:]
state["commands"].append(args)
state.setdefault("password_in_environment", []).append("JITTERSIM_AP_PASSWORD" in os.environ)
try:
    os.fstat(9)
    state.setdefault("lock_in_child", []).append(True)
except OSError:
    state.setdefault("lock_in_child", []).append(False)
code = 0
output = ""
if "--fields" in args:
    output = "jittersim-ap:" + state["settings"]["connection.uuid"] + "\n" if state["exists"] else ""
elif "-g" in args:
    fields = args[args.index("-g") + 1].split(",")
    if fields == ["GENERAL.CONNECTION"]:
        output = "jittersim-ap\n" if state["exists"] else "\n"
    elif not state["exists"]:
        code = 1
    else:
        output = "".join(state["settings"].get(field, "") + "\n" for field in fields)
elif args[:2] == ["connection", "show"]:
    code = 0 if state["exists"] else 1
elif args[:2] == ["connection", "add"]:
    if state.get("reject_security"):
        code = 7
    else:
        state["exists"] = True
        aliases = {"type": "connection.type", "ifname": "connection.interface-name",
                   "con-name": "connection.id", "ssid": "802-11-wireless.ssid"}
        for index in range(2, len(args), 2):
            name = aliases.get(args[index], args[index])
            state["settings"][name] = "802-11-wireless" if name == "connection.type" else args[index + 1]
elif "edit" in args:
    state["settings"]["802-11-wireless-security.psk"] = sys.stdin.read().splitlines()[1]
elif "delete" in args:
    state["exists"] = False
elif args[:2] == ["connection", "modify"]:
    if state.get("reject_security") and "802-11-wireless-security.proto" in args:
        code = 7
    else:
        offset = 4 if args[2] == "uuid" else 3
        for index in range(offset, len(args), 2):
            state["settings"][args[index]] = args[index + 1]
else:
    code = 99
state_file.write_text(json.dumps(state))
sys.stdout.write(output)
sys.exit(code)
"""


class WifiInstallationTests(unittest.TestCase):
    def run_setup(self, *, exists, reject_security=False, password="new-fixture-password", exported=False):
        installer = (Path(__file__).resolve().parents[2] / "deploy/raspberry-pi/install.sh").read_text()
        start = installer.index('. "$CONFIG_DIR/ap.env"')
        end = installer.index("\ndashboard_ip=", start)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = {
                "connection.uuid": "bc5f2c63-8e53-4a10-b4f4-0db16f5dc8ee",
                "connection.type": "802-11-wireless",
                "connection.interface-name": "wlan0",
                "connection.autoconnect": "no",
                "802-11-wireless.mode": "ap",
                "802-11-wireless-security.key-mgmt": "wpa-psk",
                "802-11-wireless.ssid": "Existing household name",
                "802-11-wireless-security.psk": "existing-wifi-secret",
                "802-11-wireless-security.proto": "wpa,rsn",
                "802-11-wireless-security.pairwise": "tkip,ccmp",
                "802-11-wireless-security.group": "tkip,ccmp",
            } if exists else {}
            state = root / "state.json"
            state.write_text(json.dumps({"exists": exists, "settings": settings,
                                        "commands": [], "reject_security": reject_security}))
            nmcli = root / "nmcli"
            nmcli.write_text(FAKE_NMCLI)
            nmcli.chmod(0o700)
            (root / "ap.env").write_text(
                "JITTERSIM_AP_SSID=TestNet\nJITTERSIM_AP_PASSWORD=" + shlex.quote(password) + "\n"
            )
            script = root / "setup.sh"
            source = root / "source"
            scripts = source / "deploy/raspberry-pi"
            scripts.mkdir(parents=True)
            package = source / "src/jittersim"
            package.mkdir(parents=True)
            from tests.unit.test_installer_lock import simulated_installer_manager
            (package / "updates.py").write_text(simulated_installer_manager(
                root / "updates", root / "installed-manager.py"))
            actual_scripts = Path(__file__).resolve().parents[2] / "deploy/raspberry-pi"
            shutil.copy2(actual_scripts / "create-ap-profile.py", scripts)
            (scripts / "install-lock.py").write_text(
                (actual_scripts / "install-lock.py").read_text().replace(
                    'LOCK_ROOT = Path("/var/lib/jittersim-updates")',
                    "LOCK_ROOT = Path(" + repr(str(root / "updates")) + ")"))
            lock_start = installer.index('if [ "${JITTERSIM_INSTALL_LOCK_FD:-}"')
            lock_end = installer.index("\nif ! getent", lock_start)
            script.write_text("set -eu\nset +x\nenable_passwordless_staging=no\n" + installer[lock_start:lock_end] + "\n"
                              + installer[start:end]
                              + '\n[ "$ap_settings_before" = "$ap_settings_after" ]'
                              + ' || printf "Settings changed\\n"\n')
            result = subprocess.run(["sh", str(script)], capture_output=True, text=True, timeout=10,
                                    env={**os.environ, "CONFIG_DIR": directory,
                                         "SOURCE_DIR": str(source),
                                         **({"JITTERSIM_AP_PASSWORD": password} if exported else {}),
                                         "JITTERSIM_TEST_INTERFACE": "wlan0",
                                         "WIFI_TEST_STATE": str(state),
                                         "PATH": directory + os.pathsep + os.environ["PATH"]})
            return result, json.loads(state.read_text())

    def test_damaged_existing_credentials_are_not_overwritten(self):
        installer = (Path(__file__).resolve().parents[2] / "deploy/raspberry-pi/install.sh").read_text()
        start = installer.index('if [ -e "$CONFIG_DIR/ap.env" ]')
        end = installer.index('chown root:root "$CONFIG_DIR/ap.env"', start)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for kind in ("empty", "directory", "symlink"):
                with self.subTest(kind=kind):
                    credentials = root / "ap.env"
                    if kind == "empty":
                        credentials.touch()
                    elif kind == "directory":
                        credentials.mkdir()
                    else:
                        credentials.symlink_to(root / "absent")
                    result = subprocess.run(["sh", "-c", "set -eu\n" + installer[start:end]],
                                            capture_output=True, text=True,
                                            env={**os.environ, "CONFIG_DIR": directory})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("management access", result.stderr)
                    if kind == "empty":
                        self.assertEqual(credentials.read_bytes(), b"")
                    if kind == "directory":
                        credentials.rmdir()
                    else:
                        credentials.unlink()

    def test_fresh_install_requires_wpa3_and_aes(self):
        result, state = self.run_setup(exists=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["settings"]["802-11-wireless.ssid"], "TestNet")
        self.assertEqual(state["settings"]["802-11-wireless-security.psk"], "new-fixture-password")
        self.assert_security(state["settings"])

    def test_fresh_install_keeps_literal_password_out_of_arguments_and_environment(self):
        password = ' leading " # \\ $ ` ; trailing '
        result, state = self.run_setup(exists=False, password=password, exported=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["settings"]["802-11-wireless-security.psk"], password)
        self.assertNotIn(password, repr(state["commands"]))
        self.assertFalse(any(state["password_in_environment"]))
        self.assertFalse(any(state["lock_in_child"]))
        self.assertNotIn(password, result.stdout + result.stderr)

    def test_upgrade_preserves_credentials_and_security_without_restart(self):
        result, state = self.run_setup(exists=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["settings"]["802-11-wireless.ssid"], "Existing household name")
        self.assertEqual(state["settings"]["802-11-wireless-security.psk"], "existing-wifi-secret")
        self.assertEqual(state["settings"]["802-11-wireless-security.proto"], "wpa,rsn")
        self.assertEqual(state["settings"]["802-11-wireless-security.pairwise"], "tkip,ccmp")
        self.assertFalse(any("up" in command or "down" in command for command in state["commands"]))

    def test_test_wifi_leases_are_short_so_a_device_back_on_the_router_recovers_soon(self):
        # A device that roams from the Pi back to the router (same name) keeps
        # its 192.168.50.x lease and only asks the router again when it rebinds
        # at 7/8 of the lease. NetworkManager's default of one hour can leave it
        # without internet for about 50 minutes after the router Wi-Fi returns.
        for exists in (False, True):
            with self.subTest(exists=exists):
                result, state = self.run_setup(exists=exists)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(state["settings"]["ipv4.shared-dhcp-lease-time"], "300")
                self.assertEqual(state["settings"]["ipv4.shared-dhcp-range"],
                                 "192.168.50.100,192.168.50.199")
                if exists:
                    # The running Wi-Fi is not restarted. install.sh tells the
                    # owner because the compared settings now include the lease.
                    self.assertFalse(any("up" in command or "down" in command
                                         for command in state["commands"]))
        installer = (Path(__file__).resolve().parents[2] / "deploy/raspberry-pi/install.sh").read_text()
        self.assertEqual(installer.count("ipv4.shared-dhcp-range,ipv4.shared-dhcp-lease-time,"), 2)

    def test_unsupported_security_fails_without_trying_weaker_settings(self):
        result, state = self.run_setup(exists=False, reject_security=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("802-11-wireless-security.psk", state["settings"])
        changes = [command for command in state["commands"] if "add" in command]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0][changes[0].index("802-11-wireless-security.proto") + 1], "rsn")

    def assert_security(self, settings):
        self.assertEqual(settings["802-11-wireless-security.key-mgmt"], "sae")
        self.assertEqual(settings["802-11-wireless-security.pmf"], "3")
        self.assertEqual(settings["802-11-wireless-security.proto"], "rsn")
        self.assertEqual(settings["802-11-wireless-security.pairwise"], "ccmp")
        self.assertEqual(settings["802-11-wireless-security.group"], "ccmp")

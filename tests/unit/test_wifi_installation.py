import json
import os
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
code = 0
output = ""
if "-g" in args:
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
    state["exists"] = True
    state["settings"]["802-11-wireless.ssid"] = args[args.index("ssid") + 1]
elif args[:2] == ["connection", "modify"]:
    if state.get("reject_security") and "802-11-wireless-security.proto" in args:
        code = 7
    else:
        for index in range(3, len(args), 2):
            state["settings"][args[index]] = args[index + 1]
else:
    code = 99
state_file.write_text(json.dumps(state))
sys.stdout.write(output)
sys.exit(code)
"""


class WifiInstallationTests(unittest.TestCase):
    def run_setup(self, *, exists, reject_security=False):
        installer = (Path(__file__).resolve().parents[2] / "deploy/raspberry-pi/install.sh").read_text()
        start = installer.index('. "$CONFIG_DIR/ap.env"')
        end = installer.index("\ndashboard_ip=", start)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = {
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
                "JITTERSIM_AP_SSID=JitterSim\nJITTERSIM_AP_PASSWORD=new-fixture-password\n"
            )
            script = root / "setup.sh"
            script.write_text("set -eu\n" + installer[start:end]
                              + '\n[ "$ap_settings_before" = "$ap_settings_after" ]'
                              + ' || printf "Settings changed\\n"\n')
            result = subprocess.run(["sh", str(script)], capture_output=True, text=True, timeout=10,
                                    env={**os.environ, "CONFIG_DIR": directory,
                                         "JITTERSIM_TEST_INTERFACE": "wlan0",
                                         "WIFI_TEST_STATE": str(state),
                                         "PATH": directory + os.pathsep + os.environ["PATH"]})
            return result, json.loads(state.read_text())

    def test_fresh_install_requires_wpa3_and_aes(self):
        result, state = self.run_setup(exists=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["settings"]["802-11-wireless.ssid"], "JitterSim")
        self.assertEqual(state["settings"]["802-11-wireless-security.psk"], "new-fixture-password")
        self.assert_security(state["settings"])

    def test_upgrade_preserves_credentials_and_security_without_restart(self):
        result, state = self.run_setup(exists=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["settings"]["802-11-wireless.ssid"], "Existing household name")
        self.assertEqual(state["settings"]["802-11-wireless-security.psk"], "existing-wifi-secret")
        self.assertEqual(state["settings"]["802-11-wireless-security.proto"], "wpa,rsn")
        self.assertEqual(state["settings"]["802-11-wireless-security.pairwise"], "tkip,ccmp")
        self.assertFalse(any("up" in command or "down" in command for command in state["commands"]))

    def test_unsupported_security_fails_without_trying_weaker_settings(self):
        result, state = self.run_setup(exists=False, reject_security=True)
        self.assertEqual(result.returncode, 7)
        self.assertNotIn("802-11-wireless-security.psk", state["settings"])
        changes = [command for command in state["commands"] if "modify" in command]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0][changes[0].index("802-11-wireless-security.proto") + 1], "rsn")

    def assert_security(self, settings):
        self.assertEqual(settings["802-11-wireless-security.key-mgmt"], "sae")
        self.assertEqual(settings["802-11-wireless-security.pmf"], "3")
        self.assertEqual(settings["802-11-wireless-security.proto"], "rsn")
        self.assertEqual(settings["802-11-wireless-security.pairwise"], "ccmp")
        self.assertEqual(settings["802-11-wireless-security.group"], "ccmp")

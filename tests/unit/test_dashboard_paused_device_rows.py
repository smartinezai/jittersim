"""A protected device never shows its saved profile as applied or degraded."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required to run the dashboard")


def served_functions():
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
    start = script.index("function deviceIsPaused(")
    return script[start : script.index("async function saveObservedMac(", start)]


PRELUDE = """
const profileName = (id) => id === "bad" ? "Bad test" : id;
const deviceConditions = { applied: true, devices: {} };
const appliedProfileCell = (name, state) => [name, "Applied"];
const deviceConditionCell = (id) => ["Degraded", "upload: delay 1000 ms"];
"""


def view(device):
    program = (
        PRELUDE
        + served_functions()
        + "\nconst d = %s;\nconsole.log(JSON.stringify({paused: deviceIsPaused(d), "
        "profile: deviceProfileCell(d), state: deviceStateCell(d), rowClass: deviceRowClass(d)}));"
        % json.dumps(device)
    )
    result = subprocess.run([NODE, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


BASE = {"id": "aeromac", "profile_id": "bad", "mac_address": None}


def test_a_protected_device_shows_its_profile_as_paused_and_greyed_out():
    shown = view({**BASE, "mac_address": "d6:ac:49:a3:ee:6a", "effective_state": "protected"})
    assert shown["paused"] is True
    assert shown["rowClass"] == "protected"
    assert shown["state"][0] == "Protected: profile paused"
    assert shown["profile"] == ["Bad test", "Saved profile · paused"]
    assert "Degraded" not in json.dumps(shown)


def test_a_device_that_cannot_be_checked_is_paused_with_the_reason():
    note = "This saved device has no MAC address so JitterSim cannot tell it is the protected Aeromac."
    shown = view({**BASE, "effective_state": "unverified", "effective_note": note})
    assert shown["paused"] is True
    assert shown["rowClass"] == "paused"
    assert shown["state"] == ["Profile paused", note]
    assert "Degraded" not in json.dumps(shown)


def test_an_unverified_device_without_a_note_still_explains_itself():
    shown = view({**BASE, "effective_state": "unverified"})
    assert "no MAC address" in shown["state"][1]


def test_an_active_device_keeps_the_normal_cells():
    shown = view({**BASE, "mac_address": "ba:bb:00:00:00:02", "effective_state": "active"})
    assert shown["paused"] is False
    assert shown["rowClass"] == ""
    assert shown["state"] == ["Degraded", "upload: delay 1000 ms"]
    assert shown["profile"] == ["Bad test", "Applied"]

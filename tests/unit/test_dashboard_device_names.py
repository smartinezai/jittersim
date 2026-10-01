"""Run the dashboard's device naming helpers in node."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML


def served_functions():
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
    start = script.index("function deviceDisplayName(")
    return script[start : script.index("function updateTrafficDeviceOptions(", start)]


CASES = {
    "uses_the_display_name_first": (
        {"display_name": "Sam MacBook", "name": "Client 1.2.3.4", "ipv4_address": "1.2.3.4"},
        "Sam MacBook", "Sam MacBook (1.2.3.4)",
    ),
    "falls_back_to_the_saved_name": (
        {"name": "Kitchen tablet", "ipv4_address": "1.2.3.4"},
        "Kitchen tablet", "Kitchen tablet (1.2.3.4)",
    ),
    "falls_back_to_the_assigned_name_then_the_address": (
        {"assigned_device_name": "Hall", "ipv4_address": "1.2.3.4"},
        "Hall", "Hall (1.2.3.4)",
    ),
    "address_only": ({"ipv4_address": "1.2.3.4"}, "1.2.3.4", "1.2.3.4 (1.2.3.4)"),
    "nothing_known": ({}, "Unknown device", "Unknown device"),
}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
@pytest.mark.parametrize("name", CASES)
def test_naming_helpers(name):
    device, title, picker = CASES[name]
    program = (
        served_functions()
        + "\nconst device = " + json.dumps(device) + ";\n"
        + "console.log(JSON.stringify([deviceDisplayName(device), devicePickerLabel(device)]));\n"
    )
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [title, picker]

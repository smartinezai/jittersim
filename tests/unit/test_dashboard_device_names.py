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


SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)


def function_body(name):
    """Return the source of one top-level dashboard function."""

    match = re.search(r"\n    (?:async )?function " + re.escape(name) + r"\(", SCRIPT)
    assert match, name
    following = re.search(r"\n    (?:async )?function \w+\(", SCRIPT[match.end() :])
    end = match.end() + following.start() if following else len(SCRIPT)
    return SCRIPT[match.start() : end]


@pytest.mark.parametrize(
    "old",
    [
        'device.name + " (" + device.ipv4_address + ")"',
        'device.name + " (" + device.id + ")"',
        "group.devices.map((device) => device.name)",
        "device.assigned_device_name || device.ipv4_address",
        'item.device_name + " (" + item.device_id + ")"',
    ],
)
def test_the_raw_name_expressions_are_gone_from_the_dashboard(old):
    assert old not in SCRIPT


@pytest.mark.parametrize(
    "function",
    ["updateTrafficDeviceOptions", "updateGroupMemberOptions", "loadDevicesForSchedule"],
)
def test_each_device_picker_uses_the_picker_label(function):
    assert "option.textContent = devicePickerLabel(device);" in function_body(function)


def test_the_traffic_discovery_card_title_uses_the_display_name():
    assert "title.textContent = deviceDisplayName(device);" in function_body("renderObservations")


def test_the_groups_card_lists_display_names():
    assert (
        'group.devices.map((device) => deviceDisplayName(device)).join(", ")'
        in function_body("renderGroups")
    )


def test_the_analytics_picker_and_table_row_use_the_display_name():
    assert (
        'option.textContent = deviceDisplayName(device) + " (" + device.id + ")";'
        in function_body("loadRandomWebsiteAnalytics")
    )
    assert (
        '(item.display_name || item.device_name) + " (" + item.device_id + ")"'
        in function_body("renderRandomWebsiteAnalytics")
    )

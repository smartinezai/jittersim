"""Run the dashboard's connected-row state rules in node."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

CONTEXT = {
    "helperConfigured": True,
    "profileCount": 2,
    "groupCount": 1,
    "selectedProfile": "slow",
    "selectedGroup": "g1",
}
NORMAL = {
    "unaffected": False,
    "ipv4_address": "192.168.50.10",
    "association_verified": True,
    "assigned_device_id": None,
    "group_id": None,
}
PROTECTED = {**NORMAL, "unaffected": True}

ENABLED = {
    "protectedRow": False,
    "rowClass": "connected-row",
    "badge": "",
    "disableProfile": False,
    "disableApply": False,
    "disableGroupSelect": False,
    "disableGroupButton": False,
    "protectionLabel": "Make unaffected",
}

CASES = {
    "protected_row_is_marked_and_four_controls_are_disabled": (
        PROTECTED,
        CONTEXT,
        {
            "protectedRow": True,
            "rowClass": "connected-row protected",
            "badge": "Protected",
            "disableProfile": True,
            "disableApply": True,
            "disableGroupSelect": True,
            "disableGroupButton": True,
            "protectionLabel": "Remove protection",
        },
    ),
    "normal_row_is_unchanged_and_nothing_is_disabled": (NORMAL, CONTEXT, ENABLED),
    "normal_row_without_an_address_loses_profile_and_group_controls": (
        {**NORMAL, "ipv4_address": ""},
        CONTEXT,
        {
            **ENABLED,
            "disableProfile": True,
            "disableApply": True,
            "disableGroupSelect": True,
            "disableGroupButton": True,
        },
    ),
    "normal_row_not_verified_loses_profile_and_group_controls": (
        {**NORMAL, "association_verified": False},
        CONTEXT,
        {
            **ENABLED,
            "disableProfile": True,
            "disableApply": True,
            "disableGroupSelect": True,
            "disableGroupButton": True,
        },
    ),
    "helper_not_configured_disables_apply_and_group_only": (
        NORMAL,
        {**CONTEXT, "helperConfigured": False},
        {
            **ENABLED,
            "disableApply": True,
            "disableGroupSelect": True,
            "disableGroupButton": True,
        },
    ),
    "apply_needs_a_chosen_profile": (
        NORMAL,
        {**CONTEXT, "selectedProfile": ""},
        {**ENABLED, "disableApply": True},
    ),
    "apply_needs_at_least_one_saved_profile": (
        NORMAL,
        {**CONTEXT, "profileCount": 0},
        {**ENABLED, "disableApply": True},
    ),
    "no_groups_disables_the_group_controls": (
        NORMAL,
        {**CONTEXT, "groupCount": 0},
        {**ENABLED, "disableGroupSelect": True, "disableGroupButton": True},
    ),
    "group_button_needs_a_chosen_group": (
        NORMAL,
        {**CONTEXT, "selectedGroup": ""},
        {**ENABLED, "disableGroupButton": True},
    ),
    "group_button_is_disabled_for_the_current_group": (
        {**NORMAL, "group_id": "g1"},
        CONTEXT,
        {**ENABLED, "disableGroupButton": True},
    ),
    "group_button_is_enabled_for_a_different_group": (
        {**NORMAL, "group_id": "g2"},
        CONTEXT,
        ENABLED,
    ),
    "protected_row_without_a_verified_association_still_looks_protected": (
        {**PROTECTED, "association_verified": False},
        CONTEXT,
        {
            "protectedRow": True,
            "rowClass": "connected-row protected",
            "badge": "Protected",
            "disableProfile": True,
            "disableApply": True,
            "disableGroupSelect": True,
            "disableGroupButton": True,
            "protectionLabel": "Remove protection",
        },
    ),
    "protected_row_without_an_address_still_looks_protected": (
        {**PROTECTED, "ipv4_address": ""},
        CONTEXT,
        {
            "protectedRow": True,
            "rowClass": "connected-row protected",
            "badge": "Protected",
            "disableProfile": True,
            "disableApply": True,
            "disableGroupSelect": True,
            "disableGroupButton": True,
            "protectionLabel": "Remove protection",
        },
    ),
    "protected_row_stays_disabled_whatever_the_context_says": (
        {**PROTECTED, "group_id": "g2"},
        {**CONTEXT, "selectedProfile": "", "selectedGroup": ""},
        {
            "protectedRow": True,
            "rowClass": "connected-row protected",
            "badge": "Protected",
            "disableProfile": True,
            "disableApply": True,
            "disableGroupSelect": True,
            "disableGroupButton": True,
            "protectionLabel": "Remove protection",
        },
    ),
}


def served_function():
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
    start = script.index("function connectedRowState(")
    return script[start : script.index("function renderConnectedDevices(", start)]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
@pytest.mark.parametrize("name", CASES)
def test_connected_row_state(name):
    device, context, expected = CASES[name]
    program = (
        served_function()
        + "\nconst device = " + json.dumps(device) + ";\n"
        + "const context = " + json.dumps(context) + ";\n"
        + "console.log(JSON.stringify(connectedRowState(device, context)));\n"
    )
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected

"""The page marks "This device" only on the row whose Wi-Fi address the Pi named for this browser.

The functions come from the dashboard exactly as it is served. The browser module
tests/browser/dashboard_access.py checks the label on the page and tests/integration/
test_dashboard_access_http.py checks the field the Pi sends.
"""

import json
import subprocess

import pytest

from tests.dashboard_script import NODE, function_source, run_node

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required to run the dashboard")

MINE = "02:ab:cd:00:00:01"
OTHER = "02:00:00:00:00:02"


def listed_as_this_device(named, listed):
    """What isThisDevice says about each listed address when the Pi named `named` (or nothing)."""

    state = "let thisDeviceMac = " + ("null" if named is None else '"' + named.lower() + '"') + ";"
    return run_node(
        [state, function_source("isThisDevice")],
        "[" + ",".join("isThisDevice(%s)" % value for value in listed) + "]",
    )


def test_only_the_named_address_matches_whatever_its_letter_case():
    assert listed_as_this_device(MINE, ['"%s"' % MINE, '"%s"' % MINE.upper(), '"%s"' % OTHER]) == [
        True, True, False]


def test_nothing_matches_when_the_pi_named_no_address():
    # Loopback, the management network, a router or proxy in between: the Pi sends null.
    assert listed_as_this_device(None, ['"%s"' % MINE, '"%s"' % OTHER]) == [False, False]


def test_a_row_without_an_address_never_matches():
    assert listed_as_this_device(MINE, ["null", "undefined", '""', "42"]) == [
        False, False, False, False]


def named_after_sign_in(answer):
    """thisDeviceMac after loadDashboardAccess read this status answer from the Pi."""

    program = "\n".join([
        "let thisDeviceMac = null, addressTokenRevision = null, adminTokenRevision = 1;",
        "const answer = %s;" % json.dumps(answer),
        "const request = async () => answer;",
        "const stubs = {};",
        "const element = (id) => stubs[id] = stubs[id] || {};",
        "const accessReason = () => '', setDashboardUnlocked = () => {};",
        "const loadAddressMoves = async () => {};",
        "async " + function_source("loadDashboardAccess"),
        "loadDashboardAccess().then(() => console.log(JSON.stringify(thisDeviceMac)));",
    ])
    result = subprocess.run([NODE, "-"], input=program, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("answer, expected", [
    ({"network": "test", "mac_address": MINE.upper()}, MINE),
    # An Ethernet peer can carry its own MAC. Only the Wi-Fi ("test") network counts.
    ({"network": "management", "mac_address": MINE}, None),
    ({"network": "test", "mac_address": None}, None),
    ({"mac_address": MINE}, None),
])
def test_only_a_wifi_address_from_the_pi_is_kept_at_sign_in(answer, expected):
    assert named_after_sign_in({"allowed": True, **answer}) == expected

"""The page marks "This device" only on the row whose Wi-Fi address the Pi named for this browser.

The function comes from the dashboard exactly as it is served. The browser module
tests/browser/dashboard_access.py checks the label on the page and tests/integration/
test_dashboard_access_http.py checks the field the Pi sends.
"""

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

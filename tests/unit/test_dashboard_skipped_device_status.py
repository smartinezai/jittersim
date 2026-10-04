"""Saved device profiles remain visible when live identity cannot be checked."""

import json

import pytest

from tests.unit.test_dashboard_apply_summary import (
    between, needs_node, render, run_node, served_functions,
)


def applied_message(data):
    consumer = between("function appliedMessage(", 'element("apply-button").addEventListener')
    return run_node(served_functions() + consumer +
                    "\nconsole.log(JSON.stringify(appliedMessage(%s, false)));" % json.dumps(data))


@needs_node
@pytest.mark.parametrize("devices,general,skipped", [(0, None, 3), (2, None, 1), (0, "Slow", 2)])
def test_start_reports_skipped_profiles_without_losing_applied_counts(devices, general, skipped):
    result = applied_message({
        "applied": {"devices": devices, "groups": 0, "service_lists": 0,
                    "protected": 1, "general_profile": general, "access_rules": False},
        "unverified_device_ids": ["saved-%s" % i for i in range(skipped)],
    })
    phrase = ("1 saved device profile was skipped" if skipped == 1 else
              "%s saved device profiles were skipped" % skipped)
    assert phrase in result
    assert "No device profiles" not in result
    assert "check" in result.lower()
    if devices:
        assert "%s devices with a profile" % devices in result
    if general:
        assert 'Profile "Slow" for every unprotected device' in result
    assert "1 protected device stays untouched" in result


@needs_node
@pytest.mark.parametrize("skipped", [None, "unknown", {}, []])
def test_absent_or_malformed_optional_ids_preserve_the_true_empty_case(skipped):
    data = {"applied": {"devices": 0, "groups": 0, "service_lists": 0,
                        "protected": 0, "access_rules": False}}
    if skipped is not None:
        data["unverified_device_ids"] = skipped
    assert applied_message(data) == "Started. No device profiles or service lists are saved yet."


@needs_node
def test_saved_summary_counts_pending_identity_without_counting_protected_devices():
    result = render('''
      savedDevices = [
        {id: "a", mac_address: "m1", effective_state: "active"},
        {id: "b", mac_address: null, effective_state: "unverified"},
        {id: "c", mac_address: "m3", effective_state: "protected"},
        {id: "d", mac_address: "m3", effective_state: "unverified"}
      ];
      groups = [{id: "g", device_ids: ["b", "c", "d"]}];
      unaffectedMacs = ["m3"];
      renderApplySummary();
      console.log(JSON.stringify(element("apply-summary").textContent));
    ''')
    assert result == ("Saved: 1 device with a profile · "
                      "1 saved device profile needs an identity check · "
                      "1 protected device stays untouched")


@needs_node
@pytest.mark.parametrize("general", [False, True])
def test_all_unverified_profiles_are_saved_even_with_general_precedence(general):
    result = render('''
      savedDevices = [
        {id: "a", effective_state: "unverified"},
        {id: "b", effective_state: "unverified"},
        {id: "c", effective_state: "unverified"}
      ];
      unaffectedMacs = ["protected"];
      generalProfileState = %s;
      renderApplySummary();
      console.log(JSON.stringify(element("apply-summary").textContent));
    ''' % json.dumps({"profile": {"id": "slow", "name": "Slow"}} if general else {"profile": None}))
    expected = "Saved: "
    if general:
        expected += 'Profile "Slow" for every unprotected device · '
    expected += ("3 saved device profiles need an identity check · "
                 "1 protected device stays untouched")
    assert result == expected
    assert "skipped" not in result

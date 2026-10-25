"""Exercise latest-scan comparison and the served discovery state transitions."""

import shutil

import pytest

from tests.unit.test_dashboard_traffic_groups import device, example_device, run_dashboard

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")


def test_new_markers_survive_refresh_and_are_visible_in_a_closed_group():
    out = run_dashboard("""
      latestObservationScan = {keys: new Set([
        observationNameKey('192.168.50.124', 'logs.dradis.netflix.com'),
        observationNameKey('192.168.50.125', 'logs.dradis.netflix.com')])};
      render();
      const first = groupParts(cards()[0], 0);
      const summary = elements['observation-scan-summary'].textContent;
      const closed = !first.isOpen();
      const badge = byClass(first.toggle, 'observation-new-group')[0].textContent;
      const rowCount = byClass(first.body, 'observation-new-name').length;
      render();
      console.log(JSON.stringify({summary, closed, badge, rowCount,
        refreshed: byClass(cards()[0], 'observation-new-name').length,
        other: byClass(cards()[1], 'observation-new-name').length}));
    """, [example_device(), example_device("192.168.50.125")])
    assert out["closed"]
    assert out["badge"] == "1 new to list"
    assert out["rowCount"] == out["refreshed"] == out["other"] == 1
    assert "2 displayed names new to list" in out["summary"]


def test_unavailable_current_list_is_unknown_and_hidden_captured_names_are_not_counted():
    out = run_dashboard("""
      latestObservationScan = {keys: new Set(['192.168.50.124|not-displayed.example'])};
      render();
      const capped = elements['observation-scan-summary'].textContent;
      renderObservations({available: false, devices: []});
      const unavailable = elements['observation-scan-summary'].textContent;
      const snapshot = observationListKeys;
      render();
      console.log(JSON.stringify({capped, unavailable, snapshot,
        restored: elements['observation-scan-summary'].textContent}));
    """, [device("192.168.50.124", [])])
    assert "0 displayed names new to list" in out["capped"]
    assert "comparison unavailable" in out["unavailable"]
    assert "0 displayed" not in out["unavailable"]
    assert out["snapshot"] is None
    assert out["restored"] == out["capped"]


def run_scan(steps):
    from tests.unit.test_dashboard_recurring_scan import run_recurring_scan
    return run_recurring_scan(steps, active=True)


def test_scan_uses_start_snapshot_deduplicates_pairs_and_waits_for_the_list():
    out = run_scan("""
      baseline();
      const scanning = scanObservations();
      await scanObservations();
      observationListKeys.add(observationNameKey(fresh.ipv4_address, fresh.domain));
      pending.shift().resolve({captured_observations: 4, observations: [old, fresh, fresh, anotherDevice]});
      await flush();
      const busyDuringRead = !button.disabled && observationRequestInFlight;
      pending.shift().resolve(list);
      await scanning;
      console.log(JSON.stringify({calls, busyDuringRead, busyAfter: button.disabled,
        keys: Array.from(latestObservationScan.keys).sort(), status: status.textContent}));
    """)
    assert out["calls"] == [["/api/observations/scan", "POST"], ["/api/observations", "GET"]]
    assert out["busyDuringRead"] and not out["busyAfter"]
    assert out["keys"] == ["192.168.50.124|new.example", "192.168.50.125|old.example"]
    assert "2 displayed names new to list" in out["status"]
    assert "Next capture in 30 seconds" in out["status"]


@pytest.mark.parametrize("baseline, captured, expected", [
    ("baseline();", "[old]", []),
    ("observationListKeys = null;", "[fresh]", None),
    ("baseline();", "undefined", None),
])
def test_repeated_names_are_zero_and_missing_comparisons_are_unknown(baseline, captured, expected):
    out = run_scan(baseline + f"""
      const scanning = scanObservations();
      pending.shift().resolve({{captured_observations: 1, observations: {captured}}});
      await flush(); pending.shift().resolve(list); await scanning;
      console.log(JSON.stringify({{keys: latestObservationScan.keys === null ? null :
        Array.from(latestObservationScan.keys), status: status.textContent}}));
    """)
    assert out["keys"] == expected
    assert ("comparison unavailable" in out["status"]) is (expected is None)


def test_failed_scan_preserves_previous_comparison_and_next_success_replaces_it():
    out = run_scan("""
      baseline(); latestObservationScan = {keys: new Set(['previous'])};
      const failed = scanObservations(); pending.shift().reject(new Error('scan failed')); await failed;
      const retained = Array.from(latestObservationScan.keys);
      const scanning = scanObservations(); pending.shift().resolve({captured_observations: 1, observations: [old]});
      await flush(); pending.shift().resolve(list); await scanning;
      console.log(JSON.stringify({retained, replaced: Array.from(latestObservationScan.keys)}));
    """)
    assert out == {"retained": ["previous"], "replaced": []}


def test_failed_followup_read_does_not_report_zero_new_names():
    out = run_scan("""
      baseline(); const scanning = scanObservations();
      pending.shift().resolve({captured_observations: 1, observations: [fresh]});
      await flush(); pending.shift().reject(new Error('list offline')); await scanning;
      console.log(JSON.stringify({snapshot: observationListKeys,
        keys: Array.from(latestObservationScan.keys), status: status.textContent}));
    """)
    assert out["snapshot"] is None
    assert out["keys"] == ["192.168.50.124|new.example"]
    assert "saved results could not be loaded" in out["status"]
    assert "0 displayed" not in out["status"]


@pytest.mark.parametrize("lock_during", ["scan", "list"])
def test_private_lock_invalidates_late_scan_or_list_completion(lock_during):
    steps = "baseline(); const scanning = scanObservations();\n"
    if lock_during == "list":
        steps += "pending.shift().resolve({captured_observations: 1, observations: [fresh]}); await flush();\n"
    steps += "hooks.forEach(hook => hook());\n"
    steps += ("pending.shift().resolve(list);\n" if lock_during == "list" else
              "pending.shift().resolve({captured_observations: 1, observations: [fresh]});\n")
    out = run_scan(steps + """
      await scanning;
      console.log(JSON.stringify({snapshot: observationListKeys, latest: latestObservationScan,
        draws, success: messages.some(m => m[1] === 'success'),
        summary: summary.textContent, markersRemoved: markers.every(n => n.removed)}));
    """)
    assert out == {"snapshot": None, "latest": None, "draws": [], "success": False,
                   "summary": "Saved results refresh automatically.", "markersRemoved": True}


def test_old_read_cannot_overwrite_scan_refresh_and_background_reads_wait():
    out = run_scan("""
      baseline(); const oldRead = loadObservations();
      const scanning = scanObservations();
      await loadObservations();
      const oldReply = pending.shift(), scanReply = pending.shift();
      scanReply.resolve({captured_observations: 2, observations: [fresh, anotherDevice]});
      await flush();
      oldReply.resolve({available: true, devices: []}); await oldRead;
      const beforeFreshRead = draws.length;
      pending.shift().resolve(list); await scanning;
      console.log(JSON.stringify({calls, beforeFreshRead, draws: draws.length,
        status: status.textContent}));
    """)
    assert out["calls"] == [["/api/observations", "GET"], ["/api/observations/scan", "POST"],
                            ["/api/observations", "GET"]]
    assert out["beforeFreshRead"] == 0 and out["draws"] == 1
    assert "2 displayed names new to list" in out["status"]

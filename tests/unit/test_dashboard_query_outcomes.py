"""Pin discovery's query/list semantics before adding resolver-evidence rendering.

These baseline cases deliberately use only the existing API contract. Actual
outcome/coverage fixtures follow the separately reviewed producer contract.
"""

import json
import shutil

import pytest

from tests.unit.test_dashboard_traffic_groups import device, evaluate, item, run_dashboard

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")


@pytest.mark.parametrize("membership", [True, False])
@pytest.mark.parametrize("queries", [1, 19])
def test_queries_and_saved_list_membership_are_separate_from_resolver_evidence(
    membership, queries
):
    result = run_dashboard("""
      const row = byClass(cards()[0], 'observation-domain')[0];
      const badge = byClass(row, 'observation-badge')[0];
      console.log(JSON.stringify({
        queries: byClass(row, 'observation-domain-count')[0].textContent,
        membership: badge.textContent, membershipHidden: badge.hidden,
        queryLabels: byClass(row, 'observation-domain-count').length
      }));
    """, [device("192.168.50.124", [item("sample.example", queries, blocked=membership)])])
    assert result == {
        "queries": f"{queries} " + ("query" if queries == 1 else "queries"),
        "membership": "On global block list",
        "membershipHidden": not membership,
        "queryLabels": 1,
    }


def test_unavailable_read_removes_previous_rows_without_claiming_zero_decisions():
    result = run_dashboard("""
      const before = byClass(elements.observations, 'observation-domain').length;
      renderObservations({available:false, devices, message:'Resolver telemetry unavailable.'});
      console.log(JSON.stringify({before,
        rows:byClass(elements.observations, 'observation-domain').length,
        count:elements['observation-count'].textContent,
        message:elements.observations.children[0].textContent}));
    """, [device("192.168.50.124", [item("sample.example", 19, blocked=True)])])
    assert result == {
        "before": 1,
        "rows": 0,
        "count": "Unavailable",
        "message": "Resolver telemetry unavailable.",
    }


def collection(**changes):
    value = {
        "status": "partial", "epoch": "collector-1", "policy_revision": 7,
        "window_start": 1000, "window_end": 1100, "lower_bound": True,
        "coverage_complete": False, "collector_available": True,
        "last_event_at": 1080, "dropped_events": None, "coverage_reason": "partial",
    }
    value.update(changes)
    return value


def outcomes(**changes):
    value = {"blocked_decisions": 50, "answered_noerror": 90,
             "other_dns_errors": 887766, "last_observed_at": 1080}
    value.update(changes)
    return value


def row_text(evidence, coverage=None):
    return evaluate("dnsOutcomeRowText(" + json.dumps({"dns_outcomes": evidence}) + ","
                    + json.dumps(collection() if coverage is None else coverage) + ")")


def test_observed_aggregates_are_independent_and_dns_errors_are_not_displayed():
    text = row_text(outcomes())
    assert "Blocked decisions observed: 50" in text
    assert "DNS answers observed: 90" in text
    assert "Last observed:" in text
    assert "887766" not in text
    assert "%" not in text and "successful" not in text.lower() and "allowed" not in text.lower()


@pytest.mark.parametrize("evidence", [None, {}, {"blocked_decisions": 1}])
def test_historical_or_incomplete_outcomes_are_unknown_not_zero(evidence):
    text = row_text(evidence)
    assert "Unknown" in text
    assert "observed: 0" not in text


@pytest.mark.parametrize("field", ["blocked_decisions", "answered_noerror", "other_dns_errors"])
@pytest.mark.parametrize("bad", [-1, True, "4", 9007199254740992, 1.5, None])
def test_invalid_counts_do_not_create_evidence(field, bad):
    assert "Unknown" in row_text(outcomes(**{field: bad}))


@pytest.mark.parametrize("when", [None, True, "1080", -1, 999, 1101, 8640000000001])
def test_invalid_or_out_of_window_event_times_are_unknown(when):
    assert "Unknown" in row_text(outcomes(last_observed_at=when))


@pytest.mark.parametrize("coverage", [
    {}, collection(status="unavailable", window_start=None, window_end=None),
    collection(status="complete"), collection(lower_bound=False),
    collection(coverage_complete=True), collection(window_start=1101),
    collection(window_start=None), collection(window_end="1100"),
])
def test_missing_unavailable_or_unrecognised_coverage_cannot_certify_counts(coverage):
    assert "Unknown" in row_text(outcomes(), coverage)
    text = evaluate("dnsOutcomeCollectionText(" + json.dumps(coverage) + ")")
    assert "unknown" in text and "telemetry unavailable" in text
    assert "observed: 0" not in text


def test_zero_events_are_shown_only_when_explicitly_recorded_in_a_valid_window():
    text = row_text(outcomes(blocked_decisions=0, answered_noerror=0, other_dns_errors=0))
    assert "Blocked decisions observed: 0" in text
    assert "DNS answers observed: 0" in text


def test_coverage_explains_window_policy_collector_and_unknown_sender_loss():
    text = evaluate("dnsOutcomeCollectionText(" + json.dumps(collection(dropped_events=0)) + ")")
    for phrase in ("partial coverage", "lower-bound", "Window:", "Policy revision: 7",
                   "Collector available", "Known receiver drops: 0", "sender loss unknown"):
        assert phrase in text
    assert "complete coverage" not in text
    unavailable = evaluate("dnsOutcomeCollectionText(" + json.dumps(collection(
        collector_available=False, policy_revision=None, dropped_events=None)) + ")")
    assert "Collector unavailable; retained events only" in unavailable
    assert "Policy revision: unknown" in unavailable and "Event loss unknown" in unavailable


def test_unknown_coverage_reason_never_becomes_untrusted_markup_or_complete_coverage():
    text = evaluate("dnsOutcomeCollectionText(" + json.dumps(collection(
        coverage_reason="<img src=x onerror=alert(1)>")) + ")")
    assert "<img" not in text and "partial coverage" in text


def test_epoch_replacement_does_not_mix_counts_or_change_query_membership():
    domain = item("sample.example", 19, blocked=True)
    domain["dns_outcomes"] = outcomes()
    payload = {"available": True, "devices": [device("192.168.50.124", [domain])],
               "dns_outcome_collection": collection()}
    result = run_dashboard("""
      const payload = """ + json.dumps(payload) + """;
      renderObservations(payload);
      const previous = byClass(cards()[0], 'observation-dns-outcomes')[0];
      payload.dns_outcome_collection.epoch = 'collector-2';
      payload.dns_outcome_collection.policy_revision = 8;
      payload.devices[0].domains[0].dns_outcomes = null;
      renderObservations(payload);
      const row = byClass(cards()[0], 'observation-domain')[0];
      console.log(JSON.stringify({
        text:byClass(row, 'observation-dns-outcomes')[0].textContent,
        replaced:byClass(row, 'observation-dns-outcomes')[0] !== previous,
        count:byClass(row, 'observation-domain-count')[0].textContent,
        membership:byClass(row, 'observation-badge')[0].textContent,
        summary:elements['observation-scan-summary'].textContent}));
    """, payload["devices"])
    assert result["replaced"] and "Unknown/no outcome recorded" in result["text"]
    assert result["count"] == "19 queries"
    assert result["membership"] == "On global block list"
    assert "Policy revision: 8" in result["summary"] and "Policy revision: 7" not in result["summary"]


def test_private_clear_hook_removes_outcome_nodes_and_clears_coverage_summary():
    from tests.dashboard_script import SCRIPT
    from tests.unit.test_dashboard_traffic_groups import run_node

    start = SCRIPT.index("    let observationListKeys = null;")
    end = SCRIPT.index("    let randomWebsiteAnalyticsRequestVersion", start)
    result = json.loads(run_node("""
      const privateDataLockHooks=[];
      const summary={textContent:'Private window and policy revision 7'};
      function element(){return summary;}
      let removed=false;
      const document={querySelectorAll(selector){
        return selector.includes('.observation-dns-outcomes')
          ? [{remove(){removed=true;}}] : [];
      }};
    """ + SCRIPT[start:end] + """
      privateDataLockHooks.forEach(hook=>hook());
      console.log(JSON.stringify({removed,summary:summary.textContent}));
    """))
    assert result == {"removed": True, "summary": "Saved results refresh automatically."}


@pytest.mark.parametrize("failed", [False, True])
def test_private_clear_suppresses_late_outcome_read_and_error(failed):
    from tests.unit.test_dashboard_discovery_new_names import run_scan

    payload = {"available": True, "devices": [], "dns_outcome_collection": collection()}
    finish = ("pending.shift().reject(new Error('private resolver error'));" if failed else
              "pending.shift().resolve(" + json.dumps(payload) + ");")
    result = run_scan("""
      const read=loadObservations();
      hooks.forEach(hook=>hook());
    """ + finish + """
      await read;
      console.log(JSON.stringify({draws,messages,summary:summary.textContent}));
    """)
    assert result == {"draws": [], "messages": [],
                      "summary": "Saved results refresh automatically."}

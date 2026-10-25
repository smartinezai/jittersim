"""Pin discovery's query/list semantics before adding resolver-evidence rendering.

These baseline cases deliberately use only the existing API contract. Actual
outcome/coverage fixtures follow the separately reviewed producer contract.
"""

import shutil

import pytest

from tests.unit.test_dashboard_traffic_groups import device, item, run_dashboard

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

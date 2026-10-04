import json
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperService, _build_parser
from jittersim.networking.topology import Topology
from jittersim.router_audit import RouterAudit


def service(audit=None):
    factory = Mock(side_effect=AssertionError("Router must not be contacted"))
    applier = NetworkApplier(executor=FakeExecutor(), topology=Topology("eth0", "wlan0"))
    return HelperService(applier, router_factory=factory, router_audit=audit), factory


def test_default_refuses_router_writes_even_with_request_opt_in():
    helper, factory = service()
    for enabled in (True, False):
        result = helper.handle_payload({"action": "router-wifi", "enabled": enabled,
                                       "admin_password": "private", "allow_router_wifi_changes": True})
        assert result["reason"] == "router-control-disabled"
    factory.assert_not_called()


def test_concurrent_requests_cannot_contact_router():
    helper, factory = service()
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: helper.handle_payload(
            {"action": "router-wifi", "enabled": False, "admin_password": "private"}), range(24)))
    assert all(r.get("reason") in ("router-control-disabled", "helper-busy") for r in results)
    factory.assert_not_called()


def test_diagnostic_failure_does_not_break_refusal():
    audit = Mock()
    audit.record.side_effect = OSError("private exception")
    helper, factory = service(audit)
    assert helper.handle_payload({"action": "router-wifi"})["reason"] == "router-control-disabled"
    factory.assert_not_called()


def test_audit_pairs_requests_and_excludes_private_payload(tmp_path):
    audit = RouterAudit.open(tmp_path / "audit")
    assert audit is not None
    helper, _ = service(audit)
    helper.handle_payload({"action": "router-wifi", "enabled": False, "admin_password": "private-secret"})
    data = (tmp_path / "audit/operations.jsonl").read_text()
    records = [json.loads(line) for line in data.splitlines()]
    assert [r["outcome"] for r in records] == ["start", "refused"]
    assert records[0]["operation"] == records[1]["operation"] == 1
    assert "private-secret" not in data
    assert stat.S_IMODE((tmp_path / "audit/operations.jsonl").stat().st_mode) == 0o600
    audit.handler.close()


def test_audit_retention_is_bounded(tmp_path):
    audit = RouterAudit.open(tmp_path / "audit")
    for number in range(1500):
        audit.record("router-wifi", False, "refused", 0, number)
    files = list((tmp_path / "audit").iterdir())
    assert len(files) == 2
    assert sum(p.stat().st_size for p in files) <= 131072
    audit.handler.close()


def test_audit_refuses_symlink_directory(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    (tmp_path / "audit").symlink_to(target)
    assert RouterAudit.open(tmp_path / "audit") is None


def test_startup_opt_in_is_explicit():
    args = ["--socket", "/tmp/helper.sock", "--uplink", "eth0", "--test-interface", "wlan0"]
    assert _build_parser().parse_args(args).allow_router_wifi_changes is False
    assert _build_parser().parse_args(args + ["--allow-router-wifi-changes"]).allow_router_wifi_changes is True

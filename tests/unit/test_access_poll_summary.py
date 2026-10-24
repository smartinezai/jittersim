"""Access polls omit domain lists while full reads and status decisions stay complete."""

import copy
import http.client
import json
import socket
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.models import AccessOptions, Device, MacPolicy, Profile, SiteBlock, WebsitePolicy
from jittersim.config.storage import ConfigRepository
from jittersim.engine import access as access_engine
from jittersim.engine.access import access_from_payload, build_access_plan
from jittersim.networking.helper import HelperClientError


@pytest.fixture
def server(monkeypatch):
    # Every address lookup is synthetic, including import validation.
    original_lookup = socket.getaddrinfo

    def without_dns(host, *args, **kwargs):
        if host == "127.0.0.1":
            return original_lookup(host, *args, **kwargs)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", without_dns)
    repository = ConfigRepository(":memory:")
    repository.save_profile(Profile("plain", "Plain"))
    for index in range(15):
        repository.save_device(Device(
            "device-%d" % index, "Device %d" % index, "192.168.50.%d" % (index + 10),
            "plain", "02:00:00:00:00:%02x" % index,
        ))
    repository.save_access_policies(
        MacPolicy("denylist", (), ("02:00:00:00:00:00",)),
        WebsitePolicy(allowed_domains=("allowed.example.test",), blocked_domains=tuple(
            "ads%05d.tracker.example.test" % index for index in range(2000))),
        options=AccessOptions(dns_failure_percent=25),
        blocks=(SiteBlock("video", "Video", ("video.example.test",), device_id="device-1"),
                SiteBlock("disabled", "Disabled", ("off.example.test",), enabled=False)),
    )
    instance = ControlServer(("127.0.0.1", 0), repository, admin_token="synthetic-access-test")
    policy = build_access_plan(repository.access_settings()).payload()
    instance.helper_client = SimpleNamespace(access_status=Mock(return_value={
        "available": True, "wifi_admission_available": True, "enabled": True,
        "applied_digest": "synthetic-policy-digest", "error": None, "policy": policy,
    }))
    try:
        yield instance
    finally:
        instance.server_close()
        repository.close()


def read(server, query="", path="/api/access"):
    status, _, body = server.response_for(path, query)
    assert status == 200
    return json.loads(body)


def test_warm_full_and_summary_reads_still_parse_changed_helper_policies(server):
    helper = server.helper_client.access_status
    with patch.object(access_engine, "build_access_plan", wraps=build_access_plan) as build, \
            patch.object(access_engine, "access_from_payload", wraps=access_from_payload) as parse:
        assert read(server)["applied"] is True
        assert build.call_count == 1
        parse.reset_mock()
        helper.reset_mock()
        assert read(server)["applied"] is True
        assert read(server, "summary=1")["applied"] is True
        policy = helper.return_value["policy"]
        policy["clients"][0]["address"] = "192.168.50.40"
        changed_address = read(server, "summary=1")
        assert changed_address["applied"] is True
        assert changed_address["policy"]["clients"][0]["address"] == "192.168.50.40"
        domains = policy["websites"]["blocked_domains"]
        policy["websites"]["blocked_domains"] = ("changed.example.test", *domains[1:])
        assert read(server, "summary=1")["applied"] is False
        assert build.call_count == 1
        assert helper.call_count == parse.call_count == 4
        assert all(call.args[0] is policy for call in parse.call_args_list)


def test_warm_reads_do_not_reuse_a_helper_failure_or_skip_invalid_policy_checks(server):
    helper = server.helper_client.access_status
    original = copy.deepcopy(helper.return_value["policy"])
    with patch.object(access_engine, "build_access_plan", wraps=build_access_plan) as build:
        read(server, "summary=1")
        helper.return_value["policy"] = {"version": True}
        with patch.object(access_engine, "access_from_payload", wraps=access_from_payload) as parse:
            assert server.response_for("/api/access")[0] == 503
            assert server.response_for("/api/access", "summary=1")[0] == 503
            assert parse.call_count == 2
            helper.side_effect = HelperClientError("synthetic failure after a warm read")
            assert server.response_for("/api/access", "summary=1")[0] == 503
            assert parse.call_count == 2
            helper.side_effect = None
            helper.return_value["policy"] = original
            assert read(server, "summary=1")["applied"] is True
            assert parse.call_count == 3
        assert build.call_count == 1


def test_warm_apply_reuses_rules_but_quick_action_and_disable_use_new_saved_rules(server):
    helper = server.helper_client
    helper.apply_access = Mock(return_value={"ok": True})
    helper.control_revision = Mock(return_value="synthetic-control-revision")
    with patch.object(access_engine, "build_access_plan", wraps=build_access_plan) as build:
        read(server, "summary=1")
        initial = server.repository.access_plan()
        version = server.repository.get_runtime_state().configuration_version
        status, _, body = server.write_response_for("/api/access/apply", {"version": version})
        assert status == 200, body
        assert helper.apply_access.call_args.args[0] is initial
        assert helper.apply_access.call_args.kwargs == {"control_revision": "synthetic-control-revision"}
        assert build.call_count == 1
        blocked = "02:00:00:00:02:00"
        status, _, body = server.write_response_for("/api/access/device", {
            "version": version, "mac_address": blocked, "blocked": True})
        assert status == 200, body
        device_plan = helper.apply_access.call_args.args[0]
        assert device_plan is not initial
        assert blocked in device_plan.mac.denylist
        assert device_plan is server.repository.access_plan()
        status, _, body = server.write_response_for("/api/access/disable", {
            "version": server.repository.get_runtime_state().configuration_version,
            "confirmed": True})
        assert status == 200, body
        disabled = helper.apply_access.call_args.args[0]
        assert disabled is not device_plan and disabled.options.enabled is False
        assert disabled is server.repository.access_plan()
        assert build.call_count == 3
        helper.apply_access.reset_mock()
        assert server.write_response_for("/api/access/apply", {"version": version})[0] == 409
        refusal = (409, "application/json", b'{"error":"synthetic control refusal"}\n')
        with patch.object(server, "_apply_refused", return_value=refusal):
            assert server.write_response_for("/api/access/apply", {
                "version": server.repository.get_runtime_state().configuration_version}) == refusal
        helper.apply_access.assert_not_called()
        assert build.call_count == 3


def test_summary_preserves_status_and_device_rules_without_any_domain_names(server):
    full = read(server)
    summary = read(server, "summary=1")
    assert {k: v for k, v in summary.items() if k not in ("policy", "saved_policy")} == {
        k: v for k, v in full.items() if k not in ("policy", "saved_policy")}
    for name in ("policy", "saved_policy"):
        complete, shortened = full[name], summary[name]
        assert {k: v for k, v in shortened.items() if k not in ("websites", "blocks")} == {
            k: v for k, v in complete.items() if k not in ("websites", "blocks")}
        assert shortened["websites"] == {
            "mode": "unrestricted", "allowed_domain_count": 1, "blocked_domain_count": 2000}
        assert shortened["blocks"] == [
            {**{key: value for key, value in rule.items() if key != "domains"},
             "domain_count": len(rule["domains"])} for rule in complete["blocks"]]
    assert ".example.test" not in json.dumps(summary)
    assert summary["applied"] is True


@pytest.mark.parametrize("policy", [
    {"version": 1},
    {"version": 1, "websites": {}},
    {"version": 1, "websites": {"mode": "allow_only"}},
    {"version": 1, "websites": {"blocked_domains": ["legacy.example.test"]}},
    {"version": 1, "websites": {"allowed_domains": ["allowed.example.test"]}},
    {"version": 1, "options": {"enabled": False}},
    {"version": 1, "mac": {"mode": "denylist"}},
    {"version": 1, "blocks": [
        {"id": "legacy", "name": "Legacy", "domains": ["legacy.example.test"]}]},
    {"version": 1, "clients": [
        {"id": "legacy", "address": "192.168.50.22", "mac": None}]},
])
def test_valid_partial_helper_policies_get_complete_summary_defaults_without_changing_full_reads(
    server, policy,
):
    helper = server.helper_client.access_status.return_value
    helper["policy"] = copy.deepcopy(policy)
    before = copy.deepcopy(helper)
    full = read(server)
    canonical = json.loads(json.dumps(access_from_payload(policy).payload()))
    summary = read(server, "summary=1")
    assert summary["applied"] == full["applied"]
    assert summary["normal_access"] == full["normal_access"]
    assert summary["applied_digest"] == full["applied_digest"]
    assert summary["available"] == full["available"]
    for field in ("version", "options", "mac", "clients"):
        assert summary["policy"][field] == canonical[field]
    assert summary["policy"]["websites"] == {
        "mode": canonical["websites"]["mode"],
        "allowed_domain_count": len(canonical["websites"]["allowed_domains"]),
        "blocked_domain_count": len(canonical["websites"]["blocked_domains"]),
    }
    assert summary["policy"]["blocks"] == [
        {**{key: value for key, value in rule.items() if key != "domains"},
         "domain_count": len(rule["domains"])} for rule in canonical["blocks"]]
    assert helper == before
    assert read(server)["policy"] == policy
    assert read(server) == full


@pytest.mark.parametrize("policy", [
    {}, {"version": 2}, {"version": True}, {"version": 1, "websites": None},
    {"version": 1, "websites": {"blocked_domains": ["https://invalid.test"]}},
    {"version": 1, "blocks": [{}]},
])
def test_summary_keeps_the_full_read_refusal_for_malformed_helper_policies(server, policy):
    server.helper_client.access_status.return_value["policy"] = policy
    full = server.response_for("/api/access")
    assert full[0] == 503
    assert server.response_for("/api/access", "summary=1") == full


def test_summary_does_not_mutate_helper_policy_or_later_full_editor_reads(server):
    before = copy.deepcopy(server.helper_client.access_status.return_value)
    full = read(server)
    read(server, "summary=1")
    assert server.helper_client.access_status.return_value == before
    assert read(server) == full
    editor = read(server, path="/api/policies")
    assert editor["websites"] == full["saved_policy"]["websites"]
    assert editor["site_blocks"] == full["saved_policy"]["blocks"]


@pytest.mark.parametrize("section", ["websites", "site-rule"])
def test_summary_still_detects_a_domain_only_change_with_the_same_count(server, section):
    read(server, "summary=1")
    if section == "websites":
        original = server.repository.get_website_policy()
        server.repository.save_website_policy(WebsitePolicy(
            allowed_domains=original.allowed_domains,
            blocked_domains=("new.example.test", *original.blocked_domains[1:]),
        ))
    else:
        rules = server.repository.list_site_blocks()
        server.repository.save_access_policies(None, None, blocks=(
            SiteBlock("video", "Video", ("changed.example.test",), device_id="device-1"),
            *[rule for rule in rules if rule.id != "video"],
        ))
    summary = read(server, "summary=1")
    assert summary["applied"] is False
    # Names differ, but the shortened policies are otherwise identical. The full
    # comparison must happen before shortening, or it would report them applied.
    assert summary["policy"] == summary["saved_policy"]


@pytest.mark.parametrize("mode", ["unrestricted", "allow_only"])
def test_summary_counts_empty_domains_without_changing_website_mode(server, mode):
    server.repository.save_website_policy(WebsitePolicy(mode=mode, allowed_domains=("a.test",)))
    assert read(server, "summary=1")["saved_policy"]["websites"] == {
        "mode": mode, "allowed_domain_count": 1, "blocked_domain_count": 0}


@pytest.mark.parametrize("state", ["not-applied", "configuration-only", "normal-access", "error"])
def test_summary_keeps_unavailable_reset_and_error_truth(server, state):
    helper = server.helper_client.access_status.return_value
    if state == "not-applied":
        helper.update(policy=None, enabled=False)
    elif state == "configuration-only":
        server.helper_client = None
    elif state == "normal-access":
        helper["policy"]["options"]["enabled"] = False
        helper["enabled"] = False
        server.repository.update_runtime_state(
            observed_state="reset", last_action="reset", last_result="success", last_error=None)
    else:
        helper["error"] = "Synthetic DNS health failure"
    full = read(server)
    summary = read(server, "summary=1")
    for field in ("available", "wifi_admission_available", "applied", "version", "normal_access"):
        assert summary[field] == full[field]
    assert summary.get("error") == full.get("error")
    if full.get("policy") is None:
        assert summary.get("policy") is None
    assert summary["normal_access"] is (state == "normal-access")
    if state == "error":
        assert summary["applied"] is False


def test_helper_read_failure_keeps_the_existing_error_and_status(server):
    server.helper_client.access_status.side_effect = HelperClientError("synthetic failure")
    assert server.response_for("/api/access", "summary=1") == server.response_for("/api/access")
    assert server.response_for("/api/access", "summary=1")[0] == 503


def test_only_the_opt_in_query_changes_the_default_response(server):
    full = read(server)
    for query in ("", "summary=0", "summary=false", "other=1", "summary=1&summary=0"):
        assert read(server, query) == full
    assert read(server, "other=1&summary=1")["policy"]["websites"]["blocked_domain_count"] == 2000


def test_save_and_import_keep_full_editor_names_after_summary_reads(server):
    read(server, "summary=1")
    server.helper_client = None
    editor = read(server, path="/api/policies")
    names = editor["websites"]["blocked_domains"]
    names[0] = "changed.example.test"
    status, _, body = server.write_response_for("/api/policies", editor)
    assert status == 200, body
    assert read(server, path="/api/policies")["websites"]["blocked_domains"] == names
    exported = read(server, path="/api/export")
    exported["policies"]["websites"]["blocked_domains"][-1] = "imported.example.test"
    read(server, "summary=1")
    status, _, body = server.write_response_for("/api/import", exported)
    assert status == 200, body
    assert read(server, path="/api/policies")["websites"]["blocked_domains"] == (
        exported["policies"]["websites"]["blocked_domains"])


def test_2000_name_summary_is_small_while_full_read_keeps_all_names(server):
    full = server.response_for("/api/access")[2]
    summary = server.response_for("/api/access", "summary=1")[2]
    assert len(json.loads(full)["policy"]["websites"]["blocked_domains"]) == 2000
    assert len(full) > 130_000
    assert len(summary) < 10_000
    assert len(summary) < len(full) / 10


def test_http_summary_uses_the_same_auth_and_device_checks(server):
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    checks = Mock(return_value={"allowed": False})
    server.dashboard_access = checks

    def get(token=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            headers = {"Authorization": "Bearer " + token} if token else {}
            connection.request("GET", "/api/access?summary=1", headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        assert get()[0] == 401
        assert get("invalid-synthetic-token")[0] == 401
        checks.assert_not_called()
        assert get(server.admin_token)[0] == 403
        checks.assert_called_once_with("127.0.0.1")
        checks.return_value = {"allowed": True}
        status, summary = get(server.admin_token)
        assert status == 200
        assert summary["saved_policy"]["websites"]["blocked_domain_count"] == 2000
        assert "blocked_domains" not in summary["saved_policy"]["websites"]
        checks.side_effect = HelperClientError("synthetic device verification failure")
        assert get(server.admin_token)[0] == 503
        server.admin_token = None
        assert get("synthetic-access-test")[0] == 503
    finally:
        server.shutdown()
        worker.join(timeout=3)

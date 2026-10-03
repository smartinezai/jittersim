"""Deferred old privileged writes cannot clear Panic before dashboard cleanup."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim.engine.access import AccessPlan
from jittersim.networking.helper import HelperClient, HelperClientError
from tests.unit import test_dashboard_concurrency as concurrency
from tests.unit.test_helper_access import apply_payload
from tests.unit.test_panic_restart_latch import helper


@pytest.mark.parametrize("action", ["apply", "apply-experiment", "access-apply"])
def test_old_root_intent_is_rejected_after_panic_before_any_network_mutation(action):
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        with helper(directory) as (service, client):
            old_revision = client.gateway_status().get("control_revision", "0" * 32)
            if action == "access-apply":
                payload = {"action": action, "policy": AccessPlan().payload()}
            else:
                payload = {**apply_payload(AccessPlan()), "action": action, "explicit_apply": True}
            payload["expected_control_revision"] = old_revision
            assert client.normal_access()["ok"] is True
            before = service._plan_count
            with pytest.raises(HelperClientError) as rejected:
                client._request(payload)
            assert rejected.value.reason == "control-changed"
            assert service._plan_count == before
            assert service._panic_stop_latched is True
            assert not service._access_manager.applied.options.enabled
            payload["expected_control_revision"] = client.gateway_status()["control_revision"]
            assert client._request(payload)["ok"] is True


class LateAccessConsumerTests(unittest.TestCase):
    setUp = concurrency.DashboardConcurrencyTests.setUp
    _stop = concurrency.DashboardConcurrencyTests._stop
    request = concurrency.DashboardConcurrencyTests.request

    def test_old_household_helper_completion_is_undone_and_never_claims_success(self):
        entered, release = threading.Event(), threading.Event()
        actual = {"enabled": True}
        original = self.helper.normal_access
        result = {}

        def apply(plan):
            entered.set()
            assert release.wait(5)
            actual["enabled"] = plan.options.enabled
            return {"ok": True}

        def panic():
            actual["enabled"] = False
            return original()

        self.helper.apply_access = apply
        version = self.repository.get_runtime_state().configuration_version
        with patch.object(self.helper, "normal_access", side_effect=panic):
            worker = threading.Thread(
                target=lambda: result.update(
                    response=self.request("POST", "/api/access/apply", {"version": version})
                )
            )
            self.threads.append(worker)
            worker.start()
            assert entered.wait(2)
            assert self.request("POST", "/api/normal-access", {})[0] == 200
            release.set()
            worker.join(2)
        assert result["response"][0] == 409
        assert not actual["enabled"]
        assert not self.repository.access_settings().access_options.enabled
        assert self.repository.get_runtime_state().last_action == "reset"
        assert not self.scheduler.is_active

    def test_global_block_list_completion_after_panic_is_undone(self):
        entered, release = threading.Event(), threading.Event()
        actual = {"enabled": True}
        original = self.helper.normal_access
        result = {}

        def apply(plan):
            entered.set()
            assert release.wait(5)
            actual["enabled"] = plan.options.enabled
            return {"ok": True}

        def panic():
            actual["enabled"] = False
            return original()

        self.helper.apply_access = apply
        with patch.object(self.helper, "normal_access", side_effect=panic):
            worker = threading.Thread(
                target=lambda: result.update(response=self.server._apply_household_block_list())
            )
            self.threads.append(worker)
            worker.start()
            assert entered.wait(2)
            assert self.request("POST", "/api/normal-access", {})[0] == 200
            release.set()
            worker.join(2)
        assert result["response"][0] == 409
        assert not actual["enabled"]
        assert not self.repository.access_settings().access_options.enabled
        assert self.repository.get_runtime_state().last_action == "reset"

    def test_delayed_old_access_settings_cannot_overwrite_panic_saved_disable(self):
        entered, release = threading.Event(), threading.Event()
        original = self.repository.access_settings
        lock = threading.Lock()
        calls = 0
        applied = []
        result = {}

        def read():
            nonlocal calls
            with lock:
                calls += 1
                first = calls == 1
            settings = original()
            if first:
                entered.set()
                assert release.wait(5)
            return settings

        self.helper.apply_access = lambda plan: applied.append(plan)
        version = self.repository.get_runtime_state().configuration_version
        with patch.object(self.repository, "access_settings", side_effect=read):
            worker = threading.Thread(
                target=lambda: result.update(
                    response=self.request(
                        "POST",
                        "/api/access/device",
                        {"version": version, "mac_address": "02:00:00:00:00:01", "blocked": True},
                    )
                )
            )
            self.threads.append(worker)
            worker.start()
            assert entered.wait(2)
            assert self.request("POST", "/api/normal-access", {})[0] == 200
            release.set()
            worker.join(2)
        assert result["response"][0] == 409
        assert not applied
        assert not original().access_options.enabled
        assert self.repository.get_runtime_state().last_action == "reset"


@pytest.mark.parametrize("action", ["apply", "apply-experiment", "access-apply"])
def test_serialized_old_client_write_is_rejected_without_dashboard_undo(action, monkeypatch):
    import json

    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        with helper(Path(temporary)) as (service, client):
            entered, release = threading.Event(), threading.Event()
            original = type(client)._send
            captured, errors = [], []

            def delayed(current_client, payload, timeout_seconds=None):
                if payload["action"] == action:
                    immutable = json.loads(json.dumps(payload))
                    captured.append(immutable)
                    entered.set()
                    assert release.wait(5)
                    payload = immutable
                return original(current_client, payload, timeout_seconds=timeout_seconds)

            def run():
                try:
                    if action == "access-apply":
                        client.apply_access(AccessPlan())
                    else:
                        from jittersim.engine.codec import plan_from_payload

                        plan = plan_from_payload(apply_payload(AccessPlan())["plan"])
                        if action == "apply":
                            client.apply(plan=plan, explicit_apply=True)
                        else:
                            client.apply_experiment(plan=plan)
                except HelperClientError as error:
                    errors.append(error)

            monkeypatch.setattr(type(client), "_send", delayed)
            worker = threading.Thread(target=run)
            worker.start()
            try:
                assert entered.wait(2)
                assert client.normal_access()["ok"] is True
                release.set()
                worker.join(3)
                assert not worker.is_alive()
                assert len(errors) == 1 and errors[0].reason == "control-changed"
                assert len(captured) == 1, "stale intent was replayed or refreshed"
                assert not service._access_manager.applied.options.enabled
                assert service._plan_count == 0
            finally:
                release.set()
                worker.join(3)


def test_old_serialized_revision_is_rejected_after_real_helper_reconstruction():
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        with helper(directory) as (_, client):
            old = client.control_revision()
            assert client.normal_access()["ok"] is True
        with helper(directory) as (service, client):
            assert client.control_revision() != old
            payload = {
                **apply_payload(AccessPlan()),
                "explicit_apply": True,
                "expected_control_revision": old,
            }
            with pytest.raises(HelperClientError) as error:
                client._request(payload)
            assert error.value.reason == "control-changed"
            assert service._plan_count == 0
            assert not service._access_manager.applied.options.enabled
            payload["expected_control_revision"] = client.control_revision()
            assert client._request(payload)["ok"] is True


@pytest.mark.parametrize("bad", [None, "", "f" * 31, "f" * 33, True, 0, "g" * 32, {"value": "x"}])
def test_malformed_revision_and_missing_post_panic_revision_never_mutate(bad):
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        with helper(Path(temporary)) as (service, client):
            client.normal_access()
            payload = {
                **apply_payload(AccessPlan()),
                "explicit_apply": True,
                "expected_control_revision": bad,
            }
            with pytest.raises(HelperClientError) as error:
                client._request(payload)
            assert error.value.reason == "control-changed"
            payload.pop("expected_control_revision")
            with pytest.raises(HelperClientError) as error:
                client._request(payload)
            assert error.value.reason == "control-changed"
            assert service._plan_count == 0
            assert not service._access_manager.applied.options.enabled


def test_cli_captures_root_intent_before_compiling_saved_access(capsys, monkeypatch):
    from types import SimpleNamespace

    from jittersim import cli
    from jittersim.config.storage import ConfigRepository

    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        database = directory / "settings.sqlite"
        with ConfigRepository(database):
            pass
        with helper(directory) as (service, client):
            original = cli.build_access_plan

            def overtaken(settings):
                client.normal_access()
                return original(settings)

            monkeypatch.setattr(cli, "build_access_plan", overtaken)
            arguments = SimpleNamespace(
                database=database, helper_socket=directory / "helper.sock", timeout_seconds=2
            )
            assert cli._apply_access(arguments) == 1
            assert "Network control changed" in capsys.readouterr().err
            assert service._plan_count == 0
            assert not service._access_manager.applied.options.enabled


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"control_revision": "a" * 32},
        {"control_revision": True, "recovery_required": False},
        {"control_revision": "G" * 32, "recovery_required": False},
        {"control_revision": "a" * 32, "recovery_required": "false"},
        {"control_revision": "a" * 32, "recovery_required": True},
    ],
)
def test_missing_malformed_or_unconfirmed_metadata_refuses_before_access_write(metadata):
    client = HelperClient(socket_path=Path("/unused"))
    calls = []

    def request(payload, **kwargs):
        calls.append(payload["action"])
        return metadata

    with patch.object(client, "_request", side_effect=request):
        with pytest.raises(HelperClientError):
            client.apply_access(AccessPlan())
    assert calls == ["control-revision"]


def test_older_helper_metadata_endpoint_is_reported_before_access_write():
    client = HelperClient(socket_path=Path("/unused"))
    with patch.object(
        client, "_request", side_effect=HelperClientError("unknown action")
    ) as request:
        with pytest.raises(HelperClientError, match="out of date"):
            client.apply_access(AccessPlan())
    assert request.call_count == 1


def test_metadata_read_during_real_panic_cleanup_is_prompt_and_refuses_new_write(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        with helper(Path(temporary)) as (service, client):
            entered, release = threading.Event(), threading.Event()
            original = service._handle_reset
            answers = []

            def delayed():
                entered.set()
                assert release.wait(5)
                return original()

            monkeypatch.setattr(service, "_handle_reset", delayed)
            worker = threading.Thread(target=lambda: answers.append(client.normal_access()))
            worker.start()
            try:
                assert entered.wait(2)
                with pytest.raises(HelperClientError, match="recovery is not confirmed"):
                    client._with_timeout(0.5).apply_access(AccessPlan())
                assert service._plan_count == 0
                release.set()
                worker.join(3)
                assert not worker.is_alive()
                assert answers == [
                    {
                        "ok": True,
                        "action": "normal-access",
                        "experiments_stopped": True,
                        "access_disabled": True,
                    }
                ]
                assert len(client.control_revision()) == 32
            finally:
                release.set()
                worker.join(3)


@pytest.mark.parametrize(
    "field,value",
    [
        ("control_revision", True),
        ("control_revision", "z" * 32),
        ("control_revision_required", "true"),
    ],
)
def test_gateway_status_rejects_malformed_optional_control_metadata(field, value):
    client = HelperClient(socket_path=Path("/unused"))
    status = {
        "gateway_state": "unknown",
        "mode_enabled": False,
        "outage_observed": False,
        "outage_stop_latched": False,
        "reset_pending": False,
        "recovery_pending": False,
        "state_error": False,
        field: value,
    }
    with patch.object(client, "_request", return_value=status):
        with pytest.raises(HelperClientError):
            client.gateway_status()


def test_fresh_start_keeps_legacy_write_fence_required_after_helper_reconstruction():
    import json

    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        with helper(directory) as (_, client):
            client.normal_access()
            payload = {
                **apply_payload(AccessPlan()),
                "explicit_apply": True,
                "expected_control_revision": client.control_revision(),
            }
            assert client._request(payload)["ok"] is True
            state = json.loads((directory / "gateway.json").read_text())
            assert state["panic_stop_latched"] is False
            assert state["control_revision_required"] is True
        with helper(directory) as (service, client):
            with pytest.raises(HelperClientError) as error:
                client._request({**apply_payload(AccessPlan()), "explicit_apply": True})
            assert error.value.reason == "control-changed"
            assert service._plan_count == 0

"""Persisted helper Panic state prevents restoring an obsolete database intent."""

import json
import os
import stat
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.storage import ConfigRepository, StorageError
from jittersim.engine.access import AccessPlan
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking.access import AccessManager
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import (
    HelperClient,
    HelperService,
    UnixHelperServer,
    restore_access_at_start,
)
from jittersim.networking.topology import Topology
from tests.access_support import AccessExecutor
from tests.unit.test_helper_access import apply_payload


@contextmanager
def helper(directory, *, panic_guard=None):
    topology = Topology("eth0", "wlan0")
    manager = AccessManager(
        executor=AccessExecutor(directory),
        topology=topology,
        directory=directory,
        runtime_path=directory / "access-runtime.json",
    )
    service = HelperService(
        NetworkApplier(executor=FakeExecutor(), topology=topology),
        active_plan_path=directory / "active-plan.json",
        access_manager=manager,
        gateway_state_path=directory / "gateway.json",
        panic_guard=panic_guard,
    )
    assert restore_access_at_start(manager, panic_stopped=service._panic_stop_latched)
    server = UnixHelperServer(directory / "helper.sock", service)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield service, HelperClient(socket_path=directory / "helper.sock")
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
        assert not thread.is_alive()


def test_complete_helper_and_dashboard_restart_keeps_old_apply_stopped():
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        database = directory / "settings.sqlite"
        with helper(directory) as (_, client), ConfigRepository(database) as repository:
            repository.update_runtime_state(
                observed_state="applied",
                last_action="apply",
                last_result="success",
                last_error=None,
            )
            scheduler = RuntimeScheduler(database_path=database, helper_client=client)
            server = ControlServer(
                ("127.0.0.1", 0), repository, helper_client=client, scheduler=scheduler
            )
            try:
                with (
                    patch.object(
                        repository, "save_access_policies", side_effect=StorageError("full")
                    ),
                    patch.object(
                        repository, "update_runtime_state", side_effect=StorageError("full")
                    ),
                ):
                    assert server.write_response_for("/api/normal-access", {})[0] == 503
                assert repository.get_runtime_state().last_action == "apply"
            finally:
                server.server_close()
                scheduler.stop()
        with helper(directory) as (service, client), ConfigRepository(database) as repository:
            scheduler = RuntimeScheduler(database_path=database, helper_client=client)
            server = ControlServer(
                ("127.0.0.1", 0), repository, helper_client=client, scheduler=scheduler
            )
            try:
                server.recover_saved_network_state()
                assert service._plan_count == 0
                assert not scheduler.is_active
                assert server.write_response_for("/api/apply", {})[0] == 503
                assert server.write_response_for("/api/normal-access", {})[0] == 200
                with patch.object(scheduler, "start"):
                    assert server.write_response_for("/api/apply", {})[0] == 200
                assert client.gateway_status()["panic_stop_latched"] is False
            finally:
                server.server_close()
                scheduler.stop()


def test_saved_flags_survive_monitor_and_outage_preference_changes():
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        with helper(directory) as (service, client):
            assert client.normal_access()["ok"] is True
            for enabled in (True, False, True):
                client.set_gateway_persistence(enabled=enabled)
                service.monitor_gateway_once()
            state = json.loads((directory / "gateway.json").read_text())
            assert state["panic_stop_latched"] is True and state["panic_recovery_failed"] is False
            assert service.handle_payload(apply_payload(AccessPlan()))["ok"] is False
        with helper(directory) as (service, client):
            assert client.gateway_status()["panic_stop_latched"] is True
            assert (
                service.handle_payload(
                    {
                        "action": "apply-experiment",
                        **{k: v for k, v in apply_payload(AccessPlan()).items() if k != "action"},
                    }
                )["ok"]
                is False
            )
            assert service._plan_count == 0


@pytest.mark.parametrize(
    "flags",
    [
        {},
        {"panic_stop_latched": "true"},
        {"panic_stop_latched": False, "panic_recovery_failed": True},
    ],
)
def test_missing_flags_are_compatible_and_invalid_present_flags_fail_closed(flags):
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        with helper(directory) as (service, _):
            service._write_gateway_state()
        path = directory / "gateway.json"
        state = json.loads(path.read_text())
        state.pop("panic_stop_latched")
        state.pop("panic_recovery_failed")
        path.write_text(json.dumps({**state, **flags}))
        with helper(directory) as (_, client):
            result = client.gateway_status()
            assert result["panic_stop_latched"] is bool(flags)
            assert result["panic_recovery_failed"] is bool(flags)
            assert result["state_error"] is bool(flags)


def test_durable_acknowledgement_syncs_file_and_parent_and_failed_sync_blocks_start(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        with helper(directory) as (service, client):
            original = os.fsync
            observed = []

            def sync(descriptor):
                observed.append("parent" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")
                return original(descriptor)

            monkeypatch.setattr(os, "fsync", sync)
            assert service._write_gateway_state(durable=True)
            assert observed == ["file", "parent"]
            monkeypatch.setattr(os, "fsync", original)
            client.normal_access()

            def fail(descriptor):
                raise OSError("sync unconfirmed")

            monkeypatch.setattr(os, "fsync", fail)
            answer = service.handle_payload({"action": "normal-access"})
            assert answer["ok"] is False
            assert service._panic_recovery_failed is True
            payload = {**apply_payload(AccessPlan()), "explicit_apply": True}
            assert service.handle_payload(payload)["ok"] is False
            monkeypatch.setattr(os, "fsync", original)
            assert client.normal_access()["ok"] is True
            payload["expected_control_revision"] = client.control_revision()
            assert service.handle_payload(payload)["ok"] is True

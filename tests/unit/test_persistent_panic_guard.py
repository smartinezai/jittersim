"""Panic survives runtime removal and gates both access startup callers."""

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.storage import ConfigRepository, StorageError
from jittersim.engine.access import AccessPlan
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking import access
from jittersim.networking import helper as helper_module
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.topology import Topology
from jittersim.panic_guard import PanicGuard
from tests.access_support import AccessExecutor
from tests.unit.test_helper_access import apply_payload
from tests.unit.test_panic_restart_latch import helper


def guard(directory):
    return PanicGuard(directory / "control" / "panic-state.json", owner_uid=os.getuid())


@pytest.fixture
def short_path():
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        yield Path(temporary).resolve()


def stopped_state(failed=False):
    return {
        "version": 1,
        "panic_stop_latched": True,
        "panic_recovery_failed": failed,
        "control_revision_required": True,
    }


def test_failed_sqlite_panic_remains_stopped_after_runtime_removal_and_reconstruction():
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        persistent = guard(directory)
        database = directory / "settings.sqlite"
        with (
            helper(directory, panic_guard=persistent) as (_, client),
            ConfigRepository(database) as repo,
        ):
            repo.update_runtime_state(
                observed_state="applied",
                last_action="apply",
                last_result="success",
                last_error=None,
            )
            scheduler = RuntimeScheduler(database_path=database, helper_client=client)
            server = ControlServer(
                ("127.0.0.1", 0), repo, helper_client=client, scheduler=scheduler
            )
            try:
                with (
                    patch.object(repo, "save_access_policies", side_effect=StorageError("full")),
                    patch.object(repo, "update_runtime_state", side_effect=StorageError("full")),
                ):
                    assert server.write_response_for("/api/normal-access", {})[0] == 503
                assert repo.get_runtime_state().last_action == "apply"
            finally:
                server.server_close()
        for name in ("gateway.json", "access-runtime.json", "active-plan.json"):
            (directory / name).unlink(missing_ok=True)
        with (
            helper(directory, panic_guard=persistent) as (service, client),
            ConfigRepository(database) as repo,
        ):
            scheduler = RuntimeScheduler(database_path=database, helper_client=client)
            server = ControlServer(
                ("127.0.0.1", 0), repo, helper_client=client, scheduler=scheduler
            )
            try:
                server.recover_saved_network_state()
                assert service._plan_count == 0
                assert not scheduler.is_active
                assert not service._access_manager.applied.options.enabled
                assert server.write_response_for("/api/apply", {})[0] == 503
                assert server.write_response_for("/api/normal-access", {})[0] == 200
                with patch.object(scheduler, "start"):
                    assert server.write_response_for("/api/apply", {})[0] == 200
                assert persistent.read()["panic_stop_latched"] is False
                assert persistent.read()["control_revision_required"] is True
            finally:
                server.server_close()


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "truncated",
        "oversized",
        "bool-int",
        "unknown",
        "mode",
        "symlink",
        "hardlink",
        "wrong-owner",
        "directory-symlink",
    ],
)
def test_damaged_or_untrusted_record_never_becomes_clear_initial_state(tmp_path, damage):
    persistent = guard(tmp_path)
    persistent.write(stopped_state())
    path = persistent.path
    if damage == "missing":
        path.unlink()
    elif damage == "truncated":
        path.write_text("{")
    elif damage == "oversized":
        path.write_text(" " * 4097)
    elif damage in ("bool-int", "unknown"):
        state = stopped_state()
        state["panic_stop_latched" if damage == "bool-int" else "new"] = 1
        path.write_text(json.dumps(state))
    elif damage == "mode":
        path.chmod(0o640)
    elif damage == "symlink":
        moved = tmp_path / "moved"
        path.rename(moved)
        path.symlink_to(moved)
    elif damage == "hardlink":
        os.link(path, tmp_path / "second")
    elif damage == "wrong-owner":
        persistent = PanicGuard(path, owner_uid=os.getuid() + 1)
    else:
        moved = tmp_path / "moved-dir"
        path.parent.rename(moved)
        path.parent.symlink_to(moved, target_is_directory=True)
    with pytest.raises(OSError):
        persistent.provision()


def test_first_provision_migrates_retained_panic_and_preserves_concurrent_stop(tmp_path):
    persistent = guard(tmp_path)
    legacy = tmp_path / "gateway.json"
    legacy.write_text(
        json.dumps(
            {
                "version": 1,
                "persistence_enabled": True,
                "gateway_state": "available",
                "outage_observed": False,
                "outage_stop_latched": False,
                "reset_pending": False,
                "recovery_pending": False,
                "consecutive_failures": 0,
                "consecutive_successes": 2,
                **stopped_state(True),
            }
        )
    )
    assert persistent.provision(legacy) == stopped_state(True)
    clear = {
        "version": 1,
        "panic_stop_latched": False,
        "panic_recovery_failed": False,
        "control_revision_required": False,
    }
    assert persistent.write(clear, initialize_only=True) == stopped_state(True)
    assert persistent.read() == stopped_state(True)


@pytest.mark.parametrize("failure_at", [1, 2, 3])
def test_sync_interruption_never_acknowledges_guard_durability(tmp_path, monkeypatch, failure_at):
    persistent = guard(tmp_path)
    original = os.fsync
    calls = 0

    def sync(descriptor):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError("durability unconfirmed")
        return original(descriptor)

    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(OSError):
        persistent.provision()
    monkeypatch.setattr(os, "fsync", original)
    if failure_at == 1:
        with pytest.raises(FileNotFoundError):
            persistent.provision()
    persistent.write(stopped_state(True))
    assert persistent.read() == stopped_state(True)


@pytest.mark.parametrize("entry", ["access-boot", "helper"])
@pytest.mark.parametrize("corrupt", [False, True])
def test_actual_startup_entry_never_restores_old_enabled_household_policy(
    short_path, monkeypatch, entry, corrupt
):
    tmp_path = short_path
    persistent = guard(tmp_path)
    persistent.write(stopped_state(corrupt))
    if corrupt:
        persistent.path.unlink()
    manager = access.AccessManager(
        executor=AccessExecutor(tmp_path),
        topology=Topology("eth0", "wlan0"),
        directory=tmp_path / "access",
        runtime_path=tmp_path / "runtime.json",
    )
    manager.directory.mkdir()
    saved = AccessPlan()
    manager.apply(replace(saved, options=replace(saved.options, enabled=True)))
    calls = []
    original = manager.apply

    def apply(plan, **kwargs):
        calls.append(plan)
        assert plan is not None and not plan.options.enabled
        return original(plan, **kwargs)

    monkeypatch.setattr(manager, "apply", apply)
    if entry == "access-boot":
        monkeypatch.setattr(access, "AccessManager", lambda **kwargs: manager)
        monkeypatch.setattr(access, "PanicGuard", lambda: persistent)
        assert access.main([]) == 0
    else:
        monkeypatch.setattr(helper_module, "AccessManager", lambda **kwargs: manager)
        monkeypatch.setattr(helper_module, "PanicGuard", lambda: persistent)
        monkeypatch.setattr(helper_module, "AccessStore", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            helper_module.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=os.getgid())
        )
        monkeypatch.setattr(
            helper_module,
            "NetworkApplier",
            lambda **kwargs: NetworkApplier(
                executor=FakeExecutor(), topology=Topology("eth0", "wlan0")
            ),
        )
        monkeypatch.setattr(NetworkApplier, "recover_wifi", lambda self: None)
        monkeypatch.setattr(helper_module.HelperService, "monitor_gateway_once", lambda self: None)
        serve_policy = helper_module.UnixHelperServer.serve_forever

        def stop_control_server(server):
            if server.policy_only:
                return serve_policy(server)
            raise KeyboardInterrupt()

        monkeypatch.setattr(helper_module.UnixHelperServer, "serve_forever", stop_control_server)
        helper_module.run_helper(
            socket_path=tmp_path / "helper.sock",
            topology=Topology("eth0", "wlan0"),
            enable_hardware=True,
        )
    assert len(calls) == 1
    assert not manager.applied.options.enabled


def test_failed_guard_save_keeps_panic_and_fresh_start_unconfirmed(short_path, monkeypatch):
    tmp_path = short_path
    persistent = guard(tmp_path)
    with helper(tmp_path, panic_guard=persistent) as (service, client):
        original = persistent.write
        monkeypatch.setattr(
            persistent, "write", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("full"))
        )
        with pytest.raises(helper_module.HelperClientError):
            client.normal_access()
        assert service._panic_recovery_failed
        assert not service._access_manager.applied.options.enabled

        monkeypatch.setattr(persistent, "write", original)
        client.normal_access()
        revision = client.control_revision()
        monkeypatch.setattr(
            persistent, "write", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("full"))
        )
        with pytest.raises(helper_module.HelperClientError):
            client._request(
                {
                    **apply_payload(AccessPlan()),
                    "explicit_apply": True,
                    "expected_control_revision": revision,
                }
            )
        assert service._panic_recovery_failed and service._panic_stop_latched
        assert not service._access_manager.applied.options.enabled


def test_failed_gateway_save_is_preserved_in_persistent_guard_after_runtime_loss(
    short_path, monkeypatch
):
    persistent = guard(short_path)
    with helper(short_path, panic_guard=persistent) as (service, client):
        monkeypatch.setattr(service, "_write_gateway_state", lambda **kwargs: False)
        with pytest.raises(helper_module.HelperClientError):
            client.normal_access()
        assert persistent.read()["panic_recovery_failed"] is True
    (short_path / "gateway.json").unlink(missing_ok=True)
    with helper(short_path, panic_guard=persistent) as (_, client):
        with pytest.raises(helper_module.HelperClientError, match="recovery is not confirmed"):
            client.control_revision()


@pytest.mark.parametrize("entry", ["access-boot", "helper"])
@pytest.mark.parametrize("legacy", [{"version": 1}, {"version": True}])
def test_malformed_present_legacy_gateway_never_provisions_clear_or_enables_saved_policy(
    short_path, monkeypatch, entry, legacy
):
    persistent = guard(short_path)
    legacy_path = short_path / "gateway-state.json"
    legacy_path.write_text(json.dumps(legacy))
    manager = access.AccessManager(
        executor=AccessExecutor(short_path),
        topology=Topology("eth0", "wlan0"),
        directory=short_path / "access",
        runtime_path=short_path / "runtime.json",
    )
    manager.directory.mkdir()
    saved = AccessPlan()
    manager.apply(replace(saved, options=replace(saved.options, enabled=True)))
    calls = []
    original = manager.apply

    def apply(plan, **kwargs):
        calls.append(plan)
        assert plan is not None and not plan.options.enabled
        return original(plan, **kwargs)

    monkeypatch.setattr(manager, "apply", apply)
    if entry == "access-boot":
        monkeypatch.setattr(access, "AccessManager", lambda **kwargs: manager)
        monkeypatch.setattr(access, "PanicGuard", lambda: persistent)
        monkeypatch.setattr(access, "ACCESS_RUNTIME", short_path / "access-policy.json")
        assert access.main([]) == 0
    else:
        service = helper_module.HelperService(
            NetworkApplier(executor=FakeExecutor(), topology=Topology("eth0", "wlan0")),
            access_manager=manager,
            gateway_state_path=legacy_path,
            panic_guard=persistent,
        )
        assert helper_module.restore_access_at_start(
            manager, panic_stopped=service._panic_stop_latched
        )
        assert service._panic_recovery_failed
    assert calls and all(plan is not None and not plan.options.enabled for plan in calls)
    assert persistent.read() is None

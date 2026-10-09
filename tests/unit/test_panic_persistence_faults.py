"""Panic stays fail-closed and recoverable when its durable state writes fail.

Each step of the two durable writers is failed separately on a disposable
fixture: the Panic guard (`PanicGuard.write`) and the gateway state file
(`HelperService._write_gateway_state`). The steps are the data write, the
flush, the file sync, the atomic rename, the directory sync and, for the
guard, the sync of the directory above it. Nothing here touches the live Pi.

Each failure is injected in one of three phases of one `normal-access` call:

- whole: every save of that writer fails, including the recovery result
- latch: only the first save fails, so the result save still succeeds
- completion: the first save works and the saves after the reset fail
"""

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from jittersim import panic_guard as guard_module
from jittersim.api.server import ControlServer
from jittersim.config.storage import ConfigRepository
from jittersim.engine.access import AccessPlan
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking import helper as helper_module
from jittersim.networking.access import AccessManager
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperClient, HelperClientError, HelperService
from jittersim.networking.topology import Topology
from jittersim.panic_guard import PANIC_GUARD_FIELDS, PanicGuard
from tests.access_support import AccessExecutor
from tests.panic_fault_support import (
    BEFORE_RENAME,
    GATEWAY_STAGES,
    GUARD_STAGES,
    FaultPlan,
    FaultyOs,
)
from tests.unit.test_helper_access import apply_payload
from tests.unit.test_panic_restart_latch import helper

CLEAR = {"version": 2, **dict.fromkeys(PANIC_GUARD_FIELDS, False)}
STOPPED = {
    "version": 2,
    "panic_stop_latched": True,
    "household_stop_latched": True,
    "panic_recovery_failed": False,
    "control_revision_required": True,
}
STOPPED_UNCONFIRMED = {**STOPPED, "panic_recovery_failed": True}
FAILING = [("guard", stage) for stage in GUARD_STAGES] + [
    ("gateway", stage) for stage in GATEWAY_STAGES
]
FLAGS = (
    "panic_stop_latched",
    "household_stop_latched",
    "panic_recovery_failed",
    "control_revision_required",
)


def flags(state):
    return {name: state[name] for name in FLAGS}


@contextmanager
def fault_rig(monkeypatch, target, stage, *, stalled=False):
    """A helper with running effects and a failure planned for one write step."""

    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary).resolve()
        persistent = PanicGuard(directory / "control" / "panic-state.json", owner_uid=os.getuid())
        persistent.write(CLEAR)
        plan = FaultPlan(stage, stalled=stalled)
        if target == "guard":
            monkeypatch.setattr(guard_module, "os", FaultyOs(plan, ".panic-"))
        else:
            monkeypatch.setattr(helper_module, "os", FaultyOs(plan, ".gateway.json."))
        with helper(directory, panic_guard=persistent) as (service, client):
            assert service._write_gateway_state(durable=True)
            started = service.handle_payload({**apply_payload(AccessPlan()), "explicit_apply": True})
            assert started["ok"] is True
            manager = service._access_manager
            enabled = AccessPlan()
            manager.apply(replace(enabled, options=replace(enabled.options, enabled=True)))
            # Both kinds of protected effect are running before Panic is pressed.
            assert manager.applied.options.enabled
            assert service._active_plan is not None
            assert (directory / "active-plan.json").exists()
            yield SimpleNamespace(
                directory=directory,
                guard=persistent,
                plan=plan,
                service=service,
                client=client,
                manager=manager,
                gateway_path=directory / "gateway.json",
                before_gateway=(directory / "gateway.json").read_bytes(),
                before_guard=persistent.read(),
            )


def run_panic(rig, phase):
    """Press Panic once with the planned failure active in the chosen phase."""

    service = rig.service
    original = service._applier.reset

    def reset():
        # The reset is the boundary between the first save and the result saves.
        rig.plan.armed = phase != "latch"
        return original()

    service._applier.reset = reset
    rig.plan.armed = phase != "completion"
    try:
        return service.handle_payload({"action": "normal-access"})
    finally:
        rig.plan.armed = False
        service._applier.reset = original


def leftovers(directory):
    """Hidden files other than the guard's own lock file, such as temporary state files."""

    return sorted(
        str(path.relative_to(directory))
        for path in directory.rglob(".*")
        if path.name != ".guard.lock"
    )


def restarted_service(directory, persistent):
    """A new helper object on the same files, as after a helper restart."""

    topology = Topology("eth0", "wlan0")
    manager = AccessManager(
        executor=AccessExecutor(directory),
        topology=topology,
        directory=directory,
        runtime_path=directory / "access-runtime.json",
    )
    return HelperService(
        NetworkApplier(executor=FakeExecutor(), topology=topology),
        active_plan_path=directory / "active-plan.json",
        access_manager=manager,
        gateway_state_path=directory / "gateway.json",
        panic_guard=persistent,
    )


def assert_effects_stopped(rig):
    assert rig.service._active_plan is None
    assert not (rig.directory / "active-plan.json").exists()
    assert not rig.manager.applied.options.enabled


@pytest.mark.parametrize("phase", ["whole", "completion"])
@pytest.mark.parametrize("target,stage", FAILING)
def test_failed_save_is_reported_and_stopped_effects_stay_stopped(
    monkeypatch, target, stage, phase
):
    with fault_rig(monkeypatch, target, stage) as rig:
        plans_before = rig.service._plan_count
        answer = run_panic(rig, phase)
        assert rig.plan.hits >= 1, "the injected failure never happened"

        assert answer["ok"] is False
        assert answer["error"] == "normal access recovery state could not be saved"
        assert answer["experiments_stopped"] is True and answer["access_disabled"] is True
        assert_effects_stopped(rig)
        status = rig.service._gateway_status()
        assert flags(status) == flags(STOPPED_UNCONFIRMED)
        assert status["state_error"] is True

        refused = rig.service.handle_payload(
            {
                **apply_payload(AccessPlan()),
                "explicit_apply": True,
                "expected_control_revision": rig.service._control_revision,
            }
        )
        assert refused["ok"] is False and "Retry Panic" in refused["error"]
        household = rig.service.handle_payload(
            {"action": "access-apply", "policy": AccessPlan().payload()}
        )
        assert household["ok"] is False
        assert rig.service._plan_count == plans_before
        assert_effects_stopped(rig)


@pytest.mark.parametrize("phase", ["whole", "completion"])
@pytest.mark.parametrize("target,stage", FAILING)
def test_failed_save_leaves_only_whole_records_and_no_stale_temporary(
    monkeypatch, target, stage, phase
):
    with fault_rig(monkeypatch, target, stage) as rig:
        assert run_panic(rig, phase)["ok"] is False
        assert leftovers(rig.directory) == []

        gateway = json.loads(rig.gateway_path.read_text())
        guard = rig.guard.read()
        # Nothing partial is ever visible. Each file is a complete older or newer record.
        assert gateway["version"] == 1
        assert guard["version"] == 2
        # The guard never records a recovery that Panic did not acknowledge.
        assert guard == rig.before_guard or guard["panic_recovery_failed"] is True

        before_rename = stage in BEFORE_RENAME
        if target == "gateway" and phase == "whole" and stage in ("write", "replace"):
            # The reset's own unsynced save fails at these steps too, so nothing changed.
            assert rig.gateway_path.read_bytes() == rig.before_gateway
        elif target == "gateway" and before_rename:
            assert gateway["panic_stop_latched"] is True
            assert gateway["panic_recovery_failed"] is True
        if target == "guard" and before_rename and phase == "whole":
            assert guard == rig.before_guard == CLEAR
        if target == "guard" and before_rename and phase == "completion":
            assert guard == STOPPED_UNCONFIRMED


@pytest.mark.parametrize("phase", ["whole", "completion"])
@pytest.mark.parametrize("target,stage", FAILING)
def test_retry_after_failed_save_recovers_and_allows_a_fresh_start(
    monkeypatch, target, stage, phase
):
    with fault_rig(monkeypatch, target, stage) as rig:
        assert run_panic(rig, phase)["ok"] is False

        # The storage fault is gone. The same Panic button now works over the real socket.
        assert rig.client.normal_access()["ok"] is True
        status = rig.client.gateway_status()
        assert flags(status) == flags(STOPPED)
        assert status["state_error"] is False
        assert rig.guard.read() == STOPPED
        saved = json.loads(rig.gateway_path.read_text())
        assert saved["panic_stop_latched"] is True and saved["panic_recovery_failed"] is False
        assert leftovers(rig.directory) == []
        assert_effects_stopped(rig)

        revision = rig.client.control_revision()
        started = rig.service.handle_payload(
            {**apply_payload(AccessPlan()), "explicit_apply": True, "expected_control_revision": revision}
        )
        assert started["ok"] is True
        status = rig.client.gateway_status()
        assert status["panic_stop_latched"] is False and status["panic_recovery_failed"] is False


@pytest.mark.parametrize("target,stage", FAILING)
def test_a_failed_first_save_alone_does_not_fail_a_panic_that_saved_its_result(
    monkeypatch, target, stage
):
    with fault_rig(monkeypatch, target, stage) as rig:
        answer = run_panic(rig, "latch")
        assert rig.plan.hits >= 1, "the injected failure never happened"
        assert answer == {
            "ok": True,
            "action": "normal-access",
            "experiments_stopped": True,
            "access_disabled": True,
        }
        status = rig.service._gateway_status()
        assert flags(status) == flags(STOPPED)
        assert status["state_error"] is False
        assert rig.guard.read() == STOPPED
        assert json.loads(rig.gateway_path.read_text())["panic_recovery_failed"] is False
        assert leftovers(rig.directory) == []
        assert_effects_stopped(rig)


LOST_LATCH = (
    "A guard save that fails before its rename leaves the previous clear record. A restarted "
    "helper trusts the guard over gateway.json (HelperService.__init__ reads the guard after "
    "_load_gateway_state), so the latch and the unconfirmed-recovery marker of a Panic that "
    "reported failure are forgotten until the owner presses Panic again."
)


def restart_cases():
    for phase in ("whole", "completion"):
        for target, stage in FAILING:
            forgotten = target == "guard" and stage in BEFORE_RENAME and phase == "whole"
            marks = [pytest.mark.xfail(strict=True, reason=LOST_LATCH)] if forgotten else []
            yield pytest.param(target, stage, phase, marks=marks, id=f"{target}-{stage}-{phase}")


@pytest.mark.parametrize("target,stage,phase", list(restart_cases()))
def test_helper_restart_after_a_failed_panic_save_stays_stopped_and_unconfirmed(
    monkeypatch, target, stage, phase
):
    with fault_rig(monkeypatch, target, stage) as rig:
        assert run_panic(rig, phase)["ok"] is False
        restarted = restarted_service(rig.directory, rig.guard)
        status = restarted._gateway_status()
        assert flags(status) == flags(STOPPED_UNCONFIRMED)
        refused = restarted.handle_payload(
            {
                **apply_payload(AccessPlan()),
                "explicit_apply": True,
                "expected_control_revision": restarted._control_revision,
            }
        )
        assert refused["ok"] is False


@pytest.mark.parametrize("target,stage", [("guard", "write"), ("guard", "dir-sync"), ("gateway", "file-sync"), ("gateway", "dir-sync")])
def test_dashboard_panic_reports_the_failed_save_and_a_retry_recovers(monkeypatch, target, stage):
    with fault_rig(monkeypatch, target, stage) as rig:
        database = rig.directory / "settings.sqlite"
        with ConfigRepository(database) as repository:
            scheduler = RuntimeScheduler(database_path=database, helper_client=rig.client)
            server = ControlServer(
                ("127.0.0.1", 0), repository, helper_client=rig.client, scheduler=scheduler
            )
            try:
                rig.plan.armed = True
                status, _, body = server.write_response_for("/api/normal-access", {})
                rig.plan.armed = False
                assert status == 503
                assert b"Retry the panic button" in body
                state = repository.get_runtime_state()
                assert state.observed_state == "error" and state.last_result == "failure"
                assert not scheduler.is_active
                assert_effects_stopped(rig)

                status, _, body = server.write_response_for("/api/normal-access", {})
                assert status == 200, body
                state = repository.get_runtime_state()
                assert state.observed_state == "reset" and state.last_result == "success"
                assert rig.service._gateway_status()["panic_recovery_failed"] is False
                assert leftovers(rig.directory) == []
            finally:
                server.server_close()
                scheduler.stop()


def hidden_copy(path, state, name):
    stale = path.parent / name
    stale.write_text(json.dumps(state))
    stale.chmod(0o600)
    # A newer time stamp must not make a stale file look more current.
    future = time.time() + 3600
    os.utime(stale, (future, future))
    return stale


def test_stale_guard_temporary_is_never_read_as_committed_state(tmp_path):
    persistent = PanicGuard(tmp_path / "control" / "panic-state.json", owner_uid=os.getuid())
    persistent.write(STOPPED_UNCONFIRMED)
    stale = hidden_copy(persistent.path, CLEAR, ".panic-" + "ab" * 16)

    assert persistent.read() == STOPPED_UNCONFIRMED
    assert persistent.provision() == STOPPED_UNCONFIRMED
    service = restarted_service(tmp_path, persistent)
    assert flags(service._gateway_status()) == flags(STOPPED_UNCONFIRMED)

    persistent.write(STOPPED)
    assert persistent.read() == STOPPED
    # The old temporary is neither adopted nor consumed by a later commit.
    assert json.loads(stale.read_text()) == CLEAR


def test_stale_guard_temporary_does_not_stand_in_for_a_missing_committed_record(tmp_path):
    persistent = PanicGuard(tmp_path / "control" / "panic-state.json", owner_uid=os.getuid())
    persistent.write(STOPPED)
    persistent.path.unlink()
    hidden_copy(persistent.path, CLEAR, ".panic-" + "cd" * 16)

    with pytest.raises(FileNotFoundError):
        persistent.read()
    with pytest.raises(OSError):
        persistent.provision()
    service = restarted_service(tmp_path, persistent)
    status = service._gateway_status()
    assert flags(status) == flags(STOPPED_UNCONFIRMED)
    assert status["state_error"] is True


def test_stale_gateway_temporary_is_never_loaded_as_committed_state(tmp_path):
    first = restarted_service(tmp_path, None)
    assert first.handle_payload({"action": "normal-access"})["ok"] is True
    gateway = tmp_path / "gateway.json"
    committed = json.loads(gateway.read_text())
    assert committed["panic_stop_latched"] is True
    clear = {
        **committed,
        "panic_stop_latched": False,
        "household_stop_latched": False,
        "panic_recovery_failed": False,
        "control_revision_required": False,
    }
    stale = hidden_copy(gateway, clear, f".gateway.json.{os.getpid()}.{time.time_ns()}")

    status = restarted_service(tmp_path, None)._gateway_status()
    assert status["panic_stop_latched"] is True and status["household_stop_latched"] is True
    assert status["control_revision_required"] is True and status["state_error"] is False

    # Without any committed file the stale record is still ignored, not adopted.
    gateway.unlink()
    stale.write_text(json.dumps({**committed, "panic_stop_latched": True}))
    assert restarted_service(tmp_path, None)._gateway_status()["panic_stop_latched"] is False


def test_stalled_state_sync_blocks_a_second_panic_with_helper_busy_then_recovers(monkeypatch):
    monkeypatch.setattr(helper_module, "WRITE_LOCK_WAIT_SECONDS", 0.6)
    with fault_rig(monkeypatch, "guard", "file-sync", stalled=True) as rig:
        socket_path = rig.directory / "helper.sock"
        outcome = {}

        def first_panic():
            try:
                outcome["answer"] = HelperClient(
                    socket_path=socket_path, timeout_seconds=30
                )._request({"action": "normal-access"})
            except HelperClientError as error:
                outcome["error"] = error

        rig.plan.armed = True
        worker = threading.Thread(target=first_panic, daemon=True)
        worker.start()
        try:
            assert rig.plan.entered.wait(5), "the first Panic never reached the stalled sync"

            started = time.monotonic()
            with pytest.raises(HelperClientError) as busy:
                rig.client.normal_access()
            assert busy.value.reason == "helper-busy"
            assert time.monotonic() - started < 2.0

            # A caller whose deadline is shorter than the lock wait gives up on its own.
            started = time.monotonic()
            with pytest.raises(HelperClientError):
                HelperClient(socket_path=socket_path, timeout_seconds=0.2)._request(
                    {"action": "normal-access"}
                )
            assert time.monotonic() - started < 2.0

            # Requests that take no network lock still answer while the sync is stalled.
            assert rig.client.ping()["ok"] is True
            during = rig.client._request({"action": "control-revision"})
            assert during["recovery_required"] is True
            assert rig.service._panic_stop_latched is True
        finally:
            rig.plan.release.set()
            worker.join(10)
            rig.plan.armed = False
        assert not worker.is_alive()
        assert outcome.get("answer", {}).get("ok") is True, outcome

        assert rig.client.normal_access()["ok"] is True
        assert flags(rig.client.gateway_status()) == flags(STOPPED)
        assert rig.guard.read() == STOPPED
        assert leftovers(rig.directory) == []
        assert_effects_stopped(rig)

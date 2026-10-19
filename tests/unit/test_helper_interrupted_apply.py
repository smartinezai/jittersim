"""A helper restarted after an interrupted apply removes what nothing owns (H-ORPHAN).

The published plan (active-plan.json) is written only after a complete apply.
An apply-in-progress mark next to it tells the next start that the kernel may
hold part of a plan that no published plan describes.
"""

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from jittersim.engine.codec import plan_to_payload
from jittersim.networking import helper as helper_module
from jittersim.networking.applier import ApplyError, NetworkApplier
from jittersim.networking.executor import CommandExecutionError, FakeExecutor
from jittersim.networking.helper import (
    HelperService,
    interrupted_apply_mark,
    release_interrupted_apply_at_start,
)
from jittersim.networking.topology import Topology
from jittersim.panic_guard import PanicGuard
from tests.unit.test_helper import make_plan


class Applier(NetworkApplier):
    """A fake-executor applier whose apply and reset can fail on request."""

    def __init__(self):
        super().__init__(executor=FakeExecutor(), topology=Topology("eth0", "wlan0"))
        self.resets = 0
        self.fail_reset = False
        self.crash_apply = None

    def reset(self, *args, **kwargs):
        self.resets += 1
        if self.fail_reset:
            raise CommandExecutionError("tc failed")
        return super().reset(*args, **kwargs)

    def apply(self, *args, **kwargs):
        if self.crash_apply is not None:
            # Stands in for the process dying in the middle of the apply.
            raise self.crash_apply
        return super().apply(*args, **kwargs)


class HelperDied(BaseException):
    pass


def service_in(directory, applier=None):
    applier = applier or Applier()
    return HelperService(applier, active_plan_path=directory / "active-plan.json")


def apply(service):
    return service.handle_payload({"action": "apply", "plan": plan_to_payload(make_plan())})


def test_no_mark_at_start_changes_nothing_and_the_first_apply_runs(tmp_path):
    service = service_in(tmp_path)
    assert release_interrupted_apply_at_start(service)
    assert service._applier.resets == 0
    assert not service._panic_stop_latched
    assert apply(service)["ok"]
    assert (tmp_path / "active-plan.json").is_file()


def test_a_complete_apply_clears_the_mark_after_publishing_the_plan(tmp_path):
    service = service_in(tmp_path)
    seen = []
    original = service._write_active_plan

    def write(plan):
        # The mark is still there while the plan is published.
        seen.append(interrupted_apply_mark(tmp_path / "active-plan.json").exists())
        original(plan)

    service._write_active_plan = write
    assert apply(service)["ok"]
    assert seen == [True]
    assert not interrupted_apply_mark(tmp_path / "active-plan.json").exists()


def test_the_mark_exists_before_the_first_network_change(tmp_path):
    applier = Applier()
    applier.crash_apply = HelperDied()
    service = service_in(tmp_path, applier)
    with pytest.raises(HelperDied):
        apply(service)
    assert interrupted_apply_mark(tmp_path / "active-plan.json").exists()
    assert not (tmp_path / "active-plan.json").exists()


def test_a_restart_after_a_crash_between_mark_and_change_resets_and_clears(tmp_path):
    applier = Applier()
    applier.crash_apply = HelperDied()
    with pytest.raises(HelperDied):
        apply(service_in(tmp_path, applier))
    restarted = service_in(tmp_path)
    assert release_interrupted_apply_at_start(restarted)
    assert restarted._applier.resets == 1
    assert not interrupted_apply_mark(tmp_path / "active-plan.json").exists()
    assert not restarted._panic_stop_latched
    # Idempotent: a second start finds nothing to do.
    assert release_interrupted_apply_at_start(restarted)
    assert restarted._applier.resets == 1
    assert apply(restarted)["ok"]


def test_a_mark_next_to_an_older_published_plan_removes_both(tmp_path):
    # A change of a running plan died: the old published plan no longer matches the kernel.
    service = service_in(tmp_path)
    assert apply(service)["ok"]
    interrupted_apply_mark(tmp_path / "active-plan.json").touch()
    restarted = service_in(tmp_path)
    assert release_interrupted_apply_at_start(restarted)
    assert not (tmp_path / "active-plan.json").exists()
    assert restarted._current_plan() is None


def test_a_mark_with_unexpected_content_still_counts(tmp_path):
    interrupted_apply_mark(tmp_path / "active-plan.json").write_bytes(b"\x00garbage{")
    restarted = service_in(tmp_path)
    assert release_interrupted_apply_at_start(restarted)
    assert restarted._applier.resets == 1
    assert not interrupted_apply_mark(tmp_path / "active-plan.json").exists()


def test_a_failed_release_keeps_the_mark_and_holds_the_unconfirmed_stop(tmp_path):
    interrupted_apply_mark(tmp_path / "active-plan.json").touch()
    applier = Applier()
    applier.fail_reset = True
    restarted = service_in(tmp_path, applier)
    assert not release_interrupted_apply_at_start(restarted)
    assert interrupted_apply_mark(tmp_path / "active-plan.json").exists()
    assert restarted._panic_stop_latched and restarted._panic_recovery_failed
    # Profiles do not start until Panic is retried.
    assert not apply(restarted)["ok"]
    # Panic removes it and clears the mark.
    applier.fail_reset = False
    assert restarted._handle_reset()["ok"]
    assert not interrupted_apply_mark(tmp_path / "active-plan.json").exists()


def test_an_unexpected_release_error_never_escapes(tmp_path):
    interrupted_apply_mark(tmp_path / "active-plan.json").touch()
    applier = Applier()
    applier.reset = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("odd"))
    restarted = service_in(tmp_path, applier)
    assert not release_interrupted_apply_at_start(restarted)
    assert interrupted_apply_mark(tmp_path / "active-plan.json").exists()


@pytest.mark.parametrize(
    "rolled_back,rollback_fails,kept",
    [(True, False, False), (True, True, True), (False, False, True)],
)
def test_only_a_complete_rollback_clears_the_mark(tmp_path, rolled_back, rollback_fails, kept):
    applier = Applier()
    applier.crash_apply = ApplyError(
        "apply failed", failed_command_index=3, cause=CommandExecutionError("tc failed"),
        reset_error=CommandExecutionError("reset failed") if rollback_fails else None,
        cleanup_attempted=rolled_back,
    )
    answer = apply(service_in(tmp_path, applier))
    assert not answer["ok"]
    assert answer["cleanup_failed"] is rollback_fails
    assert interrupted_apply_mark(tmp_path / "active-plan.json").exists() is kept


class FakeAccess:
    release_incomplete = False

    def __init__(self, **kwargs):
        self.calls = []

    def restore(self):
        self.calls.append("restore")

    def disable(self, **kwargs):
        self.calls.append("disable")


@pytest.fixture
def short_path():
    with tempfile.TemporaryDirectory(prefix="jsho-", dir="/tmp") as temporary:
        yield Path(temporary).resolve()


@pytest.mark.parametrize("reset_fails", [False, True])
def test_the_helper_starts_whether_the_release_works_or_not(short_path, monkeypatch, reset_fails):
    socket_path = short_path / "helper.sock"
    mark = interrupted_apply_mark(short_path / "active-plan.json")
    mark.touch()
    applier = Applier()
    applier.fail_reset = reset_fails
    persistent = PanicGuard(short_path / "control" / "panic-state.json", owner_uid=os.getuid())
    persistent.write({"version": 2, "panic_stop_latched": False, "household_stop_latched": False,
                      "panic_recovery_failed": False, "control_revision_required": False})
    accesses = []

    def make_access(**kwargs):
        accesses.append(FakeAccess())
        return accesses[-1]

    served = []
    monkeypatch.setattr(helper_module, "NetworkApplier", lambda **kwargs: applier)
    monkeypatch.setattr(NetworkApplier, "recover_wifi", lambda self: None)
    monkeypatch.setattr(helper_module, "AccessManager", make_access)
    monkeypatch.setattr(helper_module, "PanicGuard", lambda: persistent)
    monkeypatch.setattr(helper_module, "AccessStore", lambda *args, **kwargs: None)
    monkeypatch.setattr(helper_module.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=os.getgid()))
    monkeypatch.setattr(helper_module.HelperService, "monitor_gateway_once", lambda self: None)
    serve_policy = helper_module.UnixHelperServer.serve_forever

    def serve(server):
        if getattr(server, "policy_only", False):
            return serve_policy(server)
        # The release ran before the helper accepts any request.
        served.append(mark.exists())
        raise KeyboardInterrupt()

    monkeypatch.setattr(helper_module.UnixHelperServer, "serve_forever", serve)
    helper_module.run_helper(socket_path=socket_path, topology=Topology("eth0", "wlan0"),
                             enable_hardware=True)
    assert served == [reset_fails]
    assert mark.exists() is reset_fails
    saved = persistent.read()
    assert saved["panic_recovery_failed"] is reset_fails
    assert saved["panic_stop_latched"] is reset_fails
    # The unconfirmed stop keeps household rules released, as after an unconfirmed Panic.
    assert accesses[0].calls == (["disable"] if reset_fails else ["restore"])

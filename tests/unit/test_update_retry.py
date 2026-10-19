"""Explicit retry follows one retained operation without replaying new work."""

import re
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from jittersim import cli, updates
from jittersim.command import main as jittersim


STOPPED = "ActiveState=failed\nMainPID=0\nControlPID=0\nTasksCurrent=0\nJob=\n"


@pytest.fixture
def failed(tmp_path):
    root, code, config = (tmp_path / name for name in ("updates", "code", "config"))
    for path in (root, code, config):
        path.mkdir(mode=0o700)
    request = {"request_id": "a" * 32, "request_kind": "install", "version": "v1",
               "rollback": False, "attempt": "b" * 32, "restart_only": True}
    updates.atomic_json(root / "request.json", request)
    updates.atomic_json(root / "status.json", {"phase": "failed", "message": "Recovery paused.",
                        "request_id": request["request_id"], "request_kind": "install"})
    return root, code, config, request


def status(failed, query=lambda *args: STOPPED):
    root, code, config, _ = failed
    return updates.update_status(root=root, code=code, config=config, query=query)


def test_failed_status_exposes_a_bounded_read_only_retry(failed):
    root, _, _, request = failed
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    view = status(failed)
    assert view["request_id"] == request["request_id"]
    assert view["request_kind"] == "install"
    assert re.fullmatch("[0-9a-f]{64}", view["retry"]["id"])
    assert view["retry"]["request_id"] == request["request_id"]
    assert view["retry"]["kind"] == "install"
    assert view["retry"]["version"] == "v1"
    assert view["retry"]["recovering"] is True
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


def test_retry_reuses_the_exact_retained_request_and_starts_once(failed):
    root, code, config, request = failed
    retry_id = status(failed)["retry"]["id"]
    calls = []
    result = updates.retry_update(retry_id, root=root, code=code, config=config,
                                  query=lambda *args: STOPPED,
                                  run=lambda *args: calls.append(args))
    assert len(calls) == 1
    assert calls[0][0] == ["systemctl", "start", "--no-block", "jittersim-update.service"]
    assert updates.read_json(root / "request.json") == request
    assert result["phase"] == "queued"
    assert result["request_id"] == request["request_id"]
    assert result["retry"] is None


def test_retry_refuses_a_changed_intent_without_starting(failed):
    root, code, config, request = failed
    retry_id = status(failed)["retry"]["id"]
    request["attempt"] = "c" * 32
    updates.atomic_json(root / "request.json", request)
    with patch.object(updates, "run_command") as run:
        with pytest.raises(updates.UpdateError, match="changed"):
            updates.retry_update(retry_id, root=root, code=code, config=config,
                                 query=lambda *args: STOPPED, run=run)
    run.assert_not_called()


def test_retry_rechecks_intent_after_stopped_unit_probe(failed):
    root, code, config, request = failed
    retry_id = status(failed)["retry"]["id"]

    def replaced(command, timeout):
        updates.atomic_json(root / "request.json", {**request, "attempt": "c" * 32})
        return STOPPED

    with patch.object(updates, "run_command") as run, pytest.raises(updates.UpdateError, match="changed"):
        updates.retry_update(retry_id, root=root, code=code, config=config, query=replaced, run=run)
    run.assert_not_called()


def test_retry_rechecks_status_after_stopped_unit_probe(failed):
    root, code, config, _ = failed
    retry_id = status(failed)["retry"]["id"]

    def changed(command, timeout):
        record = updates.read_json(root / "status.json")
        updates.atomic_json(root / "status.json", {**record, "phase": "installing"})
        return STOPPED

    with patch.object(updates, "run_command") as run, pytest.raises(updates.UpdateError, match="changed"):
        updates.retry_update(retry_id, root=root, code=code, config=config, query=changed, run=run)
    run.assert_not_called()


@pytest.mark.parametrize("phase", ["queued", "preparing", "stopping", "backing-up",
                                    "installing", "checking", "rolling-back"])
def test_orphaned_operation_is_retryable_only_after_confirmed_stop(failed, phase):
    root, code, config, request = failed
    record = updates.read_json(root / "status.json")
    updates.atomic_json(root / "status.json", {**record, "phase": phase})
    before = (root / "status.json").read_bytes()
    view = status(failed)
    assert view["phase"] == "failed" and view["retry"]
    assert view["request_id"] == request["request_id"]
    assert (root / "status.json").read_bytes() == before
    busy = status(failed, lambda *args: STOPPED.replace("ActiveState=failed", "ActiveState=activating"))
    assert busy["phase"] == phase and busy["retry"] is None
    calls = []
    queued = updates.retry_update(view["retry"]["id"], root=root, code=code, config=config,
                                  query=lambda *args: STOPPED, run=lambda *args: calls.append(args))
    assert len(calls) == 1 and queued["request_id"] == request["request_id"]


def test_apply_waits_for_the_queue_owner_to_release_its_lock(failed):
    root, _, _, _ = failed
    # No application work is requested. This isolates the real entry-point lock.
    (root / "request.json").unlink()
    source = ("from pathlib import Path; from jittersim.updates import apply_update; "
              "import sys; apply_update(root=Path(sys.argv[1]))")
    process = None
    try:
        with updates.update_lock(root):
            process = subprocess.Popen([sys.executable, "-c", source, str(root)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(0.2)
            assert process.poll() is None, "the update unit exited before the queue owner released its lock"
        process.communicate(timeout=5)
        assert process.returncode == 0
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_update_lock_wait_has_a_finite_allowance(failed):
    root, _, _, _ = failed
    start = time.monotonic()
    with updates.update_lock(root), pytest.raises(updates.UpdateError, match="already running"):
        with updates.update_lock(root, wait=0.05):
            pytest.fail("another writer already owns the lock")
    assert time.monotonic() - start < 0.5


def test_pending_start_job_is_future_work_even_with_no_processes(failed):
    root, _, _, _ = failed
    record = updates.read_json(root / "status.json")
    updates.atomic_json(root / "status.json", {**record, "phase": "queued"})

    def queued_job(command, timeout):
        return STOPPED.replace("Job=\n", "Job=12\n") if "--property=Job" in command else STOPPED.replace("Job=\n", "")

    view = status(failed, queued_job)
    assert view["phase"] == "queued" and view["retry"] is None


def test_admission_refuses_a_job_queued_after_first_unit_answer(failed):
    root, code, config, _ = failed
    retry_id = status(failed)["retry"]["id"]
    answers = iter((STOPPED, STOPPED.replace("Job=\n", "Job=12\n")))
    with patch.object(updates, "run_command") as run, pytest.raises(updates.UpdateError):
        updates.retry_update(retry_id, root=root, code=code, config=config,
                             query=lambda *args: next(answers), run=run)
    run.assert_not_called()


def test_unknown_status_probe_remains_owned_before_another_probe(failed, monkeypatch):
    class Pending:
        pid = 123

        def wait(self, timeout):
            return 0

    queries = []
    alive = [True]
    monkeypatch.setattr(updates, "command_still_running", lambda *args: alive[0])

    def query(command, timeout):
        queries.append(command)
        if len(queries) == 1:
            raise updates.CommandNotStopped("controlled status probe", Pending())
        return STOPPED

    assert status(failed, query)["retry"] is None
    assert status(failed, query)["retry"] is None
    assert len(queries) == 1, "unknown probe completion must retain the single probe reservation"
    alive[0] = False
    assert status(failed, query)["retry"]


@pytest.mark.parametrize("answer", [
    STOPPED.replace("MainPID=0", "MainPID=7"),
    STOPPED.replace("ControlPID=0", "ControlPID=7"),
    STOPPED.replace("TasksCurrent=0", "TasksCurrent=1"),
    STOPPED.replace("TasksCurrent=0", "TasksCurrent=[not set]"),
    STOPPED.replace("ActiveState=failed", "ActiveState=activating"),
    STOPPED + "MainPID=0\n", "garbled", "",
])
def test_unknown_or_busy_unit_never_offers_or_admits_retry(failed, answer):
    root, code, config, _ = failed
    retry_id = status(failed)["retry"]["id"]
    view = status(failed, lambda *args: answer)
    assert view["retry"] is None
    assert view["retry_unavailable_reason"]
    with patch.object(updates, "run_command") as run:
        with pytest.raises(updates.UpdateError):
            updates.retry_update(retry_id, root=root, code=code, config=config,
                                 query=lambda *args: answer, run=run)
    run.assert_not_called()


def test_unfinished_group_is_not_retryable(failed, monkeypatch):
    root, code, config, request = failed
    request["command_pending"] = {"group": 123, "boot": updates._current_boot()}
    updates.atomic_json(root / "request.json", request)
    monkeypatch.setattr(updates, "command_still_running", lambda *args: True)
    assert status(failed)["retry"] is None
    with patch.object(updates, "run_command") as run, pytest.raises(updates.UpdateError):
        updates.retry_update("f" * 64, root=root, code=code, config=config,
                             query=lambda *args: STOPPED, run=run)
    run.assert_not_called()


def test_late_unit_answer_does_not_offer_retry(failed, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(updates.time, "monotonic", lambda: clock[0])

    def late_query(command, timeout):
        assert 0 < timeout <= 2
        clock[0] += timeout + 1
        return STOPPED

    assert status(failed, late_query)["retry"] is None


@pytest.mark.parametrize("field,value", [
    ("version", ["v1"]), ("attempt", "../../backup"),
    ("restart_only", "false"), ("command_pending", {}),
    ("command_pending", {"group": 123, "boot": "not-a-boot-id"}),
])
def test_damaged_retained_intent_never_becomes_retryable(failed, field, value):
    root, _, _, request = failed
    request[field] = value
    updates.atomic_json(root / "request.json", request)
    assert status(failed)["retry"] is None


def test_uncertain_retry_answer_never_replays_wait_start():
    public_id, retry_id = "a" * 32, "b" * 64
    initial = {"retry": {"id": retry_id, "request_id": public_id, "recovering": True}}
    with patch.object(cli, "HelperClient") as helper, \
            patch.object(cli.os, "geteuid", return_value=0), \
            patch.object(cli.subprocess, "run") as start:
        helper.return_value.release_status.return_value = initial
        helper.return_value.retry_update.return_value = {}
        assert jittersim(["update", "retry", "--yes", "--wait"]) == 1
    start.assert_not_called()


def test_unknown_retry_start_keeps_operation_and_failed_intent(failed):
    root, code, config, request = failed
    retry_id = status(failed)["retry"]["id"]

    def fail_start(*args):
        raise updates.UpdateError("controlled start failure")

    with pytest.raises(updates.UpdateError):
        updates.retry_update(retry_id, root=root, code=code, config=config,
                             query=lambda *args: STOPPED, run=fail_start)
    assert updates.read_json(root / "request.json") == request
    view = status(failed)
    assert view["phase"] == "failed" and view["request_id"] == request["request_id"]


def test_retry_command_follows_same_operation_without_starting_again():
    public_id, retry_id = "a" * 32, "b" * 64
    initial = {"phase": "failed", "request_id": public_id, "request_kind": "install",
               "retry": {"id": retry_id, "request_id": public_id, "kind": "install",
                         "version": "v1", "recovering": True}}
    with patch.object(cli, "HelperClient") as helper, \
            patch.object(cli.os, "geteuid", return_value=0), \
            patch.object(cli.subprocess, "run") as start, \
            patch.object(updates, "update_status", return_value={
                "phase": "rolled-back", "request_id": public_id,
                "request_kind": "install", "message": "Previous installation restored."}):
        helper.return_value.release_status.return_value = initial
        helper.return_value.retry_update.return_value = {**initial, "phase": "queued", "retry": None}
        assert jittersim(["update", "retry", "--yes", "--wait"]) == 0
    helper.return_value.retry_update.assert_called_once_with(retry_id=retry_id)
    start.assert_not_called()


def test_wait_refuses_a_different_operation_without_replaying_it():
    with patch.object(updates, "update_status", return_value={
            "phase": "complete", "request_id": "c" * 32}), \
            patch.object(cli.subprocess, "run") as start:
        assert cli._wait_for_update(request_id="a" * 32, recovering=True) == 1
    start.assert_not_called()


def test_identified_install_wait_never_replays_service_start():
    public_id = "a" * 32
    initial = {"installed": "old", "available": "v1", "installable": True}
    with patch.object(cli, "HelperClient") as helper, \
            patch.object(cli.os, "geteuid", return_value=0), \
            patch.object(cli.subprocess, "run") as start, \
            patch.object(updates, "update_status", return_value={
                "phase": "complete", "request_id": public_id, "message": "Release installed."}):
        helper.return_value.release_status.return_value = initial
        helper.return_value.install_release.return_value = {"request_id": public_id, "request_kind": "install"}
        assert jittersim(["update", "install", "--yes", "--wait"]) == 0
    start.assert_not_called()


@pytest.mark.parametrize("recovering,kind,warning", [(True, "install", True),
                                                    (False, "rollback", True),
                                                    (False, "install", False)])
def test_retry_confirmation_explains_saved_copy_replacement(recovering, kind, warning):
    initial = {"retry": {"id": "b" * 64, "recovering": recovering, "kind": kind}}
    with patch.object(cli, "HelperClient") as helper, \
            patch.object(cli.os, "geteuid", return_value=0), \
            patch.object(cli, "_confirm", return_value=False) as confirm:
        helper.return_value.release_status.return_value = initial
        assert jittersim(["update", "retry"]) == 0
    assert ("changes after that backup will be replaced" in confirm.call_args.args[0].lower()) is warning
    helper.return_value.retry_update.assert_not_called()


def test_rollback_confirmation_explains_later_settings_replacement():
    with patch.object(cli, "HelperClient") as helper, \
            patch.object(cli.os, "geteuid", return_value=0), \
            patch.object(cli, "_confirm", return_value=False) as confirm:
        helper.return_value.release_status.return_value = {"rollback_available": True}
        assert jittersim(["update", "rollback"]) == 0
    assert "Changes after that backup will be replaced" in confirm.call_args.args[0]
    helper.return_value.install_release.assert_not_called()


def test_a_retained_development_build_can_be_retried(failed):
    # Development builds are labelled X.Y.Z.dev+COMMIT. The retry check uses the same version
    # matcher as staging, so the '+' does not refuse valid retained work.
    root, code, config, request = failed
    request["version"] = "0.3.1.dev+abc1234"
    updates.atomic_json(root / "request.json", request)
    view = status(failed)
    assert view["retry"] is not None and view["retry"]["version"] == "0.3.1.dev+abc1234"
    request["version"] = "0.3.1 dev;rm"
    updates.atomic_json(root / "request.json", request)
    assert status(failed)["retry"] is None

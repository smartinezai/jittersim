"""Explicit retry follows one retained operation without replaying new work."""

import re
from unittest.mock import patch

import pytest

from jittersim import cli, updates
from jittersim.command import main as jittersim


STOPPED = "ActiveState=failed\nMainPID=0\nControlPID=0\nTasksCurrent=0\n"


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

"""The online check as the dashboard uses it: jobs, the ten minute cache, the settings, the daily
check and the automatic install. The root fetch unit does the work; the dashboard never does."""

import json

import pytest

from jittersim import updates
from tests.github_support import TOKEN
from tests.release_support import have_ssh_keygen
from tests.unit.test_online_release import Online

pytestmark = pytest.mark.skipif(not have_ssh_keygen(), reason="ssh-keygen signs the releases")


@pytest.fixture
def online(tmp_path):
    fixture = Online(tmp_path)
    fixture.clock = [1_000_000.0]
    fixture.ids = iter("%016x" % number for number in range(1, 99))
    yield fixture
    fixture.github.stop()


def request(online, **options):
    return updates.request_online_check(
        root=online.pi.root, config=online.pi.config, run=online.run,
        now=lambda: online.clock[0], job_id=lambda: next(online.ids), **options
    )


def fetch(online, **options):
    return updates.run_fetch_job(
        root=online.pi.root, code=online.pi.code, config=online.pi.config,
        source=online.source(), run=online.run, now=lambda: online.clock[0], **options
    )


def overview(online, job=None):
    return updates.online_overview(job, root=online.pi.root, config=online.pi.config,
                                   now=lambda: online.clock[0])


def test_a_request_returns_a_job_at_once_and_starts_the_root_unit(online):
    answer = request(online)
    assert answer["state"] == "running" and answer["job"] == "%016x" % 1
    assert online.run_calls == [["systemctl", "start", "--no-block", updates.FETCH_UNIT]]
    # Nothing was fetched yet: only the unit does that.
    assert online.github.requests == []
    assert overview(online, answer["job"])["state"] == "running"


def test_the_unit_runs_the_job_and_the_status_shows_the_result(online):
    job = request(online)["job"]
    record = fetch(online)
    assert record["job"] == job and record["state"] == "done" and record["result"] == "staged"
    status = overview(online, job)
    assert status["state"] == "done" and status["result"] == "staged"
    assert status["available"] == "0.7.0" and status["tag"] == "v0.7.0"
    assert status["checked_at"] == int(online.clock[0])
    assert "v0.7.0" in status["message"]
    assert not (online.pi.root / updates.ONLINE_REQUEST).exists()
    assert TOKEN not in json.dumps(status)
    assert status["token_set"] is True


def test_a_job_that_is_not_the_latest_is_unknown(online):
    request(online)
    fetch(online)
    status = overview(online, "f" * 16)
    assert status["state"] == "unknown"
    assert overview(online)["state"] == "done"


def test_without_a_request_the_unit_does_nothing(online):
    assert fetch(online) is None
    assert online.github.requests == []
    (online.pi.root / updates.ONLINE_REQUEST).write_text(json.dumps({"job": "../x"}))
    assert fetch(online) is None and online.github.requests == []


def test_a_failed_check_is_recorded_with_its_reason(online):
    online.github.latest_status = 401
    request(online)
    status = overview(online, fetch(online)["job"])
    assert status["state"] == "done" and status["result"] == "failed"
    assert "refused the token" in status["message"]
    assert TOKEN not in json.dumps(status)


def test_a_second_request_within_ten_minutes_asks_github_nothing(online):
    request(online)
    fetch(online)
    count = len(online.github.requests)
    online.run_calls.clear()
    online.clock[0] += 599
    again = request(online)
    assert again["cached"] is True and again["state"] == "done" and again["job"] == "%016x" % 1
    assert online.run_calls == [] and len(online.github.requests) == count


def test_check_now_asks_again_even_within_ten_minutes(online):
    request(online)
    fetch(online)
    online.clock[0] += 5
    answer = request(online, force=True)
    assert answer["state"] == "running" and answer["cached"] is False
    assert answer["job"] == "%016x" % 2
    assert len(online.run_calls) == 2


def test_after_ten_minutes_a_request_starts_a_new_check(online):
    request(online)
    fetch(online)
    online.clock[0] += updates.CACHE_SECONDS + 1
    assert request(online)["state"] == "running"


def test_a_request_while_a_check_runs_joins_that_check(online):
    first = request(online)
    online.run_calls.clear()
    online.clock[0] += 30
    second = request(online, force=True)
    assert second["job"] == first["job"] and second["cached"] is True
    assert online.run_calls == []


def test_a_check_that_never_ended_shows_as_failed_and_can_be_repeated(online):
    request(online)
    online.clock[0] += updates.STALE_SECONDS + 1
    status = overview(online)
    assert status["state"] == "done" and status["result"] == "failed"
    assert "did not finish" in status["message"]
    assert request(online)["state"] == "running"


def test_a_failure_to_start_the_unit_is_recorded(online):
    def broken(command, timeout=0):
        raise updates.UpdateError("no systemd")

    answer = updates.request_online_check(
        root=online.pi.root, config=online.pi.config, run=broken, now=lambda: online.clock[0],
        job_id=lambda: "a" * 16,
    )
    assert answer["state"] == "done" and answer["result"] == "failed"
    assert not (online.pi.root / updates.ONLINE_REQUEST).exists()
    # Nothing reached GitHub so the next request is not answered from the cache.
    assert request(online)["state"] == "running"


def test_without_a_token_the_answer_is_immediate_and_nothing_is_started(online):
    updates.clear_token(config=online.pi.config)
    answer = request(online)
    assert answer["state"] == "done" and answer["result"] == "failed"
    assert "set-token" in answer["message"]
    assert online.run_calls == []
    updates.set_token(TOKEN, config=online.pi.config)
    assert request(online)["state"] == "running"


def test_the_settings_are_off_by_default_and_change_only_when_asked(online):
    root = online.pi.root
    assert updates.online_settings(root) == {"daily_check": False, "install_automatically": False}
    updates.set_online_settings({"daily_check": True}, root=root)
    assert updates.online_settings(root) == {"daily_check": True, "install_automatically": False}
    for bad in ({}, {"daily_check": "yes"}, {"other": True}, {"daily_check": 1}, None):
        with pytest.raises(updates.UpdateError):
            updates.set_online_settings(bad, root=root)
    assert updates.online_settings(root)["daily_check"] is True
    assert overview(online)["settings"] == {"daily_check": True, "install_automatically": False}


def test_the_daily_check_is_off_by_default(online):
    assert updates.scheduled_online_check(root=online.pi.root, config=online.pi.config,
                                          run=online.run, now=lambda: online.clock[0]) is False
    assert online.run_calls == []


def test_the_daily_check_starts_once_a_day_when_on(online):
    updates.set_online_settings({"daily_check": True}, root=online.pi.root)

    def tick():
        return updates.scheduled_online_check(root=online.pi.root, config=online.pi.config,
                                              run=online.run, now=lambda: online.clock[0])

    assert tick() is True
    assert tick() is False
    fetch(online)
    online.clock[0] += updates.DAILY_SECONDS - 60
    assert tick() is False
    online.clock[0] += 120
    assert tick() is True
    assert len(online.run_calls) == 2


def test_the_daily_check_needs_a_token(online):
    updates.set_online_settings({"daily_check": True}, root=online.pi.root)
    updates.clear_token(config=online.pi.config)
    assert updates.scheduled_online_check(root=online.pi.root, config=online.pi.config,
                                          run=online.run, now=lambda: online.clock[0]) is False
    assert not (online.pi.root / updates.ONLINE_FILE).exists()


def test_a_staged_release_is_not_installed_unless_install_automatically_is_on(online):
    request(online)
    record = fetch(online)
    assert record["install_queued"] is False
    assert not (online.pi.root / "request.json").exists()
    assert ["systemctl", "start", "--no-block", "jittersim-update.service"] not in online.run_calls


def test_install_automatically_queues_the_normal_install_with_its_recovery_copy(online):
    updates.set_online_settings({"install_automatically": True}, root=online.pi.root)
    request(online)
    record = fetch(online)
    assert record["install_queued"] is True and "install was started" in record["message"]
    queued = json.loads((online.pi.root / "request.json").read_text())
    assert queued["version"] == "0.7.0" and queued["rollback"] is False
    assert queued["request_kind"] == "install"
    assert len(queued["request_id"]) == 32
    assert updates.read_json(online.pi.root / "status.json")["request_id"] == queued["request_id"]
    assert ["systemctl", "start", "--no-block", "jittersim-update.service"] in online.run_calls
    assert overview(online)["install_queued"] is True


def test_install_automatically_does_not_install_a_refused_release(online):
    updates.set_online_settings({"install_automatically": True}, root=online.pi.root)
    online.github.files[11] = b"nonsense"
    request(online)
    assert fetch(online)["result"] == "failed"
    assert not (online.pi.root / "request.json").exists()


def test_install_automatically_tries_a_failed_release_only_once(online):
    updates.set_online_settings({"install_automatically": True}, root=online.pi.root)
    request(online)
    fetch(online)
    (online.pi.root / "request.json").unlink()
    online.clock[0] += updates.CACHE_SECONDS + 1
    request(online)
    record = fetch(online)
    assert record["install_queued"] is False
    assert "failed before" in record["message"]
    assert not (online.pi.root / "request.json").exists()


def test_the_command_line_check_never_installs_on_its_own(online):
    updates.set_online_settings({"install_automatically": True}, root=online.pi.root)
    record = updates.perform_online_check(
        "c" * 16, root=online.pi.root, code=online.pi.code, config=online.pi.config,
        source=online.source(), run=online.run, auto_install=False)
    assert record["result"] == "staged" and record["install_queued"] is False
    assert online.run_calls == []


def test_the_dashboard_files_never_hold_the_token(online):
    request(online)
    fetch(online)
    for path in online.pi.root.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes()

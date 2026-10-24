"""Exercise byte progression starts through the API and the real scheduler."""

import json
import socketserver
from unittest.mock import patch

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from tests.unit.test_api import CountingHelper


@pytest.fixture
def live_progressions():
    repository = ConfigRepository(":memory:")
    repository.save_profile(Profile("start", "Start", download=Impairment(delay_ms=100)))
    repository.save_profile(Profile("end", "End", download=Impairment(delay_ms=1000)))
    repository.save_device(Device("alice", "Alice", "192.168.50.10", "start"))
    repository.save_device(Device("bob", "Bob", "192.168.50.11", "start"))
    helper = CountingHelper(devices={"alice": 0, "bob": 0})
    scheduler = RuntimeScheduler(
        database_path=":memory:", helper_client=helper, poll_seconds=3600,
    )
    clock = [1000]
    # All counters and ticks are local and controlled; no listener or worker is needed.
    with patch.object(socketserver.TCPServer, "server_bind"), \
            patch.object(socketserver.TCPServer, "server_activate"), \
            patch.object(scheduler, "start"), patch("time.time", side_effect=lambda: clock[0]):
        server = ControlServer(("127.0.0.1", 0), repository,
                               helper_client=helper, scheduler=scheduler)
        try:
            yield server, repository, helper, scheduler, clock
        finally:
            server.server_close()
            repository.close()


def start_progression(server, run_id, device_id="alice"):
    status, _, body = server.write_response_for("/api/progressions", {
        "id": run_id, "device_id": device_id,
        "start_profile_id": "start", "end_profile_id": "end",
        "duration_minutes": 60, "mode": "bytes", "traffic_target_bytes": 1000,
    })
    response = json.loads(body)
    assert status == 200, response
    assert response["live_applied"] is True
    return response["progression"]


def download_delay(helper, device_id="alice"):
    return next(queue.impairment.delay_ms for queue in helper.applied_plan.queues
                if queue.device_id == device_id and queue.direction.value == "download")


def progression_payload(server, run_id):
    status, _, body = server.response_for("/api/progressions")
    assert status == 200
    return next(run for run in json.loads(body)["progressions"] if run["id"] == run_id)


@pytest.mark.parametrize("second_id", ["second", "first"])
def test_repeat_byte_progression_counts_only_its_own_traffic(live_progressions, second_id):
    server, repository, helper, scheduler, clock = live_progressions
    start_progression(server, "first")
    helper.devices["alice"] = 1000
    clock[0] += 6
    scheduler.tick(repository=repository, now=clock[0])
    assert download_delay(helper) == 1000

    # These bytes have not reached a scheduler tick when the next Start arrives.
    helper.devices["alice"] = 200
    response = start_progression(server, second_id)
    saved = next(run for run in repository.list_active_progressions(now=clock[0])
                 if run.id == second_id)
    assert saved.traffic_baseline_bytes == 1200
    assert response["traffic_baseline_bytes"] == 1200
    assert response["progress_percent"] == response["traffic_bytes"] == 0
    assert download_delay(helper) == 100

    # Every Apply resets CountingHelper's counters, matching a full plan replacement.
    for expected_percent, expected_delay in ((50, 550), (100, 1000)):
        helper.devices["alice"] = 500
        clock[0] += 6
        scheduler.tick(repository=repository, now=clock[0])
        current = progression_payload(server, second_id)
        assert current["progress_percent"] == expected_percent
        assert current["traffic_bytes"] == expected_percent * 10
        assert download_delay(helper) == expected_delay

    status, _, _ = server.write_response_for("/api/progressions/stop", {"id": second_id})
    assert status == 200
    assert not repository.list_active_progressions(now=clock[0])
    restarted = start_progression(server, "restarted")
    assert restarted["traffic_baseline_bytes"] == 2200
    assert restarted["progress_percent"] == restarted["traffic_bytes"] == 0
    assert download_delay(helper) == 100


def test_first_byte_progression_excludes_existing_unsampled_traffic(live_progressions):
    server, _, helper, _, _ = live_progressions
    helper.devices["alice"] = 400

    response = start_progression(server, "first")

    assert response["traffic_baseline_bytes"] == 400
    assert response["progress_percent"] == response["traffic_bytes"] == 0
    assert download_delay(helper) == 100


def test_byte_start_preserves_quota_usage_and_other_device_progress(live_progressions):
    server, repository, helper, scheduler, clock = live_progressions
    status, _, _ = server.write_response_for("/api/data-limits", {
        "device_id": "alice", "profile_id": "end", "limit_bytes": 100_000,
    })
    assert status == 200
    start_progression(server, "bob-run", "bob")
    helper.devices.update(alice=1000, bob=250)
    clock[0] += 6
    scheduler.tick(repository=repository, now=clock[0])
    assert repository.get_data_limit("alice").used_bytes == 1000
    assert download_delay(helper, "bob") == 325

    helper.devices["alice"] = 200
    response = start_progression(server, "alice-run")
    assert response["traffic_baseline_bytes"] == 1200
    assert response["progress_percent"] == response["traffic_bytes"] == 0
    assert repository.get_data_limit("alice").used_bytes == 1200
    assert download_delay(helper) == 100
    assert progression_payload(server, "bob-run")["progress_percent"] == 25
    assert download_delay(helper, "bob") == 325

    helper.devices.update(alice=500, bob=250)
    clock[0] += 6
    scheduler.tick(repository=repository, now=clock[0])
    assert repository.get_data_limit("alice").used_bytes == 1700
    assert progression_payload(server, "alice-run")["progress_percent"] == 50
    assert progression_payload(server, "bob-run")["progress_percent"] == 50
    assert download_delay(helper) == download_delay(helper, "bob") == 550

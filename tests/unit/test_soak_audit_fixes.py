"""Fixes of the soak and resource audit (docs/soak-and-resource-audit-2026-10-18.md).

1. The scheduler opened a new ConfigRepository on every tick. Opening one runs the schema
   check, the migrations and the history pruning, so the scheduler did all of that once a
   second for as long as a plan ran (about 14,000 empty commits and 230,000 schema statements
   an hour). It now keeps one connection.
2. The quota samplers stored the same counter again every 5 s when no traffic moved. Each such
   UPDATE is a journaled write with fsyncs on the SD card. An unchanged counter now writes
   nothing.
"""

import sqlite3
from unittest.mock import patch

import pytest

from jittersim.config.models import DataLimit, Device, DeviceGroup, GroupDataLimit, Profile, ServiceQuota
from jittersim.config.storage import ConfigRepository
from jittersim.engine import runtime
from jittersim.engine.runtime import RuntimeScheduler


class _Helper:
    def apply(self, *, plan):
        return {"commands_run": 1}

    def reset(self):
        return {"commands_run": 1}

    def traffic_counters(self):
        return {"devices": {}, "services": {}}


@pytest.fixture
def counted(monkeypatch):
    opened = []

    class Counting(ConfigRepository):
        def __init__(self, *args, **kwargs):
            opened.append(self)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(runtime, "ConfigRepository", Counting)
    return opened


def _scheduler(tmp_path):
    ConfigRepository(tmp_path / "config.db").close()
    scheduler = RuntimeScheduler(database_path=tmp_path / "config.db", helper_client=_Helper())
    # Running, like after an Apply, but without the loop thread: the test ticks by hand.
    scheduler._paused = False
    scheduler._current_payload = {}
    return scheduler


def test_ticks_share_one_connection(tmp_path, counted):
    scheduler = _scheduler(tmp_path)
    for second in range(5):
        scheduler.tick(now=1000 + second)
    assert len(counted) == 1


def test_a_tick_that_leaves_a_write_open_gives_up_its_connection(tmp_path, counted):
    scheduler = _scheduler(tmp_path)
    scheduler.tick(now=1000)
    first = counted[0]

    def half_written(*, repository, now, **_ignored):
        repository._connection.execute("DELETE FROM activity_log")
        raise RuntimeError("the tick failed after a write")

    with patch.object(scheduler, "_follow_device_addresses", half_written):
        with pytest.raises(RuntimeError):
            scheduler.tick(now=1001)
    with pytest.raises(sqlite3.ProgrammingError):
        first._connection.execute("SELECT 1")
    # The write lock went with it: the dashboard's own connection can write at once.
    dashboard = sqlite3.connect(str(tmp_path / "config.db"), timeout=0.2)
    with dashboard:
        dashboard.execute("DELETE FROM activity_log")
    dashboard.close()
    scheduler.tick(now=1002)
    assert len(counted) == 2


def test_stop_closes_the_connection(tmp_path, counted):
    scheduler = _scheduler(tmp_path)
    scheduler.tick(now=1000)
    scheduler.stop()
    assert scheduler._tick_repository is None
    with pytest.raises(sqlite3.ProgrammingError):
        counted[-1]._connection.execute("SELECT 1")



def test_stop_does_not_wait_for_a_manual_apply_without_bound(tmp_path, counted):
    import threading
    import time

    scheduler = _scheduler(tmp_path)
    scheduler.tick(now=1000)
    held, release = threading.Event(), threading.Event()

    def apply_in_progress():
        with scheduler.exclusive():
            held.set()
            release.wait(10)

    worker = threading.Thread(target=apply_in_progress)
    worker.start()
    try:
        assert held.wait(5)
        started = time.monotonic()
        scheduler.stop(timeout=0.3)
        assert time.monotonic() - started < 2
    finally:
        release.set()
        worker.join(5)

@pytest.fixture
def quotas(tmp_path):
    repository = ConfigRepository(tmp_path / "quota.db")
    repository.save_profile(Profile("slow", "Slow"))
    repository.save_device(Device("alice", "Phone", "192.168.50.184", "slow", "02:00:00:00:00:01"))
    repository.save_device_group(DeviceGroup("family", "Family", ("alice",)))
    repository.save_data_limit(DataLimit("alice", "slow", 10 ** 9))
    repository.save_group_data_limit(GroupDataLimit("family", "slow", 10 ** 9))
    repository.save_service_quota(ServiceQuota("video", "alice", "Video", ("video.example",), "slow", 10 ** 9))
    yield repository
    repository.close()


@pytest.mark.parametrize("record, key", [
    ("record_data_usage", "alice"), ("record_group_data_usage", "family"),
    ("record_service_usage", "video")])
def test_an_unchanged_counter_writes_nothing(quotas, record, key):
    first = getattr(quotas, record)(key, 5000)
    assert first.used_bytes == 5000
    changes = quotas._connection.total_changes
    again = getattr(quotas, record)(key, 5000)
    assert quotas._connection.total_changes == changes
    assert (again.used_bytes, again.counter_bytes) == (5000, 5000)
    # Traffic that moves, and a counter that started again from zero, are still stored.
    assert getattr(quotas, record)(key, 7000).used_bytes == 7000
    assert getattr(quotas, record)(key, 0).counter_bytes == 0
    assert getattr(quotas, record)(key, 300).used_bytes == 7300
    assert quotas._connection.total_changes == changes + 3


@pytest.mark.parametrize("module", [
    "jittersim.networking.browser_proxy", "jittersim.networking.adblocking"])
def test_long_running_services_do_not_load_requests(module):
    """3. Only the blocklist fetch child needs requests. Loading it in the browser proxy and the
    ad-blocking service cost each of them about 5 MB of Python heap for nothing."""

    import subprocess
    import sys
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "src"
    loaded = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", f"import sys, {module}; print('requests' in sys.modules)"],
        capture_output=True, text=True, check=True, env={"PYTHONPATH": str(source)})
    assert loaded.stdout.strip() == "False"

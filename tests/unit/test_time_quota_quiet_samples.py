"""A time quota stores a sample only when its usage changes (soak audit proposal 9).

The sample time of a quiet sample lives in the scheduler's memory. The database keeps the last
sample that changed something. These tests pin what is charged, with and without a restart.
"""

from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Profile, TimeQuota, time_quota_counter_id
from jittersim.config.storage import ConfigRepository
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.runtime import RuntimeScheduler

ALICE = "02:00:00:00:00:01"
QUOTA = "alice-video"


class Counters:
    """The helper's counters, as far as the quota sampler reads them."""

    def __init__(self):
        self.counters = {"devices": {}, "services": {}}

    def traffic_counters(self):
        return self.counters

    def set_quota_counter(self, value):
        self.counters["services"][time_quota_counter_id(QUOTA)] = value


def make_repository(path, *, last_sample_at=100, counter_bytes=10):
    repository = ConfigRepository(path)
    repository.save_profile(Profile("normal", "Normal"))
    repository.save_profile(Profile("slow", "Slow"))
    repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal", ALICE))
    repository.save_time_quota(TimeQuota(
        QUOTA, "alice", "Video", ("example.com",), "slow", 100_000,
        window_duration_seconds=100_000, used_seconds=0, counter_bytes=counter_bytes,
        last_sample_at=last_sample_at, last_reset_at=100, started_at=100, expires_at=100_000,
    ))
    return repository


def make_scheduler(repository, helper, now):
    scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
    plan = scheduler.build_plan(repository=repository, now=now)
    scheduler._current_payload = plan_to_payload(plan)
    return scheduler


def stored(repository):
    quota = repository.get_time_quota(QUOTA)
    return quota.used_seconds, quota.counter_bytes, quota.last_sample_at


def changes(repository):
    return repository._connection.total_changes


# The repository

def test_a_sample_without_new_traffic_stores_nothing():
    with make_repository(":memory:") as repository:
        before = changes(repository)
        for now in range(105, 400, 5):
            quota = repository.record_time_usage(
                QUOTA, 10, now, active=True, previous_sample_at=now - 5)
            assert quota.used_seconds == 0
        assert changes(repository) == before
        assert repository.get_time_quota(QUOTA).last_sample_at == 100


def test_the_remembered_sample_time_starts_the_elapsed_time():
    with make_repository(":memory:") as repository:
        quota = repository.record_time_usage(QUOTA, 50, 405, active=True, previous_sample_at=400)
        assert (quota.used_seconds, quota.counter_bytes, quota.last_sample_at) == (5, 50, 405)
        assert stored(repository) == (5, 50, 405)


def test_without_a_remembered_time_the_stored_one_is_used_and_the_charge_is_capped():
    with make_repository(":memory:") as repository:
        quota = repository.record_time_usage(QUOTA, 50, 405, active=True)
        assert quota.used_seconds == 60


def test_the_stored_time_wins_when_it_is_newer_than_the_remembered_one():
    # A start, a reset or an Apply stored a newer time after the last remembered sample.
    with make_repository(":memory:", last_sample_at=300) as repository:
        quota = repository.record_time_usage(QUOTA, 50, 305, active=True, previous_sample_at=200)
        assert quota.used_seconds == 5


def test_the_first_sample_of_a_new_quota_is_stored_as_its_baseline():
    # Nothing stored yet (last_sample_at 0) and the counter did not move.
    with make_repository(":memory:", last_sample_at=0, counter_bytes=0) as repository:
        quota = repository.record_time_usage(QUOTA, 0, 105, active=True, previous_sample_at=104)
        assert quota.used_seconds == 0
        assert stored(repository) == (0, 0, 105)


# The scheduler, one run

def test_a_quiet_device_costs_no_write_and_traffic_charges_from_the_last_sample(tmp_path):
    helper = Counters()
    with make_repository(tmp_path / "settings.db") as repository:
        helper.set_quota_counter(10)
        scheduler = make_scheduler(repository, helper, 100)
        before = changes(repository)
        for now in range(105, 305, 5):
            scheduler._sample_quota_usage(repository=repository, now=now)
        assert changes(repository) == before
        assert stored(repository) == (0, 10, 100)
        # The device wakes up: five seconds since the last sample are charged, like before.
        helper.set_quota_counter(50)
        scheduler._sample_quota_usage(repository=repository, now=305)
        assert stored(repository) == (5, 50, 305)


def test_traffic_in_every_sample_stores_every_sample(tmp_path):
    helper = Counters()
    with make_repository(tmp_path / "settings.db") as repository:
        scheduler = make_scheduler(repository, helper, 100)
        for step, now in enumerate(range(105, 130, 5), start=1):
            helper.set_quota_counter(10 + step * 10)
            scheduler._sample_quota_usage(repository=repository, now=now)
        assert stored(repository) == (25, 60, 125)


# A restart

def quiet_run_then_restart(database, helper):
    """One run with a quiet quota that then sees traffic. The stored sample stays at 1000."""

    with make_repository(database) as repository:
        helper.set_quota_counter(10)
        run = make_scheduler(repository, helper, 100)
        for now in range(105, 1_000, 5):
            run._sample_quota_usage(repository=repository, now=now)
        helper.set_quota_counter(50)
        run._sample_quota_usage(repository=repository, now=1_000)
        # The run remembered the last quiet sample: 5 s are charged, not 900.
        assert stored(repository) == (5, 50, 1_000)
        # The device goes quiet again. Nothing more is stored before the service stops.
        before = changes(repository)
        for now in range(1_005, 1_100, 5):
            run._sample_quota_usage(repository=repository, now=now)
        assert changes(repository) == before


def test_after_a_restart_quiet_samples_still_cost_no_write(tmp_path):
    database = tmp_path / "settings.db"
    helper = Counters()
    quiet_run_then_restart(database, helper)
    with ConfigRepository(database) as repository:
        assert stored(repository) == (5, 50, 1_000)
        run = make_scheduler(repository, helper, 1_200)
        before = changes(repository)
        for now in range(1_200, 1_500, 5):
            run._sample_quota_usage(repository=repository, now=now)
        assert changes(repository) == before
        assert stored(repository) == (5, 50, 1_000)


def test_after_a_restart_the_first_sample_with_traffic_charges_at_most_one_minute(tmp_path):
    database = tmp_path / "settings.db"
    helper = Counters()
    quiet_run_then_restart(database, helper)
    with ConfigRepository(database) as repository:
        run = make_scheduler(repository, helper, 1_400)
        # Traffic in the first sample after the restart. The run remembers nothing yet, so the
        # time counts from the last stored sample (400 s ago) and is capped at 60 s.
        helper.set_quota_counter(90)
        run._sample_quota_usage(repository=repository, now=1_400)
        assert stored(repository) == (65, 90, 1_400)
        # From here the new run remembers every sample again.
        helper.set_quota_counter(100)
        run._sample_quota_usage(repository=repository, now=1_405)
        assert stored(repository) == (70, 100, 1_405)


def test_after_a_restart_while_the_device_was_active_the_charge_is_the_same_as_before(tmp_path):
    database = tmp_path / "settings.db"
    helper = Counters()
    with make_repository(database) as repository:
        run = make_scheduler(repository, helper, 100)
        for step, now in enumerate(range(105, 130, 5), start=1):
            helper.set_quota_counter(10 + step * 10)
            run._sample_quota_usage(repository=repository, now=now)
        assert stored(repository) == (25, 60, 125)
    with ConfigRepository(database) as repository:
        run = make_scheduler(repository, helper, 150)
        helper.set_quota_counter(200)
        run._sample_quota_usage(repository=repository, now=150)
        # The service was away for 25 s: they are charged, as they were before this change.
        assert stored(repository) == (50, 200, 150)


# A device that is left out of the sampling (protected)

def test_a_protected_device_costs_no_write_and_is_charged_only_from_its_last_sample(tmp_path):
    helper = Counters()
    with make_repository(tmp_path / "settings.db") as repository:
        repository.save_unaffected_device(UnaffectedDevice(ALICE))
        helper.set_quota_counter(10)
        scheduler = make_scheduler(repository, helper, 100)
        before = changes(repository)
        for now in range(105, 305, 5):
            helper.set_quota_counter(10 + now)  # traffic that is not charged
            scheduler._sample_quota_usage(repository=repository, now=now)
        assert changes(repository) == before
        # Protection ends. The next sample counts on from the last one, not from the start.
        repository.delete_unaffected_device(ALICE)
        plan = scheduler.build_plan(repository=repository, now=305)
        scheduler._current_payload = plan_to_payload(plan)
        helper.set_quota_counter(1_000)
        scheduler._sample_quota_usage(repository=repository, now=305)
        assert stored(repository)[0] == 5

"""Regressions for independent events and changing service scopes."""

from dataclasses import replace
from ipaddress import IPv4Address
from unittest.mock import patch

import pytest

from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import (
    DataLimit, Device, Impairment, Profile, Randomness, ServiceAssignment, ServiceList,
    ServiceQuota, TimeQuota, ValidationError,
)
from jittersim.config.storage import ConfigRepository
from jittersim.engine.compiler import PlanError, compile_plan
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking.applier import ApplyError, NetworkApplier
from jittersim.networking.executor import CommandExecutionError
from jittersim.networking.helper import HelperClientError
from jittersim.networking.topology import Topology
from tests.unit.test_applier import RootQueueExecutor
from tests.unit.test_runtime import RecordingHelper


@pytest.fixture
def control():
    with ConfigRepository(":memory:") as repository:
        repository.save_profile(Profile("normal", "Normal"))
        helper = RecordingHelper()
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        scheduler.start = lambda: None
        scheduler.activate(applied_payload={})
        yield repository, helper, scheduler


@pytest.mark.parametrize("general", [False, True])
def test_outage_only_profiles_make_new_choices_without_packet_rerolls(control, general):
    repository, helper, scheduler = control
    repository.save_profile(Profile("outages", "Outages", random_seed=13,
        outage_interval_seconds=2, outage_probability=50, outage_duration_seconds=1))
    if general:
        repository.save_general_profile("outages")
    else:
        repository.save_device(Device("alice", "Alice", "192.168.50.10", "outages"))
    outcomes = []
    for now in range(0, 120, 3):
        scheduler.tick(repository=repository, now=now)
        plan = helper.plans[-1]
        outcomes.append(plan.general_profile.outage_active if general else bool(plan.blocked_device_addresses))
    assert set(outcomes) == {False, True}
    assert all(roll.roll_number == 0 and roll.outage_check_number > 20 for roll in scheduler._rolls.values())


def test_an_outage_cannot_extend_itself_and_has_a_recovery_interval(control):
    repository, helper, scheduler = control
    repository.save_profile(Profile("outages", "Outages", outage_interval_seconds=1,
        outage_probability=100, outage_duration_seconds=5))
    repository.save_device(Device("alice", "Alice", "192.168.50.10", "outages"))
    blocked = []
    for now in range(9):
        scheduler.tick(repository=repository, now=now)
        blocked.append(bool(helper.plans[-1].blocked_device_addresses))
    assert blocked == [False, True, True, True, True, True, False, True, True]


@pytest.mark.parametrize("scope", ["list", "general-list", "assignment", "bytes", "time"])
def test_service_profiles_change_and_recover_without_changing_the_base_device(control, scope):
    repository, helper, scheduler = control
    repository.save_profile(Profile("changing", "Changing", download=Impairment(delay_ms=100),
        reroll_interval_seconds=1, outage_interval_seconds=2,
        outage_probability=100, outage_duration_seconds=2))
    repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
    if "list" in scope:
        repository.save_service_list(ServiceList("video", "Video", "changing", services=("netflix",)))
        if scope == "general-list":
            repository.save_general_profile("normal")
    elif scope == "assignment":
        repository.save_service_assignment(ServiceAssignment("video", "alice", "Video", ("netflix.com",), "changing"))
    elif scope == "bytes":
        repository.save_service_quota(ServiceQuota("video", "alice", "Video", ("netflix.com",), "changing", 1, used_bytes=1))
    else:
        repository.save_time_quota(TimeQuota("video", "alice", "Video", ("netflix.com",), "changing", 60, used_seconds=60, started_at=1, expires_at=100))
    observed = []
    with patch("jittersim.engine.runtime.resolve_domains", return_value=(IPv4Address("8.8.8.8"),)):
        for now in (1, 3, 4, 5):
            scheduler.tick(repository=repository, now=now)
            plan = helper.plans[-1]
            rules = plan.service_list_rules or plan.service_assignment_rules or plan.service_quota_rules
            observed.append(100 if getattr(rules[0], "outage_active", False) else rules[0].download_impairment.loss_percent)
            base = [q for q in plan.queues if q.device_id == "alice"]
            assert all(q.impairment.loss_percent == 0 for q in base)
    assert observed == [0, 100, 100, 0]


def test_preview_does_not_draw_fresh_seeds_or_write_history_and_stop_starts_a_new_pattern(control):
    repository, helper, scheduler = control
    repository.save_profile(Profile("fresh", "Fresh", random_mode="fresh"))
    repository.save_device(Device("alice", "Alice", "192.168.50.10", "fresh"))
    with patch("jittersim.engine.runtime.secrets.randbits", side_effect=[123, 456]) as draw:
        scheduler.build_plan(repository=repository, now=0)
        scheduler.build_plan(repository=repository, now=0)
        draw.assert_not_called()
        assert repository.list_pattern_events(now=0) == ()
        scheduler.tick(repository=repository, now=0)
        scheduler.tick(repository=repository, now=1)
        assert draw.call_count == 1
        assert scheduler._rolls["alice"].seed == 123
        scheduler.pause()
        scheduler.activate(applied_payload={})
        scheduler.tick(repository=repository, now=2)
        assert scheduler._rolls["alice"].seed == 456
    assert {event["seed"] for event in repository.list_pattern_events(now=2)} == {"123", "456"}


def test_old_profiles_keep_the_zero_seed_and_new_modes_validate(control):
    repository, _, _ = control
    assert repository.get_profile("normal").random_mode == "repeatable"
    with pytest.raises(ValidationError):
        Profile("bad", "Bad", random_mode="fresh", random_seed=1)
    with pytest.raises(ValidationError):
        Profile("bad", "Bad", random_mode="unknown")


def test_prepared_session_records_its_seed_only_after_a_successful_tick(control):
    repository, helper, scheduler = control
    repository.save_profile(Profile("fresh", "Fresh", random_mode="fresh"))
    repository.save_device(Device("alice", "Alice", "192.168.50.10", "fresh"))
    with patch("jittersim.engine.runtime.secrets.randbits", return_value=42) as draw:
        scheduler.prepare_plan(repository=repository, now=1)
        assert repository.list_pattern_events(now=1) == ()
        assert scheduler.tick(repository=repository, now=2)
        assert draw.call_count == 1
    (event,) = repository.list_pattern_events(now=2)
    assert event["kind"] == "start" and event["seed"] == "42"
    assert len(helper.plans) == 1


def test_gateway_stop_does_not_restore_the_old_fresh_session(control):
    repository, helper, scheduler = control
    repository.save_profile(Profile("fresh", "Fresh", random_mode="fresh"))
    repository.save_device(Device("alice", "Alice", "192.168.50.10", "fresh"))
    with patch("jittersim.engine.runtime.secrets.randbits", return_value=42):
        assert scheduler.tick(repository=repository, now=1)
    helper.gateway_status = lambda: {"panic_stop_latched": True}
    assert not scheduler.tick(repository=repository, now=2)
    assert not scheduler.is_active and scheduler._rolls == {}
    helper.gateway_status = lambda: {}
    scheduler.activate(applied_payload={})
    with patch("jittersim.engine.runtime.secrets.randbits", return_value=84):
        assert scheduler.tick(repository=repository, now=3)
    assert scheduler._rolls["alice"].seed == 84


@pytest.mark.parametrize("scope", ["assignment", "bytes", "time"])
def test_protection_removes_each_private_service_session_history(control, scope):
    repository, _, scheduler = control
    repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal", "02:00:00:00:00:01"))
    if scope == "assignment":
        repository.save_service_assignment(ServiceAssignment("video", "alice", "Video", ("netflix.com",), "normal"))
    elif scope == "bytes":
        repository.save_service_quota(ServiceQuota("video", "alice", "Video", ("netflix.com",), "normal", 1, used_bytes=1))
    else:
        repository.save_time_quota(TimeQuota("video", "alice", "Video", ("netflix.com",), "normal", 60, used_seconds=60, started_at=1, expires_at=100))
    with patch("jittersim.engine.runtime.resolve_domains", return_value=(IPv4Address("8.8.8.8"),)):
        assert scheduler.tick(repository=repository, now=1)
    assert len(repository.list_pattern_events(now=1)) == 2
    repository.save_unaffected_device(UnaffectedDevice("02:00:00:00:00:01", "Alice", 1))
    assert repository.list_pattern_events(now=1) == ()


def test_failed_apply_keeps_the_last_committed_random_state(control):
    repository, helper, scheduler = control
    repository.save_profile(Profile("random", "Random", download=Impairment(
        delay_ms=100, delay_randomness=Randomness(variation_percent=100)), reroll_interval_seconds=1))
    repository.save_device(Device("alice", "Alice", "192.168.50.10", "random"))
    scheduler.tick(repository=repository, now=0)
    with patch.object(helper, "apply", side_effect=HelperClientError("failed")):
        assert not scheduler.tick(repository=repository, now=2)
    assert scheduler._rolls["alice"].roll_number == 0
    assert [event["kind"] for event in repository.list_pattern_events(now=2)] == ["start"]
    assert not scheduler.tick(repository=repository, now=2)
    assert scheduler.tick(repository=repository, now=3)
    assert scheduler._rolls["alice"].roll_number == 1


def test_service_website_rules_are_rejected_instead_of_silently_ignored(control):
    repository, _, scheduler = control
    repository.save_profile(Profile("restricted", "Restricted", website_mode="allow_only", website_domains=("netflix.com",)))
    repository.save_service_list(ServiceList("video", "Video", "restricted", services=("netflix",)))
    with pytest.raises(PlanError, match="website allowlist"):
        scheduler.build_plan(repository=repository, now=0)


def test_live_queue_changes_preserve_counters_but_duplication_uses_replacement():
    executor = RootQueueExecutor()
    applier = NetworkApplier(executor=executor, topology=Topology("eth0", "wlan0"))
    device = Device("alice", "Alice", "192.168.50.10", "slow")
    def plan(impairment):
        return compile_plan(devices=(device,), profiles={"slow": Profile("slow", "Slow", upload=impairment, download=impairment)})
    applier.apply(plan=plan(Impairment(delay_ms=100)))
    executor.commands.clear()
    report = applier.apply(plan=plan(Impairment(delay_ms=200, loss_percent=5)))
    assert report.counters_preserved
    assert [c.arguments[:2] for c in executor.commands[2:]] == [("qdisc", "change"), ("qdisc", "change")]
    executor.commands.clear()
    report = applier.apply(plan=plan(Impairment(delay_ms=200, duplicate_percent=1)))
    assert not report.counters_preserved
    assert any(c.arguments[:2] == ("qdisc", "del") for c in executor.commands)


def test_preserved_counters_are_not_counted_twice_after_live_and_manual_updates(control):
    repository, helper, scheduler = control
    repository.save_profile(Profile("normal", "Normal", download=Impairment(delay_ms=100)))
    repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
    repository.save_data_limit(DataLimit("alice", "normal", 10**9, last_reset_at=100))
    helper.counters = {"devices": {"alice": 0}, "services": {}}
    assert scheduler.tick(repository=repository, now=100)

    def live_apply(*, plan):
        helper.plans.append(plan)
        return {"counters_preserved": True}

    with patch.object(helper, "apply", side_effect=live_apply):
        helper.counters["devices"]["alice"] = 1000
        repository.save_profile(Profile("normal", "Normal", download=Impairment(delay_ms=200)))
        assert scheduler.tick(repository=repository, now=102)
        scheduler.activate(applied_payload=scheduler._current_payload, counters_preserved=True)
        helper.counters["devices"]["alice"] = 1600
        repository.save_profile(Profile("normal", "Normal", download=Impairment(delay_ms=300)))
        assert scheduler.tick(repository=repository, now=110)
    assert repository.get_data_limit("alice").used_bytes == 1600
    assert scheduler.device_traffic_bytes(device_id="alice") == 1600


def test_failed_live_change_runs_cleanup_and_next_apply_replaces_everything():
    class FailedChange(RootQueueExecutor):
        def run(self, command):
            if command.arguments[:2] == ("qdisc", "change"):
                raise CommandExecutionError("failed change")
            return super().run(command)
    executor = FailedChange()
    applier = NetworkApplier(executor=executor, topology=Topology("eth0", "wlan0"))
    device = Device("alice", "Alice", "192.168.50.10", "slow")
    first = compile_plan(devices=(device,), profiles={"slow": Profile("slow", "Slow", download=Impairment(delay_ms=100))})
    applier.apply(plan=first)
    changed = replace(first, queues=tuple(replace(q, impairment=Impairment(delay_ms=200)) if q.impairment.delay_ms else q for q in first.queues))
    with pytest.raises(ApplyError):
        applier.apply(plan=changed)
    assert applier._installed_plan is None
    assert not applier.apply(plan=changed).counters_preserved


def test_history_is_bounded_and_protection_removes_device_choices(control):
    repository, _, scheduler = control
    repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal", "02:00:00:00:00:01"))
    scheduler.tick(repository=repository, now=1)
    event = repository.list_pattern_events(now=1)[0]
    repository.save_pattern_events([dict(event, happened_at=2) for _ in range(1000)], now=2)
    assert repository._connection.execute("SELECT count(*) FROM pattern_events").fetchone()[0] == 1000
    repository.save_pattern_events([], now=8 * 86400)
    assert repository.list_pattern_events(now=8 * 86400) == ()
    repository.save_pattern_events([event], now=1)
    repository.save_unaffected_device(UnaffectedDevice("02:00:00:00:00:01", "Alice", 1))
    assert repository.list_pattern_events(now=1) == ()

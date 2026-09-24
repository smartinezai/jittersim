"""Background runtime for schedules, randomized rerolls and outages."""

import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Dict, Optional, Sequence, Union

from jittersim.config.models import (
    DataLimit,
    Device,
    DomainObservation,
    GroupDataLimit,
    Profile,
    ServiceQuota,
    TimeQuota,
    time_quota_counter_id,
)
from jittersim.config.storage import ConfigRepository, DatabasePath, StorageError
from jittersim.networking.helper import HelperClient, HelperClientError

from .codec import plan_to_payload
from .compiler import PlanError, compile_plan
from .plan import ServiceAssignmentRule, ServiceQuotaRule
from .progression import interpolate_profile, progression_factor
from .randomizer import (
    outage_duration_seconds,
    outage_interval_seconds,
    outage_is_selected,
    reroll_interval_seconds,
    reroll_profile,
    seeded_generator,
)
from .schedules import active_schedule
from .website import build_website_filters, resolve_domains

LOGGER = logging.getLogger(__name__)


@dataclass
class _DeviceRollState:
    """Mutable scheduler state for one device between ticks."""

    profile_id: str
    runtime_profile: Profile
    roll_number: int
    next_roll_at: float
    next_outage_check_at: float
    outage_until: float


class RuntimeScheduler:
    """Apply only changed scheduled plans while the dashboard is running."""

    def __init__(
        self,
        *,
        database_path: DatabasePath,
        helper_client: Optional[HelperClient],
        poll_seconds: float = 1.0,
    ) -> None:
        self._database_path = database_path
        self._helper_client = helper_client
        self._poll_seconds = poll_seconds
        self._rolls: Dict[str, _DeviceRollState] = {}
        self._current_payload = None
        self._paused = True
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_traffic_sample_at = 0.0
        self._last_quota_sample_at = 0.0
        self._last_device_counters: Dict[str, int] = {}
        self._device_traffic_bytes: Dict[str, int] = {}

    def start(self) -> None:
        """Start the scheduler loop when hardware control is available."""

        if self._helper_client is None:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="jittersim-scheduler",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop the loop and wait briefly for its current tick to finish."""

        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        self._thread = None

    def activate(self, *, applied_payload: object) -> None:
        """Resume scheduled work after a successful manual apply."""

        with self._lock:
            self._current_payload = applied_payload
            self._paused = False
            self._last_device_counters.clear()
            self._device_traffic_bytes.clear()
        self.start()

    def build_plan(
        self,
        *,
        repository: ConfigRepository,
        now: Optional[float] = None,
    ):
        """Build the current runtime plan, including timers and schedules."""

        current_time = time.time() if now is None else now
        return self._build_plan(repository=repository, now=current_time)

    def pause(self) -> None:
        """Pause scheduled reapplication after a manual network reset."""

        with self._lock:
            self._paused = True
            self._rolls.clear()
            self._current_payload = None
            self._last_device_counters.clear()
            self._device_traffic_bytes.clear()

    def device_traffic_bytes(self, *, device_id: str) -> int:
        """Return cumulative device bytes observed by the active scheduler."""

        with self._lock:
            return self._device_traffic_bytes.get(device_id, 0)

    def control_operation(self):
        """Share one lock with manual apply and reset operations."""

        return self._lock

    def tick(
        self,
        *,
        repository: Optional[ConfigRepository] = None,
        now: Optional[float] = None,
    ) -> bool:
        """Recompute and apply a changed runtime plan once."""

        with self._lock:
            return self._tick_under_lock(repository=repository, now=now)

    def _tick_under_lock(
        self,
        *,
        repository: Optional[ConfigRepository] = None,
        now: Optional[float] = None,
    ) -> bool:
        if self._paused or self._helper_client is None:
            return False
        current_time = time.time() if now is None else now
        owned_repository = repository is None
        active_repository = repository or ConfigRepository(self._database_path)
        try:
            self._sample_traffic_if_needed(repository=active_repository, now=current_time)
            self._sample_quota_usage_if_needed(repository=active_repository, now=current_time)
            plan = self._build_plan(repository=active_repository, now=current_time)
            payload = plan_to_payload(plan)
            if payload == self._current_payload:
                return False
            self._helper_client.apply(plan=plan)
            self._current_payload = payload
            active_repository.update_runtime_state(
                observed_state="applied",
                last_action="schedule",
                last_result="success",
                last_error=None,
            )
            return True
        except (HelperClientError, PlanError, StorageError, OSError):
            LOGGER.exception("scheduled network plan failed")
            try:
                active_repository.update_runtime_state(
                    observed_state="error",
                    last_action="schedule",
                    last_result="failure",
                    last_error="scheduled network plan failed",
                )
            except StorageError:
                LOGGER.exception("could not record scheduled network failure")
            return False
        finally:
            if owned_repository:
                active_repository.close()

    def _build_plan(self, *, repository: ConfigRepository, now: float):
        profiles = {profile.id: profile for profile in repository.list_profiles()}
        schedules = repository.list_schedules()
        active_progressions = {}
        for progression in repository.list_active_progressions(now=int(now)):
            active_progressions.setdefault(progression.device_id, progression)
        active_timers = {}
        for timer in repository.list_active_timers(now=int(now)):
            active_timers.setdefault(timer.device_id, timer)
        moment = datetime.fromtimestamp(now).replace(second=0, microsecond=0)
        runtime_devices = []
        runtime_profiles = {}
        blocked_addresses = []
        blocked_domains_by_device = {}
        active_device_ids = set()
        data_limits = {
            data_limit.device_id: data_limit
            for data_limit in repository.list_data_limits()
        }
        groups_by_device = {
            device_id: group
            for group in repository.list_device_groups()
            for device_id in group.device_ids
        }
        group_data_limits = {
            data_limit.group_id: data_limit
            for data_limit in repository.list_group_data_limits()
        }

        for device in repository.list_devices():
            group = groups_by_device.get(device.id)
            progression = active_progressions.get(device.id)
            timer = None if progression is not None else active_timers.get(device.id)
            schedule = None if timer is not None or progression is not None else active_schedule(
                schedules=schedules,
                device_id=device.id,
                moment=moment,
            )
            group_profile_id = group.profile_id if group is not None else None
            source_profile_id = (
                group_profile_id or device.profile_id
                if schedule is None
                else schedule.profile_id
            )
            selected_profile = profiles.get(source_profile_id)
            source_key = (
                f"group-profile:{group.id}"
                if group is not None and group_profile_id is not None and schedule is None
                else source_profile_id
            )
            if timer is not None:
                source_profile_id = timer.profile_id
                selected_profile = profiles.get(source_profile_id)
                source_key = f"timer:{timer.id}"
            elif progression is not None:
                start_profile = profiles.get(progression.start_profile_id)
                end_profile = profiles.get(progression.end_profile_id)
                if start_profile is None or end_profile is None:
                    raise PlanError(
                        f"progression {progression.id} references a missing profile"
                    )
                traffic_queries = repository.total_domain_queries(str(device.ipv4_address))
                factor = progression_factor(
                    progression=progression,
                    now=now,
                    traffic_queries=traffic_queries,
                    traffic_bytes=self._device_traffic_bytes.get(device.id, 0),
                )
                selected_profile = interpolate_profile(
                    start=start_profile,
                    end=end_profile,
                    factor=factor,
                )
                source_key = f"progression:{progression.id}"
                domain_count = int(len(progression.website_domains) * factor)
                if domain_count:
                    blocked_domains_by_device[device.id] = progression.website_domains[:domain_count]
            data_limit = data_limits.get(device.id)
            if data_limit is not None and data_limit.enabled and data_limit.used_bytes >= data_limit.limit_bytes:
                fallback_profile = profiles.get(data_limit.profile_id)
                if fallback_profile is None:
                    raise PlanError(
                        f"data limit for device {device.id} references a missing profile"
                    )
                selected_profile = fallback_profile
                source_key = f"data-limit:{device.id}"
            elif group is not None:
                group_data_limit = group_data_limits.get(group.id)
                if (
                    group_data_limit is not None
                    and group_data_limit.enabled
                    and group_data_limit.used_bytes >= group_data_limit.limit_bytes
                ):
                    fallback_profile = profiles.get(group_data_limit.profile_id)
                    if fallback_profile is None:
                        raise PlanError(
                            f"group data limit for {group.id} references a missing profile"
                        )
                    selected_profile = fallback_profile
                    source_key = f"group-data-limit:{group.id}"
            if selected_profile is None:
                raise PlanError(f"device {device.id} references missing profile {source_profile_id}")
            active_device_ids.add(device.id)
            roll = self._rolls.get(device.id)
            if roll is None or roll.profile_id != source_key:
                roll = _DeviceRollState(
                    profile_id=source_key,
                    runtime_profile=selected_profile,
                    roll_number=0,
                    next_roll_at=now + max(1, selected_profile.reroll_interval_seconds),
                    next_outage_check_at=now
                    + max(
                        1,
                        selected_profile.outage_interval_seconds
                        or selected_profile.reroll_interval_seconds,
                    ),
                    outage_until=0,
                )
                self._rolls[device.id] = roll

            if progression is not None:
                generator = seeded_generator(
                    profile=selected_profile,
                    device_id=device.id,
                    roll_number=roll.roll_number,
                )
                roll.runtime_profile = reroll_profile(
                    profile=selected_profile,
                    generator=generator,
                )

            if selected_profile.reroll_interval_seconds and now >= roll.next_roll_at:
                generator = seeded_generator(
                    profile=selected_profile,
                    device_id=device.id,
                    roll_number=roll.roll_number,
                )
                roll.runtime_profile = reroll_profile(
                    profile=selected_profile,
                    generator=generator,
                )
                roll.roll_number += 1
                roll.next_roll_at = now + max(
                    1,
                    reroll_interval_seconds(profile=selected_profile, generator=generator),
                )

            outage_interval = selected_profile.outage_interval_seconds or selected_profile.reroll_interval_seconds
            if outage_interval and now >= roll.next_outage_check_at:
                generator = seeded_generator(
                    profile=selected_profile,
                    device_id=device.id,
                    roll_number=100_000 + roll.roll_number,
                )
                if outage_is_selected(profile=selected_profile, generator=generator):
                    duration = outage_duration_seconds(
                        profile=selected_profile,
                        generator=generator,
                    )
                    roll.outage_until = now + duration if duration else now
                roll.next_outage_check_at = now + max(
                    1,
                    outage_interval_seconds(profile=selected_profile, generator=generator),
                )
            if roll.outage_until > now:
                blocked_addresses.append(device.ipv4_address)

            runtime_profile_id = f"runtime-{device.id}"
            runtime_profiles[runtime_profile_id] = roll.runtime_profile
            runtime_devices.append(replace(device, profile_id=runtime_profile_id))

        self._rolls = {
            device_id: roll
            for device_id, roll in self._rolls.items()
            if device_id in active_device_ids
        }
        global_blocked, website_filters = build_website_filters(
            devices=runtime_devices,
            profiles=runtime_profiles,
            global_policy=repository.get_website_policy(),
            blocked_domains_by_device=blocked_domains_by_device,
        )
        service_quota_rules = self._build_service_quota_rules(
            repository=repository,
            devices=runtime_devices,
            profiles=profiles,
            now=now,
        )
        service_assignment_rules = []
        devices_by_id = {device.id: device for device in runtime_devices}
        for assignment in repository.list_service_assignments():
            if not assignment.enabled:
                continue
            device = devices_by_id.get(assignment.device_id)
            profile = profiles.get(assignment.profile_id)
            if device is None or profile is None:
                raise PlanError(f"service assignment {assignment.id} has a missing reference")
            service_assignment_rules.append(
                ServiceAssignmentRule(
                    assignment_id=assignment.id,
                    device_id=device.id,
                    device_ip=device.ipv4_address,
                    domains=assignment.domains,
                    mark=0,
                    upload_impairment=profile.upload,
                    download_impairment=profile.download,
                )
            )
        return compile_plan(
            devices=runtime_devices,
            profiles=runtime_profiles,
            mac_policy=repository.get_mac_policy(),
            global_blocked_addresses=global_blocked,
            website_filters=website_filters,
            blocked_device_addresses=blocked_addresses,
            service_quota_rules=service_quota_rules,
            service_assignment_rules=service_assignment_rules,
        )

    def _build_service_quota_rules(
        self,
        *,
        repository: ConfigRepository,
        devices: Sequence[Device],
        profiles: Dict[str, Profile],
        now: float,
    ) -> tuple:
        """Resolve enabled service quotas into destination-aware traffic rules."""

        devices_by_id = {device.id: device for device in devices}
        rules = []
        for quota in repository.list_service_quotas():
            if not quota.enabled:
                continue
            device = devices_by_id.get(quota.device_id)
            fallback_profile = profiles.get(quota.profile_id)
            if device is None:
                LOGGER.warning("service quota %s references a missing device", quota.id)
                continue
            if fallback_profile is None:
                raise PlanError(
                    f"service quota {quota.id} references a missing profile"
                )
            destination_addresses = resolve_domains(domains=quota.domains)
            if not destination_addresses:
                LOGGER.warning(
                    "service quota %s has no currently resolvable observed domains",
                    quota.id,
                )
                continue
            rules.append(
                ServiceQuotaRule(
                    quota_id=quota.id,
                    device_id=device.id,
                    device_ip=device.ipv4_address,
                    destination_addresses=destination_addresses,
                    mark=0,
                    upload_impairment=fallback_profile.upload,
                    download_impairment=fallback_profile.download,
                    active=quota.used_bytes >= quota.limit_bytes,
                )
            )
        for quota in repository.list_time_quotas():
            if not quota.enabled:
                continue
            device = devices_by_id.get(quota.device_id)
            fallback_profile = profiles.get(quota.profile_id)
            if device is None:
                LOGGER.warning("time quota %s references a missing device", quota.id)
                continue
            if fallback_profile is None:
                raise PlanError(f"time quota {quota.id} references a missing profile")
            destination_addresses = resolve_domains(domains=quota.domains)
            if not destination_addresses:
                LOGGER.warning(
                    "time quota %s has no currently resolvable observed domains",
                    quota.id,
                )
                continue
            rules.append(
                ServiceQuotaRule(
                    quota_id=time_quota_counter_id(quota.id),
                    device_id=device.id,
                    device_ip=device.ipv4_address,
                    destination_addresses=destination_addresses,
                    mark=0,
                    upload_impairment=fallback_profile.upload,
                    download_impairment=fallback_profile.download,
                    active=(
                        self._time_quota_window_is_active(quota, now)
                        and quota.used_seconds >= quota.limit_seconds
                    ),
                )
            )
        return tuple(rules)

    def _sample_traffic_if_needed(self, *, repository: ConfigRepository, now: float) -> None:
        """Persist a bounded DNS sample when a traffic progression is active."""

        if now - self._last_traffic_sample_at < 5:
            return
        progressions = repository.list_active_progressions(now=int(now))
        if not any(progression.mode == "traffic" for progression in progressions):
            return
        observe_dns_queries = getattr(self._helper_client, "observe_dns_queries", None)
        if observe_dns_queries is None:
            return
        self._last_traffic_sample_at = now
        try:
            observations = observe_dns_queries()
            observed_at = int(now)
            repository.save_domain_observations(
                tuple(
                    DomainObservation(
                        ipv4_address=observation["ipv4_address"],
                        domain=observation["domain"],
                        query_count=observation["query_count"],
                        first_seen=observed_at,
                        last_seen=observed_at,
                    )
                    for observation in observations
                ),
                now=observed_at,
            )
        except (KeyError, TypeError, ValueError, StorageError, HelperClientError):
            LOGGER.warning("traffic progression sample failed", exc_info=True)

    def _sample_quota_usage_if_needed(
        self,
        *,
        repository: ConfigRepository,
        now: float,
    ) -> None:
        """Sample device and quota byte counters and perform due resets."""

        if now - self._last_quota_sample_at < 5:
            return
        data_limits = tuple(
            data_limit
            for data_limit in repository.list_data_limits()
            if data_limit.enabled
        )
        groups_by_id = {group.id: group for group in repository.list_device_groups()}
        group_data_limits = tuple(
            data_limit
            for data_limit in repository.list_group_data_limits()
            if data_limit.enabled
        )
        service_quotas = tuple(
            quota for quota in repository.list_service_quotas() if quota.enabled
        )
        time_quotas = tuple(
            quota for quota in repository.list_time_quotas() if quota.enabled
        )
        progressions = repository.list_active_progressions(now=int(now))
        has_byte_progression = any(progression.mode == "bytes" for progression in progressions)
        if (
            not data_limits
            and not group_data_limits
            and not service_quotas
            and not time_quotas
            and not has_byte_progression
        ):
            return
        get_traffic_counters = getattr(self._helper_client, "traffic_counters", None)
        if get_traffic_counters is None:
            return
        self._last_quota_sample_at = now
        try:
            counters = get_traffic_counters()
            device_counters = counters.get("devices", {})
            service_counters = counters.get("services", {})
            for device_id, counter in device_counters.items():
                previous = self._last_device_counters.get(device_id)
                if previous is None:
                    self._device_traffic_bytes[device_id] = counter
                elif counter >= previous:
                    self._device_traffic_bytes[device_id] = (
                        self._device_traffic_bytes.get(device_id, 0) + counter - previous
                    )
                self._last_device_counters[device_id] = counter
            for data_limit in data_limits:
                counter = device_counters.get(data_limit.device_id)
                if counter is not None:
                    repository.record_data_usage(data_limit.device_id, counter)
            for data_limit in group_data_limits:
                group = groups_by_id.get(data_limit.group_id)
                if group is None or any(
                    device_id not in device_counters for device_id in group.device_ids
                ):
                    continue
                aggregate_counter = sum(
                    device_counters[device_id] for device_id in group.device_ids
                )
                repository.record_group_data_usage(data_limit.group_id, aggregate_counter)
            for quota in service_quotas:
                counter = service_counters.get(quota.id)
                if counter is not None:
                    repository.record_service_usage(quota.id, counter)
            for quota in time_quotas:
                counter_id = time_quota_counter_id(quota.id)
                counter = service_counters.get(counter_id)
                if counter is not None:
                    refreshed = repository.get_time_quota(quota.id)
                    if refreshed is not None:
                        repository.record_time_usage(
                            quota.id,
                            counter,
                            int(now),
                            active=self._time_quota_window_is_active(refreshed, now),
                        )
            for data_limit in data_limits:
                refreshed = repository.get_data_limit(data_limit.device_id)
                if refreshed is not None and self._quota_reset_is_due(refreshed, now):
                    repository.reset_data_limit_usage(
                        refreshed.device_id,
                        counter_bytes=device_counters.get(refreshed.device_id),
                        reset_at=int(now),
                    )
            for data_limit in group_data_limits:
                refreshed = repository.get_group_data_limit(data_limit.group_id)
                group = groups_by_id.get(data_limit.group_id)
                aggregate_counter = None
                if group is not None and all(
                    device_id in device_counters for device_id in group.device_ids
                ):
                    aggregate_counter = sum(
                        device_counters[device_id] for device_id in group.device_ids
                    )
                if refreshed is not None and self._quota_reset_is_due(refreshed, now):
                    repository.reset_group_data_limit_usage(
                        refreshed.group_id,
                        counter_bytes=aggregate_counter,
                        reset_at=int(now),
                    )
            for quota in service_quotas:
                refreshed = repository.get_service_quota(quota.id)
                if refreshed is not None and self._quota_reset_is_due(refreshed, now):
                    repository.reset_service_quota_usage(
                        refreshed.id,
                        counter_bytes=service_counters.get(refreshed.id),
                        reset_at=int(now),
                    )
            for quota in time_quotas:
                refreshed = repository.get_time_quota(quota.id)
                if refreshed is not None and self._quota_reset_is_due(refreshed, now):
                    repository.reset_time_quota_usage(
                        refreshed.id,
                        counter_bytes=service_counters.get(time_quota_counter_id(refreshed.id)),
                        reset_at=int(now),
                    )
        except (AttributeError, KeyError, TypeError, ValueError, StorageError, HelperClientError):
            LOGGER.warning("quota traffic sample failed", exc_info=True)

    @staticmethod
    def _quota_reset_is_due(
        quota: Union[DataLimit, GroupDataLimit, ServiceQuota, TimeQuota],
        now: float,
    ) -> bool:
        """Return whether a quota crossed its configured reset boundary."""

        if quota.reset_mode == "interval":
            return now - quota.last_reset_at >= quota.reset_interval_seconds
        if quota.reset_mode != "daily":
            return False
        current = datetime.fromtimestamp(now)
        for day_offset in (0, -1):
            day = current + timedelta(days=day_offset)
            for minute in quota.reset_times:
                boundary = day.replace(
                    hour=minute // 60,
                    minute=minute % 60,
                    second=0,
                    microsecond=0,
                ).timestamp()
                if quota.last_reset_at < boundary <= now:
                    return True
        return False

    @staticmethod
    def _time_quota_window_is_active(quota: TimeQuota, now: float) -> bool:
        """Return whether a timed quota is inside its configured start window."""

        return quota.started_at > 0 and quota.started_at <= now < quota.expires_at

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_seconds):
            self.tick()

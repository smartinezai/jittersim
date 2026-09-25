"""Background runtime for schedules, randomized rerolls and outages."""

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Dict, Iterator, Optional, Sequence, Union

from jittersim.config.models import (
    DataLimit,
    Device,
    DomainObservation,
    GroupDataLimit,
    Profile,
    ServiceQuota,
    TimeQuota,
    ValidationError,
    time_quota_counter_id,
)
from jittersim.config.storage import ConfigRepository, DatabasePath, StorageError
from jittersim.networking.helper import HelperClient, HelperClientError

from .codec import PlanCodecError, plan_from_payload, plan_to_payload
from .compiler import PlanError, compile_plan
from .plan import (
    EVERYONE_ELSE_QUEUE_ID,
    EveryoneElseRule,
    NetworkPlan,
    ServiceAssignmentRule,
    ServiceQuotaRule,
)
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
    # The profile values this state was built from, so edits start a fresh roll.
    source_profiles: object
    runtime_profile: Profile
    roll_number: int
    next_roll_at: float
    next_outage_check_at: float
    outage_until: float
    outage_check_number: int = 0


class RuntimeScheduler:
    """Apply only changed scheduled plans while the dashboard is running."""

    # How often saved devices are matched to the addresses their MAC addresses use.
    address_check_seconds = 30.0

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
        # Held while a plan is built and applied, so pause() and manual applies
        # never interleave with a scheduled apply.
        self._apply_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_traffic_sample_at = 0.0
        self._last_address_check_at = 0.0
        self._last_quota_sample_at = 0.0
        self._last_device_counters: Dict[str, int] = {}
        self._device_traffic_bytes: Dict[str, int] = {}
        self._group_members: Dict[str, frozenset] = {}

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
            # The apply recreated the helper counters, so keep the device
            # totals and count on from zero.
            self._last_device_counters = dict.fromkeys(self._last_device_counters, 0)
        self.start()

    def build_plan(
        self,
        *,
        repository: ConfigRepository,
        now: Optional[float] = None,
    ):
        """Build the current runtime plan, including timers and schedules."""

        current_time = time.time() if now is None else now
        with self._apply_lock:
            return self._build_plan(repository=repository, now=current_time)

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Hold off scheduled applies while a manual apply runs."""

        with self._apply_lock:
            yield

    def pause(self) -> None:
        """Pause scheduled reapplication after a manual network reset."""

        # Wait for an in-flight scheduled apply so a reset cannot be undone by it.
        with self._apply_lock, self._lock:
            self._paused = True
            self._rolls.clear()
            self._current_payload = None
            self._last_device_counters.clear()
            self._device_traffic_bytes.clear()
            self._group_members.clear()

    def sample_usage(self, *, repository: ConfigRepository) -> None:
        """Record quota and device traffic before the helper counters restart."""

        with self._apply_lock:
            self._sample_quota_usage(repository=repository, now=time.time())

    def device_traffic_bytes(self, *, device_id: str) -> int:
        """Return cumulative device bytes observed by the active scheduler."""

        with self._lock:
            return self._device_traffic_bytes.get(device_id, 0)

    def applied_plan(self) -> Optional[NetworkPlan]:
        """Return the plan this scheduler last applied, if the network runs one."""

        with self._lock:
            payload = self._current_payload
        if payload is None:
            return None
        try:
            return plan_from_payload(payload)
        except PlanCodecError:
            LOGGER.warning("the applied plan could not be read back", exc_info=True)
            return None

    def applied_queue_bandwidth_limits(
        self,
    ) -> Optional[Dict[str, Dict[str, Optional[int]]]]:
        """Return bandwidth limits from the last successfully applied plan."""

        with self._lock:
            payload = self._current_payload
            if not isinstance(payload, dict):
                return None
            queues = payload.get("queues")
            if not isinstance(queues, list):
                return None
            limits: Dict[str, Dict[str, Optional[int]]] = {}
            seen_directions = set()
            for queue in queues:
                if not isinstance(queue, dict):
                    return None
                device_id = queue.get("device_id")
                direction = queue.get("direction")
                impairment = queue.get("impairment")
                if (
                    not isinstance(device_id, str)
                    or direction not in ("upload", "download")
                    or not isinstance(impairment, dict)
                ):
                    return None
                rate = impairment.get("bandwidth_kbit")
                if rate is not None and (
                    isinstance(rate, bool) or not isinstance(rate, int) or rate < 1
                ):
                    return None
                queue_key = (device_id, direction)
                if queue_key in seen_directions:
                    return None
                seen_directions.add(queue_key)
                device_limits = limits.setdefault(
                    device_id,
                    {"upload_kbit": None, "download_kbit": None},
                )
                field = f"{direction}_kbit"
                device_limits[field] = rate
            return limits

    def tick(
        self,
        *,
        repository: Optional[ConfigRepository] = None,
        now: Optional[float] = None,
    ) -> bool:
        """Recompute and apply a changed runtime plan once."""

        with self._apply_lock:
            with self._lock:
                if self._paused or self._helper_client is None:
                    return False
            return self._tick(repository=repository, now=now)

    def _tick(self, *, repository: Optional[ConfigRepository], now: Optional[float]) -> bool:
        current_time = time.time() if now is None else now
        owned_repository = repository is None
        active_repository = repository or ConfigRepository(self._database_path)
        try:
            if active_repository.get_runtime_state().last_action == "reset":
                self._honor_external_reset()
                return False
            self._follow_device_addresses(repository=active_repository, now=current_time)
            self._sample_traffic_if_needed(repository=active_repository, now=current_time)
            self._sample_quota_usage_if_needed(repository=active_repository, now=current_time)
            plan = self._build_plan(repository=active_repository, now=current_time)
            payload = plan_to_payload(plan)
            with self._lock:
                if payload == self._current_payload:
                    return False
            # Applying recreates the helper's counters at zero, so record the
            # traffic they hold first.
            self._sample_quota_usage(repository=active_repository, now=current_time)
            self._helper_client.apply(plan=plan)
            active_repository.restart_usage_counters()
            with self._lock:
                self._current_payload = payload
                self._last_device_counters = dict.fromkeys(self._last_device_counters, 0)
            if not active_repository.record_schedule_result(success=True):
                # A reset was recorded while this plan was being applied.
                self._honor_external_reset()
                return False
            return True
        except (HelperClientError, PlanError, StorageError, OSError):
            LOGGER.exception("scheduled network plan failed")
            try:
                active_repository.record_schedule_result(success=False)
            except StorageError:
                LOGGER.exception("could not record scheduled network failure")
            return False
        finally:
            if owned_repository:
                active_repository.close()

    def _follow_device_addresses(self, *, repository: ConfigRepository, now: float) -> None:
        """Move saved devices to the addresses their MAC addresses use now.

        DHCP can hand a device a new address, and a saved device is matched by
        its address, so its profile would silently stop applying.
        """

        if now - self._last_address_check_at < self.address_check_seconds:
            return
        self._last_address_check_at = now
        read_clients = getattr(self._helper_client, "connected_device_snapshot", None)
        if read_clients is None:
            return
        try:
            snapshot = read_clients()
        except HelperClientError:
            LOGGER.warning("could not check which addresses saved devices use", exc_info=True)
            return
        if not snapshot.get("association_status_available"):
            return
        current = {
            client["mac_address"]: client["ipv4_address"]
            for client in snapshot.get("devices", [])
            if client.get("association_verified")
            and client.get("mac_address")
            and client.get("ipv4_address")
        }
        devices = repository.list_devices()
        holders = {str(device.ipv4_address): device.id for device in devices}
        for device in devices:
            address = current.get(device.mac_address) if device.mac_address else None
            if address is None or address == str(device.ipv4_address):
                continue
            holder = holders.get(address)
            if holder is not None:
                LOGGER.warning(
                    "device %s now uses %s, which saved device %s holds; not moving it",
                    device.id,
                    address,
                    holder,
                )
                continue
            try:
                moved = replace(device, ipv4_address=address)
            except ValidationError:
                continue
            repository.save_device(moved)
            holders.pop(str(device.ipv4_address), None)
            holders[address] = device.id
            LOGGER.info("device %s moved from %s to %s", device.id, device.ipv4_address, address)

    def _honor_external_reset(self) -> None:
        # Someone reset the network outside the dashboard, for example with
        # `jittersim reset`. Stop applying, and reset once more in case a
        # scheduled apply raced that reset.
        self.pause()
        try:
            self._helper_client.reset()
        except HelperClientError:
            LOGGER.warning("could not repeat an external network reset", exc_info=True)

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
            schedule = None
            if timer is None and progression is None:
                # A device's own schedule wins over its group's.
                schedule = active_schedule(schedules=schedules, device_id=device.id, moment=moment)
                if schedule is None and group is not None:
                    schedule = active_schedule(
                        schedules=schedules, group_id=group.id, moment=moment
                    )
            group_profile_id = group.profile_id if group is not None else None
            source_profile_id = (
                group_profile_id or device.profile_id
                if schedule is None
                else schedule.profile_id
            )
            selected_profile = profiles.get(source_profile_id)
            source_profiles = selected_profile
            source_key = (
                f"group-profile:{group.id}"
                if group is not None and group_profile_id is not None and schedule is None
                else source_profile_id
            )
            if timer is not None:
                source_profile_id = timer.profile_id
                selected_profile = profiles.get(source_profile_id)
                source_profiles = selected_profile
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
                # The interpolated profile changes as the progression advances,
                # so only edits to its start or end profile restart the roll.
                source_profiles = (start_profile, end_profile)
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
                source_profiles = fallback_profile
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
                    source_profiles = fallback_profile
                    source_key = f"group-data-limit:{group.id}"
            if selected_profile is None:
                raise PlanError(f"device {device.id} references missing profile {source_profile_id}")
            active_device_ids.add(device.id)
            roll = self._roll(
                key=device.id,
                selected_profile=selected_profile,
                source_key=source_key,
                source_profiles=source_profiles,
                now=now,
                reroll_each_tick=progression is not None,
            )
            if roll.outage_until > now:
                blocked_addresses.append(device.ipv4_address)

            # Device ids may already use the full identifier length, so number
            # the runtime profiles instead of prefixing the device id.
            runtime_profile_id = f"runtime-{len(runtime_devices) + 1}"
            runtime_profiles[runtime_profile_id] = roll.runtime_profile
            runtime_devices.append(replace(device, profile_id=runtime_profile_id))

        everyone_else = None
        everyone_else_schedule = active_schedule(
            schedules=schedules, everyone_else=True, moment=moment
        )
        # A schedule can give everyone else a profile even when none is saved.
        everyone_else_profile_id = (
            everyone_else_schedule.profile_id
            if everyone_else_schedule is not None
            else repository.get_everyone_else_profile_id()
        )
        if everyone_else_profile_id is not None:
            profile = profiles.get(everyone_else_profile_id)
            if profile is None:
                raise PlanError(
                    f"everyone else references missing profile {everyone_else_profile_id}"
                )
            active_device_ids.add(EVERYONE_ELSE_QUEUE_ID)
            roll = self._roll(
                key=EVERYONE_ELSE_QUEUE_ID,
                selected_profile=profile,
                source_key=profile.id,
                source_profiles=profile,
                now=now,
            )
            everyone_else = EveryoneElseRule(
                mark=0,
                upload_impairment=roll.runtime_profile.upload,
                download_impairment=roll.runtime_profile.download,
                unaffected_macs=tuple(
                    unaffected.mac_address for unaffected in repository.list_unaffected_devices()
                ),
                blocked=roll.outage_until > now,
            )

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
            everyone_else=everyone_else,
        )

    def _roll(
        self,
        *,
        key: str,
        selected_profile: Profile,
        source_key: str,
        source_profiles: object,
        now: float,
        reroll_each_tick: bool = False,
    ) -> _DeviceRollState:
        """Advance the rerolls and outage checks kept under one key."""

        roll = self._rolls.get(key)
        if (
            roll is None
            or roll.profile_id != source_key
            or roll.source_profiles != source_profiles
        ):
            roll = _DeviceRollState(
                profile_id=source_key,
                source_profiles=source_profiles,
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
            self._rolls[key] = roll

        if reroll_each_tick:
            generator = seeded_generator(
                profile=selected_profile,
                device_id=key,
                roll_number=roll.roll_number,
            )
            roll.runtime_profile = reroll_profile(
                profile=selected_profile,
                generator=generator,
            )

        if selected_profile.reroll_interval_seconds and now >= roll.next_roll_at:
            generator = seeded_generator(
                profile=selected_profile,
                device_id=key,
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

        outage_interval = (
            selected_profile.outage_interval_seconds or selected_profile.reroll_interval_seconds
        )
        if outage_interval and now >= roll.next_outage_check_at:
            # Count outage checks separately: the reroll number stays at 0
            # when rerolls are off, which would repeat the same outcome.
            generator = seeded_generator(
                profile=selected_profile,
                device_id=key,
                roll_number=100_000 + roll.outage_check_number,
            )
            roll.outage_check_number += 1
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
        return roll

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
        self._sample_quota_usage(repository=repository, now=now)

    def _sample_quota_usage(self, *, repository: ConfigRepository, now: float) -> None:
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
                members = frozenset(group.device_ids)
                previous_members = self._group_members.get(data_limit.group_id)
                self._group_members[data_limit.group_id] = members
                if previous_members is not None and previous_members != members:
                    # New or removed members change the summed counter without
                    # any new group traffic, so count on from the new sum.
                    repository.rebaseline_group_data_usage(data_limit.group_id, aggregate_counter)
                    continue
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
            try:
                self.tick()
            except Exception:
                # Keep schedules, timers, outages and quotas running; the next
                # tick retries with fresh state.
                LOGGER.exception("scheduler tick failed unexpectedly")

"""Background runtime for schedules, randomized rerolls and outages."""

import logging
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

from jittersim.diagnostic_records import Operation
from jittersim.config.device_names import saved_device_title
from jittersim.config.models import (
    DataLimit,
    Device,
    DomainObservation,
    GroupDataLimit,
    Profile,
    RandomWebsiteOutcome,
    ServiceList,
    ServiceQuota,
    TimeQuota,
    ValidationError,
    time_quota_counter_id,
)
from jittersim.config.protection import (
    observation_is_protected,
    protected_addresses,
    protected_macs,
    station_macs_by_address,
)
from jittersim.config.storage import (
    MAX_RANDOM_WEBSITE_ANALYTICS_BATCH,
    ConfigRepository,
    DatabasePath,
    StorageError,
)
from jittersim.networking.helper import HelperClient, HelperClientError
from jittersim.request_workers import WorkersStillRunning
from jittersim.router_canary import RouterCanary

from .access import build_access_plan
from .activity import Source, TargetState, activity_records
from .codec import PlanCodecError, plan_from_payload, plan_to_payload
from .compiler import PlanError, compile_plan
from .eligibility import eligible_clients, station_snapshot
from .general import compile_general_plan
from .plan import (
    EVERYONE_ELSE_QUEUE_ID,
    GENERAL_QUEUE_ID,
    SERVICE_LIST_QUEUE_PREFIX,
    EveryoneElseRule,
    GeneralProfileRule,
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
from .scenarios import active_scenario_runs
from .schedules import active_schedule, next_schedule_change
from .selection import choose_automation, reached_limit
from .service_lists import build_service_list_rules
from .service_resolver import ServiceAddressRefresher
from .website import build_website_filters, lookup_session, resolve_domains

LOGGER = logging.getLogger(__name__)


def device_titles(repository: ConfigRepository, devices: Sequence[Device]) -> Dict[str, str]:
    """Name saved devices in the activity log the way the dashboard names them.

    A device saved from the connected list is called "Client <ip>" until someone
    names it, so its saved label comes first. A name is only wording: when the
    labels cannot be read the saved names are used.
    """

    try:
        labels = repository.list_connected_device_labels()
    except StorageError:
        LOGGER.warning("could not read device labels for the activity log", exc_info=True)
        labels = {}
    return {
        device.id: saved_device_title(
            saved_label=labels.get(device.mac_address or "", {}).get("display_name"),
            saved_name=device.name,
            ipv4_address=device.ipv4_address,
        )
        for device in devices
    }


def plan_address_moves(
    devices: Sequence[Device], seen: Mapping[str, str]
) -> Tuple[Dict[str, Tuple[str, str]], Dict[str, Tuple[str, str]]]:
    """Choose which saved devices move to the address their MAC address uses now.

    `seen` maps MAC addresses to confirmed client addresses. Returns the moves
    as device ID to (old, new) address, and the blocked moves as device ID to
    (address, the device that keeps it). A device moves only when no saved
    device keeps that address, so devices that swap or pass addresses on move
    together.
    """

    current = {device.id: str(device.ipv4_address) for device in devices}
    wanted = {}
    for device in devices:
        address = seen.get(device.mac_address) if device.mac_address else None
        if address is None or address == current[device.id]:
            continue
        try:
            replace(device, ipv4_address=address)
        except ValidationError:
            # For example a link-local address, which a saved device can't use.
            continue
        wanted[device.id] = address
    blocked = {}
    while True:
        claims: Dict[str, list] = {}
        for device_id, address in current.items():
            claims.setdefault(wanted.get(device_id, address), []).append(device_id)
        stopped = False
        for address, claimants in claims.items():
            if len(claimants) < 2:
                continue
            keepers = [device_id for device_id in claimants if device_id not in wanted]
            for device_id in claimants:
                if device_id in wanted:
                    others = keepers or [other for other in claimants if other != device_id]
                    blocked[device_id] = (address, others[0])
                    del wanted[device_id]
                    stopped = True
        # A stopped device keeps its old address, which another move may want.
        if not stopped:
            break
    moves = {device_id: (current[device_id], address) for device_id, address in wanted.items()}
    return moves, blocked


@dataclass(frozen=True)
class PreparedRuntimePlan:
    """Network rules and their source descriptions from one evaluation."""

    plan: NetworkPlan
    states: Dict[str, TargetState]
    # Website names whose lookup missed the deadline, so the plan leaves them out.
    unresolved_names: Tuple[str, ...] = ()
    # The saved configuration version read before the plan was built. None when unknown.
    configuration_version: Optional[int] = None


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
    seed: int = 0
    session_id: str = ""
    started_at: float = 0.0
    history_started: bool = False
    random_mode: str = "repeatable"


def _general_profile_runs(repository: ConfigRepository) -> bool:
    """A saved Everyone profile stays paused while a device-only start runs."""

    return (repository.get_general_profile() is not None
            and repository.get_device_start_scope() is None)


def _only_everyone_runs(repository: ConfigRepository) -> bool:
    """Everyone covers every client and no device runs its own profile beside it."""

    return _general_profile_runs(repository) and not repository.get_everyone_exceptions()


class RuntimeScheduler:
    """Apply only changed scheduled plans while the dashboard is running."""

    # How often saved devices are matched to the addresses their MAC addresses use.
    address_check_seconds = 30.0
    # A plan that fails is tried again after 1 s then 2 4 8 and so on up to 30 s. A saved
    # change or a manual apply starts again at once and a success clears the wait.
    retry_base_seconds = 1.0
    retry_max_seconds = 30.0
    # Tries to reset the helper after a device-only start ended before giving up.
    cleanup_attempts = 8

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
        self._preview = False
        self._pending_events: List[dict] = []
        self._last_pattern_cleanup_at = 0.0
        self._current_payload = None
        # The saved configuration version the applied plan was built from. It counts only
        # while `_current_payload` is set.
        self._applied_version: Optional[int] = None
        self._paused = True
        # Counts Panic requests so an apply that was running meanwhile can tell.
        self._halt_count = 0
        self._closed = False
        self._lock = threading.RLock()
        # Held while a plan is built and applied, so pause() and manual applies
        # never interleave with a scheduled apply.
        self._apply_lock = threading.RLock()
        self._apply_intent = threading.local()
        self._recoveries = set()
        self._recovery_required = False
        self._recovery_failures = 0
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_traffic_sample_at = 0.0
        self._last_traffic_sample_error: Optional[str] = None
        self._last_address_check_at = 0.0
        # Blocked moves already logged, so each is logged once.
        self._blocked_moves: Dict[str, Tuple[str, str]] = {}
        # What applied to each device and to everyone else at the last applied
        # plan, for the activity log. None until a plan applies after a reset.
        self._applied_states: Optional[Dict[str, TargetState]] = None
        self._schedule_hint_settings = None
        self._schedule_hints = {}
        self._last_quota_sample_at = 0.0
        self._last_analytics_cleanup_at = 0.0
        self._analytics_batch_overflowed = False
        self._last_device_counters: Dict[str, int] = {}
        self._device_traffic_bytes: Dict[str, int] = {}
        self._group_members: Dict[str, frozenset] = {}
        # The wait before a failed plan is tried again, guarded by `_lock`. The saved
        # configuration version at the failure tells a saved change from a plain retry.
        self._plan_failures = 0
        self._plan_retry_at = 0.0
        self._plan_failure_version: Optional[int] = None
        # The website names the last built plan left out because their lookup was too slow.
        self._unresolved_names: Tuple[str, ...] = ()
        # The last reason logged for each kind of failure, so a repeat is not logged again.
        self._reported_failures: Dict[str, Tuple[str, str]] = {}
        # Read-only router health checks for the persistent outage log. Only
        # with hardware control, like the other workers.
        self._router_canary = (
            None if helper_client is None else RouterCanary(profiles=self._active_profile_names)
        )
        self._service_addresses = (
            None
            if helper_client is None
            else ServiceAddressRefresher(
                applied_plan=self._applied_payload, helper_client=helper_client
            )
        )

    def _applied_payload(self):
        with self._lock:
            return self._current_payload

    def start(self) -> None:
        """Start the scheduler loop when hardware control is available."""

        if self._helper_client is None:
            return
        with self._lock:
            if self._closed:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            try:
                self._thread = threading.Thread(
                    target=self._run, name="jittersim-scheduler", daemon=True,
                )
                self._thread.start()
            except BaseException:
                self._closed = True
                self._paused = True
                self._stop_event.set()
                raise
            if self._service_addresses is not None:
                self._service_addresses.start()
            if self._router_canary is not None:
                self._router_canary.start()

    def _active_profile_names(self):
        """Name the profiles of the last applied plan for the router canary's records."""

        with self._lock:
            states = dict(self._applied_states or {})
        return [state.source.profile for state in states.values() if state.source.profile]

    def begin_shutdown(self) -> None:
        """Permanently stop admission before draining any shared-state owner."""

        with self._lock:
            self._closed = True
            self._paused = True
            self._stop_event.set()
        if self._service_addresses is not None:
            self._service_addresses.begin_shutdown()
        if self._router_canary is not None:
            self._router_canary.begin_shutdown()

    def stop(self, timeout: float = 5.0) -> None:
        """Drain actual workers or keep their handles and report incomplete shutdown."""

        self.begin_shutdown()
        deadline = time.monotonic() + timeout
        unresolved = False
        thread = self._thread
        if thread is not None:
            try:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
                unresolved = thread.is_alive()
            except RuntimeError:
                unresolved = True
            if not unresolved:
                self._thread = None
        if self._service_addresses is not None:
            try:
                self._service_addresses.stop(timeout=max(0.0, deadline - time.monotonic()))
            except WorkersStillRunning:
                unresolved = True
        if self._router_canary is not None:
            self._router_canary.stop(timeout=min(1.0, max(0.0, deadline - time.monotonic())))
        if unresolved:
            raise WorkersStillRunning("Scheduler workers did not stop. Their state remains owned.")

    def activate(self, *, applied_payload: object, counters_preserved: bool = False,
                 configuration_version: Optional[int] = None) -> None:
        """Resume scheduled work after a successful manual apply."""

        with self._lock:
            if not self.can_apply(self.apply_generation):
                return
            self._current_payload = applied_payload
            self._applied_version = configuration_version
            self._paused = False
            # A replacement restarts the counters. Keep totals and count on from zero.
            if not counters_preserved:
                self._last_device_counters = dict.fromkeys(self._last_device_counters, 0)
            # The apply worked, so the next scheduled change does not wait for a retry.
            self._clear_plan_failure()
        self.start()

    def invalidate_applied_plan(self) -> None:
        """Forget a plan the helper may have removed while handling a failure."""

        with self._lock:
            self._current_payload = None

    def activate_plan(
        self, *, repository: ConfigRepository, prepared: PreparedRuntimePlan,
        counters_preserved: bool = False,
    ) -> None:
        """Record a successful manual apply before starting background work."""

        with self._apply_lock:
            self._record_activity(repository=repository, states=prepared.states, now=time.time())
            self._save_patterns(repository, time.time())
            self.activate(applied_payload=plan_to_payload(prepared.plan),
                          counters_preserved=counters_preserved,
                          configuration_version=prepared.configuration_version)

    def build_plan(
        self,
        *,
        repository: ConfigRepository,
        now: Optional[float] = None,
        connected_snapshot: Optional[dict] = None,
        device_scope: Optional[frozenset] = None,
        everyone_exceptions: Optional[frozenset] = None,
    ) -> NetworkPlan:
        """Preview the current runtime plan without advancing rerolls or outages.

        The scheduler and `prepare_plan` own the roll state. A preview copies it
        so that reading the plan never changes what the next tick applies.
        """

        current_time = time.time() if now is None else now
        with self._apply_lock:
            previous_rolls = self._rolls
            previous_preview = self._preview
            self._preview = True
            self._rolls = {key: replace(roll) for key, roll in previous_rolls.items()}
            try:
                plan, _ = self._build_plan(
                    repository=repository, now=current_time, connected_snapshot=connected_snapshot,
                    device_scope=device_scope, everyone_exceptions=everyone_exceptions,
                )
                return plan
            finally:
                self._rolls = previous_rolls
                self._preview = previous_preview

    def prepare_plan(
        self,
        *,
        repository: ConfigRepository,
        now: Optional[float] = None,
        connected_snapshot: Optional[dict] = None,
        device_scope: Optional[frozenset] = None,
        everyone_exceptions: Optional[frozenset] = None,
    ) -> PreparedRuntimePlan:
        """Keep source descriptions paired with the exact plan being applied.

        `device_scope` limits the plan to a device-only start. Without it the
        whole saved configuration runs. While Everyone runs, `everyone_exceptions`
        names the devices that run their own profile beside it: None reads the saved
        ones and an empty set is the whole-scope plan.
        """

        current_time = time.time() if now is None else now
        with self._apply_lock:
            # Read before building: a write that lands meanwhile makes the version look older
            # than the plan, never newer.
            version = repository.get_runtime_state().configuration_version
            plan, states = self._build_plan(
                repository=repository, now=current_time, connected_snapshot=connected_snapshot,
                device_scope=device_scope, everyone_exceptions=everyone_exceptions,
            )
            return PreparedRuntimePlan(
                plan=plan, states=states, unresolved_names=self._unresolved_names,
                configuration_version=version,
            )

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Keep the original intent while waiting for earlier manual work."""

        outer = getattr(self._apply_intent, "generation", None)
        generation = self.halt_count if outer is None else outer
        self._apply_intent.generation = generation
        try:
            with self._apply_lock:
                yield
        finally:
            if outer is None:
                del self._apply_intent.generation
            else:
                self._apply_intent.generation = outer

    @property
    def apply_generation(self) -> int:
        return getattr(self._apply_intent, "generation", self.halt_count)

    @property
    def shutting_down(self) -> bool:
        with self._lock:
            return self._closed

    def can_apply(self, generation: int) -> bool:
        with self._lock:
            return (not self._closed and generation == self._halt_count
                    and not self._recoveries and not self._recovery_required)

    def publish_if_current(self, generation: int, publish):
        """Publish a short local result under the same fence that Panic changes."""

        with self._lock:
            if not self.can_apply(generation):
                return False, None
            value = publish()
            return self.can_apply(generation), value

    def require_recovery(self) -> None:
        with self._lock:
            self._recovery_required = True
            self._paused = True
            self._current_payload = None

    def begin_recovery(self, *, cancel: bool = True):
        """Block new work until emergency cleanup has a known saved result."""

        with self._lock:
            if cancel:
                self.halt()
            ticket = (self._halt_count, object(), self._recovery_failures)
            self._recoveries.add(ticket)
            self._recovery_required = True
            self._paused = True
            self._current_payload = None
            return ticket

    def publish_recovery(self, ticket, publish):
        """Keep older initial writes from replacing a newer recovery result."""

        with self._lock:
            if ticket[0] != self._halt_count:
                return False, None
            return True, publish()

    def abandon_recovery(self, ticket) -> None:
        with self._lock:
            self._recovery_failures += 1
            self._recovery_required = True
            self._recoveries.discard(ticket)

    def finish_recovery(self, ticket, publish, *, failed: bool = False):
        """Ignore older recovery results while retaining every pending owner."""

        with self._lock:
            try:
                if ticket[0] != self._halt_count:
                    if failed:
                        self._recovery_failures += 1
                        self._recovery_required = True
                        publish(False)
                    return None
                overlap_failed = ticket[2] != self._recovery_failures
                result, success = publish(overlap_failed)
                self._recovery_required = not success
                if not success:
                    self._recovery_failures += 1
                self.finish_halt()
                return result
            except BaseException:
                self._recovery_failures += 1
                self._recovery_required = True
                raise
            finally:
                self._recoveries.discard(ticket)

    @property
    def is_active(self) -> bool:
        """Tell whether scheduled reapplication is running.

        This reads one flag without taking the lock so that polling never waits
        for an apply that is in flight.
        """

        return not self._paused

    def pause(self) -> None:
        """Pause scheduled reapplication after a manual network reset."""

        # Wait for an in-flight scheduled apply so a reset cannot be undone by it.
        with self._apply_lock, self._lock:
            self._paused = True
            self._rolls.clear()
            self._pending_events = []
            self._current_payload = None
            self._last_device_counters.clear()
            self._device_traffic_bytes.clear()
            self._group_members.clear()
            self._blocked_moves.clear()
            self._applied_states = None
            self._clear_plan_failure()

    def _clear_plan_failure(self) -> int:
        """Forget the wait before a retry. Return how many tries had failed."""

        with self._lock:
            failures = self._plan_failures
            self._plan_failures = 0
            self._plan_retry_at = 0.0
            self._plan_failure_version = None
            self._reported_failures.pop("plan", None)
        return failures

    def _plan_retry_is_waiting(self, *, now: float, configuration_version: int) -> bool:
        """Tell whether a failed plan is still inside its wait before the next try."""

        with self._lock:
            if not self._plan_failures:
                return False
            if configuration_version != self._plan_failure_version:
                # A saved change gets a try at once. The wait then starts again at the
                # shortest step and the reason already logged stays logged.
                self._plan_failures = 0
                self._plan_retry_at = 0.0
                self._plan_failure_version = None
                return False
            wait = self._plan_retry_at - now
            # A wait longer than the longest step means the clock went backwards.
            return 0 < wait <= self.retry_max_seconds

    def _note_plan_failure(
        self, error: BaseException, *, now: float, configuration_version: Optional[int]
    ) -> Tuple[bool, float]:
        """Set the wait before the next try. Return whether the reason is new and the wait."""

        with self._lock:
            wait = min(
                self.retry_base_seconds * 2 ** min(self._plan_failures, 16),
                self.retry_max_seconds,
            )
            self._plan_failures += 1
            self._plan_retry_at = now + wait
            self._plan_failure_version = configuration_version
            return self._is_new_failure("plan", error), wait

    def _is_new_failure(self, kind: str, error: BaseException) -> bool:
        """Tell whether this reason differs from the last one logged for this kind of failure."""

        reason = (type(error).__name__, str(error))
        with self._lock:
            if self._reported_failures.get(kind) == reason:
                return False
            self._reported_failures[kind] = reason
            return True

    def halt(self) -> None:
        """Stop scheduled reapplication at once, without waiting for an apply in flight.

        Panic uses this so it never queues behind a slow apply. An apply that was
        running checks `halt_count` before it records its result.
        """

        with self._lock:
            self._paused = True
            self._halt_count += 1
            self._current_payload = None

    def finish_halt(self) -> None:
        """Clear the roll state of a halt when no apply is in flight.

        An apply that is in flight backs out on its own and clears it then.
        """

        if self._apply_lock.acquire(blocking=False):
            try:
                self.pause()
            finally:
                self._apply_lock.release()

    @property
    def halt_count(self) -> int:
        with self._lock:
            return self._halt_count

    def _keep_applied_version(self, configuration_version: int) -> None:
        """Keep the saved version of the running plan only while nothing was saved since.

        A scheduled apply changes timed and random effects only: it leaves access rule and
        ad-blocking edits pending. So it never makes newer saved settings count as running.
        Only a full apply from the dashboard does.
        """

        if self._applied_version != configuration_version:
            self._applied_version = None

    def running_plan(self):
        """Return the plan that runs and a copy of its randomization state, or None.

        The plan carries its source descriptions and the saved configuration version it
        was built from, so a caller can tell whether the saved settings are what runs and
        can apply exactly this plan again.
        """

        with self._apply_lock, self._lock:
            version = self._applied_version
            plan, states = self.applied_snapshot()
            if plan is None:
                return None
            return (
                PreparedRuntimePlan(plan=plan, states=dict(states or {}),
                                    configuration_version=version),
                {key: replace(roll) for key, roll in self._rolls.items()},
            )

    def restore_rolls(self, snapshot) -> None:
        """Put back the randomization state of a plan that is applied again."""

        with self._apply_lock:
            self._rolls = {key: replace(roll) for key, roll in snapshot.items()}

    def applied_snapshot(self):
        """Return the applied plan and its source descriptions without the apply lock."""

        with self._lock:
            return self.applied_plan(), self.applied_target_states()

    def sample_usage(self, *, repository: ConfigRepository) -> None:
        """Record quota and device traffic before the helper counters restart."""

        with self._apply_lock:
            self._sample_quota_usage(repository=repository, now=time.time())

    def sample_before_control_change(self, *, repository: ConfigRepository) -> None:
        """Record usage before an Apply or Reset replaces the helper counters."""

        with self._lock:
            self._last_quota_sample_at = 0
        self.sample_usage(repository=repository)

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

    def applied_target_states(self) -> Optional[Dict[str, TargetState]]:
        """Return confirmed source descriptions without rebuilding a plan."""

        with self._lock:
            if self._current_payload is None or self._applied_states is None:
                return None
            return dict(self._applied_states)

    def _next_schedule_change(self, schedules, now, **target) -> Optional[float]:
        settings = (schedules, time.tzname, time.timezone, time.altzone)
        if settings != self._schedule_hint_settings:
            self._schedule_hint_settings = settings
            self._schedule_hints.clear()
        key = tuple(sorted(target.items()))
        cached = self._schedule_hints.get(key)
        if cached is not None:
            checked_at, next_at = cached
            if now >= checked_at and (next_at is None or now < next_at):
                return next_at
        next_at = next_schedule_change(schedules=schedules, now=now, **target)
        if len(self._schedule_hints) >= 64:
            self._schedule_hints.clear()
        self._schedule_hints[key] = (now, next_at)
        return next_at

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

        with self.exclusive():
            with self._lock:
                if (self._paused or self._helper_client is None
                        or not self.can_apply(self.apply_generation)):
                    return False
                halts_at_start = self.apply_generation
            return self._tick(repository=repository, now=now, halts_at_start=halts_at_start)

    def _tick(
        self,
        *,
        repository: Optional[ConfigRepository],
        now: Optional[float],
        halts_at_start: Optional[int] = None,
    ) -> bool:
        current_time = time.time() if now is None else now
        if halts_at_start is None:
            halts_at_start = self.halt_count
        owned_repository = repository is None
        active_repository = repository or ConfigRepository(self._database_path)
        configuration_version: Optional[int] = None
        helper_attempted = False
        diagnostic_operation = None
        previous_rolls = {key: replace(roll) for key, roll in self._rolls.items()}
        self._pending_events = []
        published = False
        try:
            read_gateway_status = getattr(self._helper_client, "gateway_status", None)
            try:
                gateway_status = read_gateway_status() if read_gateway_status is not None else {}
            except HelperClientError:
                # The helper may be restarting. Try again on the next tick.
                LOGGER.warning("could not read the gateway state", exc_info=True)
                return False
            if (gateway_status.get("outage_stop_latched") is True
                    or gateway_status.get("panic_stop_latched") is True
                    or gateway_status.get("state_error") is True):
                self.pause()
                self._record(
                    active_repository,
                    [("outage", "Automatic profile recovery paused by gateway state")],
                    current_time,
                )
                return False
            control_revision = None
            reader = getattr(self._helper_client, "control_revision", None)
            if callable(reader):
                control_revision = gateway_status.get("control_revision")
                if control_revision is None:
                    control_revision = reader()
            recovery_pending = (
                gateway_status.get("recovery_pending") is True
                and gateway_status.get("gateway_state") == "available"
            )
            runtime_state = active_repository.get_runtime_state()
            configuration_version = runtime_state.configuration_version
            device_scope = None
            if runtime_state.last_action == "reset":
                # A device-only start keeps the Reset for everything outside its scope.
                device_scope = active_repository.get_device_start_scope()
                if device_scope is None:
                    # A saved change marks the state unknown, an outside reset says
                    # resetting or reset.
                    scope_ended = runtime_state.observed_state not in ("reset", "resetting")
                    if scope_ended and self._plan_retry_is_waiting(
                            now=current_time, configuration_version=configuration_version):
                        return False
                    self._honor_external_reset(
                        repository=active_repository, now=current_time, scope_ended=scope_ended,
                    )
                    return False
            self._follow_device_addresses(repository=active_repository, now=current_time)
            self._sample_traffic_if_needed(repository=active_repository, now=current_time)
            self._sample_quota_usage_if_needed(repository=active_repository, now=current_time)
            if current_time - self._last_analytics_cleanup_at >= 24 * 60 * 60:
                try:
                    active_repository.prune_random_website_analytics(
                        now=int(current_time)
                    )
                except StorageError:
                    LOGGER.warning(
                        "could not prune randomized website history",
                        exc_info=True,
                    )
                self._last_analytics_cleanup_at = current_time
            if self._plan_retry_is_waiting(
                now=current_time, configuration_version=configuration_version
            ):
                # The last plan failed a moment ago and nothing was saved since.
                # Sampling above carries on, only the plan waits.
                return False
            random_website_outcomes: List[RandomWebsiteOutcome] = []
            self._analytics_batch_overflowed = False
            plan, states = self._build_plan(
                repository=active_repository,
                now=current_time,
                random_website_outcomes=random_website_outcomes,
                device_scope=device_scope,
            )
            if random_website_outcomes:
                try:
                    active_repository.save_random_website_outcomes(
                        random_website_outcomes,
                        now=int(current_time),
                    )
                except StorageError:
                    LOGGER.warning(
                        "could not save randomized website history",
                        exc_info=True,
                    )
            if self._analytics_batch_overflowed:
                LOGGER.warning(
                    "randomized website history omitted choices because the tick reached its limit"
                )
            payload = plan_to_payload(plan)
            with self._lock:
                unchanged = payload == self._current_payload and not recovery_pending
            if unchanged:
                # The network already runs this plan, for example one the
                # dashboard applied after a saved change.
                def publish_unchanged():
                    self._keep_applied_version(configuration_version)
                    self._log_plan_recovery(self._clear_plan_failure())
                    self._record_activity(repository=active_repository, states=states, now=current_time)
                    self._save_patterns(active_repository, current_time)
                published, _ = self.publish_if_current(halts_at_start, publish_unchanged)
                return False
            # Record traffic before an apply can replace the helper counters.
            self._sample_quota_usage(repository=active_repository, now=current_time)
            if not self.can_apply(halts_at_start):
                return False
            helper_attempted = True
            diagnostic_operation = Operation("schedule", "scheduler", configuration_version)
            try:
                apply = getattr(self._helper_client, "apply_experiment", self._helper_client.apply)
                arguments = {"control_revision": control_revision} if control_revision is not None else {}
                report = apply(plan=plan, **arguments)
            except HelperClientError:
                # Cleanup may have removed the previous rules too. An active
                # scheduler must retry even if the desired plan is unchanged.
                self.invalidate_applied_plan()
                raise
            def publish():
                if not isinstance(report, dict) or report.get("counters_preserved") is not True:
                    active_repository.rebase_quota_counters(now=int(current_time))
                    self._last_device_counters = dict.fromkeys(self._last_device_counters, 0)
                self._current_payload = payload
                self._keep_applied_version(configuration_version)
                self._log_plan_recovery(self._clear_plan_failure())
                if not active_repository.record_schedule_result(success=True):
                    self.pause()
                    return False
                self._record_activity(repository=active_repository, states=states, now=current_time)
                self._save_patterns(active_repository, current_time)
                return True

            accepted, applied = self.publish_if_current(halts_at_start, publish)
            published = bool(accepted and applied)
            if not accepted and self.halt_count != halts_at_start:
                self._undo_overtaken_apply(active_repository)
            elif accepted and not applied:
                self._honor_external_reset(repository=active_repository, now=current_time)
            return bool(accepted and applied)
        except (HelperClientError, PlanError, StorageError, OSError) as error:
            if isinstance(error, HelperClientError) and error.reason == "control-changed":
                self.halt()
                self.require_recovery()
                return False
            if not self.can_apply(halts_at_start):
                if helper_attempted and self.halt_count != halts_at_start:
                    self._undo_overtaken_apply(active_repository)
                return False
            is_new_reason, wait = self._note_plan_failure(
                error, now=current_time, configuration_version=configuration_version
            )
            if is_new_reason:
                LOGGER.exception(
                    "scheduled network plan failed (%s); trying again in %.0f s and then less often",
                    error,
                    wait,
                )
            else:
                LOGGER.debug(
                    "scheduled network plan failed again (%s); trying again in %.0f s",
                    error,
                    wait,
                    exc_info=True,
                )
            try:
                self.publish_if_current(halts_at_start, lambda: active_repository.record_schedule_result(success=False))
            except StorageError:
                LOGGER.exception("could not record scheduled network failure")
            return False
        finally:
            if diagnostic_operation is not None:
                diagnostic_operation.finish("succeeded" if published else
                                            "cancelled" if not self.can_apply(halts_at_start) else "failed")
            if not published and not self._paused and self.can_apply(halts_at_start):
                self._rolls = previous_rolls
            self._pending_events = []
            if owned_repository:
                active_repository.close()

    def _undo_overtaken_apply(self, repository: ConfigRepository) -> None:
        ticket = self.begin_recovery(cancel=False)
        error = None
        try:
            restore = getattr(self._helper_client, "normal_access", self._helper_client.reset)
            restore()
        except HelperClientError:
            LOGGER.error("could not undo a scheduled apply that Panic overtook", exc_info=True)
            error = "Normal access was not fully restored after Panic. Retry Panic before choosing Start."
        except BaseException:
            self.abandon_recovery(ticket)
            raise

        def publish(overlap_failed=False):
            nonlocal error
            if overlap_failed and error is None:
                error = "Another Panic cleanup failed during this recovery. Retry Panic before choosing Start."
            try:
                repository.update_runtime_state(
                    observed_state="error" if error else "reset", last_action="reset",
                    last_result="failure" if error else "success", last_error=error,
                )
            except (StorageError, sqlite3.Error):
                LOGGER.exception("could not save scheduled Panic cleanup")
                return None, False
            return None, error is None

        self.finish_recovery(ticket, publish, failed=error is not None)

    @staticmethod
    def _log_plan_recovery(failed_tries: int) -> None:
        if failed_tries:
            LOGGER.info(
                "scheduled network plan works again after %d failed %s",
                failed_tries,
                "attempt" if failed_tries == 1 else "attempts",
            )

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
        # Only a reachable neighbour entry confirms an address. Otherwise the
        # helper falls back to a DHCP lease or an old neighbour entry, which
        # can be stale and would move a device back and forth.
        seen = {
            client["mac_address"]: client["ipv4_address"]
            for client in snapshot.get("devices", [])
            if client.get("association_verified")
            and client.get("neighbor_state") == "reachable"
            and client.get("mac_address")
            and client.get("ipv4_address")
        }
        devices = {device.id: device for device in repository.list_devices()}
        moves, blocked = plan_address_moves(tuple(devices.values()), seen)
        titles = device_titles(repository, tuple(devices.values())) if moves or blocked else {}
        for device_id, (address, holder) in blocked.items():
            if self._blocked_moves.get(device_id) != (address, holder):
                LOGGER.warning(
                    "device %s now uses %s, which saved device %s keeps; not moving it",
                    device_id,
                    address,
                    holder,
                )
                device = devices[device_id]
                keeper = titles.get(holder, holder)
                self._record(
                    repository,
                    [(
                        "address",
                        f"{titles[device_id]} now uses {address}, which {keeper} keeps, "
                        f"so it stays at {device.ipv4_address}",
                    )],
                    now,
                )
        # An idle device drops out of the confirmed clients for a while; it keeps
        # its entry until it is seen again, so its blocked move is logged once.
        unconfirmed = {
            device_id: entry
            for device_id, entry in self._blocked_moves.items()
            if device_id in devices and devices[device_id].mac_address not in seen
        }
        self._blocked_moves = {**unconfirmed, **blocked}
        if not moves:
            return
        if not repository.move_device_addresses(moves):
            LOGGER.warning("saved devices changed during the address check; checking again later")
            return
        for device_id, (old_address, new_address) in moves.items():
            LOGGER.info("device %s moved from %s to %s", device_id, old_address, new_address)
            self._record(
                repository,
                [(
                    "address",
                    f"{titles[device_id]} moved from {old_address} to {new_address}",
                )],
                now,
                device_ids=[(device_id,)],
            )

    def _record_activity(
        self, *, repository: ConfigRepository, states: Dict[str, TargetState], now: float
    ) -> None:
        """Record what changed on the network since the last applied plan."""

        with self._lock:
            previous = self._applied_states
            self._applied_states = states
        # The first successful plan after a reset or restart is the starting point.
        if previous is None:
            return
        records = activity_records(previous, states)
        # A service list belongs to no device, so its events name no device.
        self._record(
            repository,
            [(kind, message) for kind, message, _ in records],
            now,
            device_ids=[
                tuple(
                    target for target in targets
                    if not target.startswith(SERVICE_LIST_QUEUE_PREFIX)
                )
                for _, _, targets in records
            ],
        )

    @staticmethod
    def _record(
        repository: ConfigRepository, events: Sequence[Tuple[str, str]], now: float, *, device_ids=None
    ) -> None:
        if not events:
            return
        try:
            repository.record_activities(events, now=now, device_ids=device_ids)
        except StorageError:
            LOGGER.warning("could not record activity", exc_info=True)

    def _honor_external_reset(
        self, *, repository: ConfigRepository, now: float, scope_ended: bool = False
    ) -> None:
        # Someone reset the network outside the dashboard, for example with
        # `jittersim reset`. Stop applying, and reset once more in case a
        # scheduled apply raced that reset. A device-only start whose devices
        # were all deleted ends the same way, but its cleanup stays pending:
        # the scheduler keeps running and retries the reset with growing waits.
        if not scope_ended:
            self.pause()
        try:
            self._helper_client.reset()
        except HelperClientError as error:
            LOGGER.warning("could not repeat an external network reset", exc_info=True)
            message = "Network reset requested outside the dashboard; repeating it failed"
            if scope_ended:
                message = "Ending a device-only start failed; trying the reset again"
                with self._lock:
                    attempts = self._plan_failures
                if attempts + 1 >= self.cleanup_attempts:
                    self.pause()
                    message = "Ending a device-only start failed. Retry Panic"
                self._note_plan_failure(
                    error, now=now,
                    configuration_version=repository.get_runtime_state().configuration_version)
                try:
                    repository.update_runtime_state(
                        observed_state="error", last_action="reset", last_result="failure",
                        last_error="device-only cleanup failed",
                    )
                except StorageError:
                    LOGGER.warning("could not record the failed cleanup", exc_info=True)
        else:
            if scope_ended:
                self.pause()
            message = (
                "Device-only start ended because its devices were removed"
                if scope_ended else "Network reset outside the dashboard"
            )
            if scope_ended:
                try:
                    repository.update_runtime_state(
                        observed_state="reset", last_action="reset",
                        last_result="success", last_error=None,
                    )
                except StorageError:
                    LOGGER.warning("could not record the ended device-only start", exc_info=True)
        self._record(repository, [("reset", message)], now)

    def _build_plan(
        self,
        *,
        repository: ConfigRepository,
        now: float,
        random_website_outcomes: Optional[List[RandomWebsiteOutcome]] = None,
        connected_snapshot: Optional[dict] = None,
        device_scope: Optional[frozenset] = None,
        everyone_exceptions: Optional[frozenset] = None,
    ) -> Tuple[NetworkPlan, Dict[str, TargetState]]:
        """Build the runtime plan and what applies to each target, for the activity log.

        The website lookups of the plan share one deadline. Names that miss it are
        left out of the plan and listed in `_unresolved_names` for the caller.
        """

        with lookup_session() as lookups:
            try:
                return self._build_plan_with_lookups(
                    repository=repository,
                    now=now,
                    random_website_outcomes=random_website_outcomes,
                    connected_snapshot=connected_snapshot,
                    device_scope=device_scope,
                    everyone_exceptions=everyone_exceptions,
                )
            finally:
                self._unresolved_names = tuple(lookups.timed_out)

    def _build_plan_with_lookups(
        self,
        *,
        repository: ConfigRepository,
        now: float,
        random_website_outcomes: Optional[List[RandomWebsiteOutcome]] = None,
        connected_snapshot: Optional[dict] = None,
        device_scope: Optional[frozenset] = None,
        everyone_exceptions: Optional[frozenset] = None,
    ) -> Tuple[NetworkPlan, Dict[str, TargetState]]:
        scoped = device_scope is not None
        general_profile = None if scoped else repository.get_general_profile()
        beside_everyone = None
        if general_profile is not None:
            if everyone_exceptions is None:
                everyone_exceptions = repository.get_everyone_exceptions()
            if not everyone_exceptions:
                return self._build_general_plan(
                    repository=repository, profile=general_profile, now=now)
            # Everyone runs and these devices opt out of it. Only they get device rules.
            beside_everyone = general_profile
            device_scope = frozenset(everyone_exceptions)
            scoped = True
        snapshot = connected_snapshot
        if snapshot is None and repository.list_unaffected_devices():
            snapshot = station_snapshot(self._helper_client)
        clients = eligible_clients(repository, snapshot)
        if scoped:
            # A device-only start: every other saved choice stays stopped.
            clients = replace(clients, devices=tuple(
                device for device in clients.devices if device.id in device_scope))
        profiles = {profile.id: profile for profile in repository.list_profiles()}
        schedules = repository.list_schedules()
        active_progressions = {}
        for progression in repository.list_active_progressions(now=int(now)):
            active_progressions.setdefault(progression.device_id, progression)
        active_timers = {}
        for timer in repository.list_active_timers(now=int(now)):
            active_timers.setdefault(timer.device_id, timer)
        # Newest first, so the newest run wins if a target somehow has two.
        device_runs = {}
        group_runs = {}
        everyone_else_run = None
        for running in active_scenario_runs(repository, now=now):
            run = running[0]
            if run.device_id is not None:
                device_runs.setdefault(run.device_id, running)
            elif run.group_id is not None:
                group_runs.setdefault(run.group_id, running)
            elif everyone_else_run is None and not scoped:
                everyone_else_run = running
        moment = datetime.fromtimestamp(now).replace(second=0, microsecond=0)
        runtime_devices = []
        runtime_profiles = {}
        states: Dict[str, TargetState] = {}
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
        titles = device_titles(repository, clients.devices)

        for device in clients.devices:
            group = groups_by_device.get(device.id)
            progression, timer, running, schedule = choose_automation(
                device, group, moment, active_progressions, active_timers,
                device_runs, group_runs, schedules,
            )
            group_profile_id = group.profile_id if group is not None else None
            # How the activity log tells this device's source apart and names it:
            # kind, identity, label, step and, for a group's source, the group.
            if schedule is not None:
                described = (
                    "schedule", f"schedule:{schedule.id}", schedule.id, "",
                    group.name if schedule.group_id is not None else "",
                )
            elif group_profile_id is not None:
                described = (
                    "group-profile", f"group-profile:{group.id}", group.name, "", group.name
                )
            else:
                described = ("profile", "profile", "", "", "")
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
                described = ("timer", f"timer:{timer.id}", "", "", "")
            elif running is not None:
                run, scenario, position = running
                source_profile_id = position.profile_id
                selected_profile = profiles.get(source_profile_id)
                if selected_profile is None:
                    raise PlanError(f"scenario {scenario.id} references a missing profile")
                source_profiles = selected_profile
                # Each step starts a fresh roll, even when a loop returns to it.
                source_key = f"scenario:{run.id}:{position.step_index}"
                described = (
                    "scenario",
                    source_key,
                    scenario.name,
                    f"step {position.step_index + 1} of {len(scenario.steps)}",
                    group.name if run.group_id is not None else "",
                )
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
                source_profiles = (progression, start_profile, end_profile)
                source_key = f"progression:{progression.id}"
                described = (
                    "progression", source_key,
                    f"from {start_profile.name} to {end_profile.name}", "", "",
                )
                domain_count = int(len(progression.website_domains) * factor)
                if domain_count:
                    blocked_domains_by_device[device.id] = progression.website_domains[:domain_count]
            data_limit = data_limits.get(device.id)
            if reached_limit(data_limit):
                fallback_profile = profiles.get(data_limit.profile_id)
                if fallback_profile is None:
                    raise PlanError(
                        f"data limit for device {device.id} references a missing profile"
                    )
                selected_profile = fallback_profile
                source_profiles = fallback_profile
                source_key = f"data-limit:{device.id}"
                described = ("data-limit", "data-limit", "", "", "")
            elif group is not None:
                group_data_limit = group_data_limits.get(group.id)
                if reached_limit(group_data_limit):
                    fallback_profile = profiles.get(group_data_limit.profile_id)
                    if fallback_profile is None:
                        raise PlanError(
                            f"group data limit for {group.id} references a missing profile"
                        )
                    selected_profile = fallback_profile
                    source_profiles = fallback_profile
                    source_key = f"group-data-limit:{group.id}"
                    described = ("group-data-limit", source_key, group.name, "", group.name)
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
                random_website_outcomes=(
                    random_website_outcomes
                    if progression is None and selected_profile.website_mode == "allow_only"
                    else None
                ),
            )
            if roll.outage_until > now:
                blocked_addresses.append(device.ipv4_address)
            kind, identity, label, step, group_name = described
            # A progression's profile changes as it goes, so its name is left out.
            profile_name = "" if kind == "progression" else selected_profile.name
            next_change = None
            if kind == "timer":
                next_change = timer.expires_at
            elif kind == "scenario":
                next_change = int(now) + position.step_remaining_seconds
            elif kind == "progression" and progression.mode == "time":
                next_change = progression.started_at + progression.duration_seconds
            elif kind in ("profile", "group-profile", "schedule"):
                next_change = self._next_schedule_change(
                    schedules, now, device_id=device.id,
                    group_id=group.id if group is not None else None,
                )
            states[device.id] = TargetState(
                name=titles[device.id],
                next_change_at=next_change,
                source=Source(kind, identity, label, step, profile_name, group_name),
                outage=roll.outage_until > now,
            )

            # Device ids may already use the full identifier length, so number
            # the runtime profiles instead of prefixing the device id.
            runtime_profile_id = f"runtime-{len(runtime_devices) + 1}"
            while beside_everyone is not None and runtime_profile_id == beside_everyone.id:
                runtime_profile_id += "-device"
            runtime_profiles[runtime_profile_id] = roll.runtime_profile
            runtime_devices.append(replace(device, profile_id=runtime_profile_id))

        everyone_else = None
        everyone_else_key = None
        everyone_else_described = ("profile", "profile", "", "")
        if everyone_else_run is not None:
            run, scenario, position = everyone_else_run
            everyone_else_profile_id = position.profile_id
            everyone_else_key = f"scenario:{run.id}:{position.step_index}"
            everyone_else_described = (
                "scenario",
                everyone_else_key,
                scenario.name,
                f"step {position.step_index + 1} of {len(scenario.steps)}",
            )
        else:
            everyone_else_schedule = None if scoped else active_schedule(
                schedules=schedules, everyone_else=True, moment=moment
            )
            # A schedule or scenario can give everyone else a profile even when
            # none is saved.
            everyone_else_profile_id = (
                everyone_else_schedule.profile_id
                if everyone_else_schedule is not None
                else None if scoped else repository.get_everyone_else_profile_id()
            )
            if everyone_else_schedule is not None:
                everyone_else_described = (
                    "schedule",
                    f"schedule:{everyone_else_schedule.id}",
                    everyone_else_schedule.id,
                    "",
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
                source_key=everyone_else_key or profile.id,
                source_profiles=profile,
                now=now,
            )
            everyone_else = EveryoneElseRule(
                mark=0,
                upload_impairment=roll.runtime_profile.upload,
                download_impairment=roll.runtime_profile.download,
                homepage_bounce_percent=roll.runtime_profile.homepage_bounce_percent,
                unaffected_macs=clients.protected_macs,
                blocked=roll.outage_until > now,
            )
            kind, identity, label, step = everyone_else_described
            next_change = (
                int(now) + position.step_remaining_seconds if everyone_else_run is not None
                else self._next_schedule_change(schedules, now, everyone_else=True)
            )
            states[EVERYONE_ELSE_QUEUE_ID] = TargetState(
                name="Everyone else",
                next_change_at=next_change,
                source=Source(kind, identity, label, step, profile.name),
                outage=roll.outage_until > now,
            )
        else:
            states[EVERYONE_ELSE_QUEUE_ID] = TargetState(
                name="Everyone else", source=Source("none", "none"),
                next_change_at=self._next_schedule_change(schedules, now, everyone_else=True),
            )

        general_rule = None
        if beside_everyone is not None:
            # Everyone keeps its own roll, outage and website choices, as when it runs alone.
            general_roll = self._roll(
                key="@general",
                selected_profile=beside_everyone,
                source_key=beside_everyone.id,
                source_profiles=beside_everyone,
                now=now,
                generator_key=GENERAL_QUEUE_ID,
            )
            active_device_ids.add("@general")
            runtime_profiles[beside_everyone.id] = general_roll.runtime_profile
            general_rule = GeneralProfileRule(
                profile_id=beside_everyone.id,
                allowed_addresses=resolve_domains(
                    domains=general_roll.runtime_profile.website_domains)
                if general_roll.runtime_profile.website_mode == "allow_only" else None,
                outage_active=general_roll.outage_until > now,
            )
        service_list_rules = () if scoped and general_rule is None else self._build_service_list_rules(
            repository=repository,
            profiles=profiles,
            now=now,
            active_keys=active_device_ids,
            states=states,
        )
        website_policy = repository.get_website_policy()
        global_blocked, website_filters = build_website_filters(
            devices=runtime_devices,
            profiles=runtime_profiles,
            global_policy=website_policy,
            blocked_domains_by_device=blocked_domains_by_device,
        )
        service_quota_rules = self._build_service_quota_rules(
            repository=repository,
            devices=runtime_devices,
            profiles=profiles,
            now=now,
            active_keys=active_device_ids,
        )
        # Limits are told apart by ID, so a renamed limit is the same limit.
        limit_labels = {
            quota.id: f"service limit {quota.name}" for quota in repository.list_service_quotas()
        }
        limit_labels.update(
            (time_quota_counter_id(quota.id), f"time limit {quota.name}")
            for quota in repository.list_time_quotas()
        )
        for rule in service_quota_rules:
            if rule.active and rule.device_id in states:
                state = states[rule.device_id]
                label = limit_labels.get(rule.quota_id, rule.quota_id)
                reached = state.limits + ((rule.quota_id, label),)
                states[rule.device_id] = replace(state, limits=tuple(sorted(reached)))
        service_assignment_rules = []
        devices_by_id = {device.id: device for device in runtime_devices}
        for assignment in repository.list_service_assignments():
            if not assignment.enabled or assignment.device_id in clients.excluded_ids:
                continue
            if scoped and assignment.device_id not in devices_by_id:
                continue
            device = devices_by_id.get(assignment.device_id)
            profile = profiles.get(assignment.profile_id)
            if device is None or profile is None:
                raise PlanError(f"service assignment {assignment.id} has a missing reference")
            key = f"assignment:{assignment.id}"
            active_device_ids.add(key)
            profile = self._service_profile(profile, key, now)
            service_assignment_rules.append(
                ServiceAssignmentRule(
                    assignment_id=assignment.id,
                    device_id=device.id,
                    device_ip=device.ipv4_address,
                    domains=assignment.domains,
                    mark=0,
                    upload_impairment=profile.upload,
                    download_impairment=profile.download,
                    homepage_bounce_percent=profile.homepage_bounce_percent,
                )
            )
        self._rolls = {key: roll for key, roll in self._rolls.items() if key in active_device_ids}
        plan = compile_plan(
            devices=runtime_devices,
            profiles=runtime_profiles,
            protected_mac_addresses=clients.protected_macs,
            unverified_device_ids=clients.unverified_ids,
            mac_policy=repository.get_mac_policy(),
            access=build_access_plan(repository.access_settings()),
            global_blocked_addresses=global_blocked,
            global_allowed_addresses=resolve_domains(domains=website_policy.allowed_domains)
            if website_policy.mode == "allow_only"
            else None,
            website_filters=website_filters,
            blocked_device_addresses=blocked_addresses,
            service_quota_rules=service_quota_rules,
            service_assignment_rules=service_assignment_rules,
            service_list_rules=service_list_rules,
            everyone_else=everyone_else,
            general_profile=general_rule,
        )
        return plan, states

    def _build_general_plan(
        self, *, repository: ConfigRepository, profile: Profile, now: float
    ) -> Tuple[NetworkPlan, Dict[str, TargetState]]:
        """Build the shared plan. Device rolls are dropped while it is active."""

        key = "@general"
        roll = self._roll(
            key=key,
            selected_profile=profile,
            source_key=profile.id,
            source_profiles=profile,
            now=now,
            generator_key=GENERAL_QUEUE_ID,
        )
        # The activity log describes saved devices. The general profile covers
        # every unprotected client, so it has no per-device sources to compare.
        # Service lists still roll and their outages are still logged.
        states: Dict[str, TargetState] = {}
        active_keys = {key}
        service_list_rules = self._build_service_list_rules(
            repository=repository, profiles=None, now=now, active_keys=active_keys, states=states
        )
        self._rolls = {
            roll_key: kept for roll_key, kept in self._rolls.items() if roll_key in active_keys
        }
        plan = compile_general_plan(
            repository=repository,
            profile=roll.runtime_profile,
            outage_active=roll.outage_until > now,
            service_list_rules=service_list_rules,
        )
        return plan, states

    def _build_service_list_rules(
        self,
        *,
        repository: ConfigRepository,
        profiles: Optional[Mapping[str, Profile]],
        now: float,
        active_keys: set,
        states: Dict[str, TargetState],
    ) -> tuple:
        """Roll each enabled list once for all affected clients.

        A list keeps one roll under its queue key so every affected device
        shares its values and its outages, like the list's shared speed limit.
        The roll uses the list's saved profile and adds the list's key to
        `active_keys` and its outage state to `states` for the activity log.
        """

        def roll(service_list: ServiceList, profile: Profile) -> Tuple[Profile, bool]:
            if profile.website_mode != "unrestricted":
                raise PlanError("Service profiles cannot use a profile website allowlist")
            key = SERVICE_LIST_QUEUE_PREFIX + service_list.id
            active_keys.add(key)
            state = self._roll(
                key=key,
                selected_profile=profile,
                source_key=profile.id,
                source_profiles=profile,
                now=now,
            )
            outage = state.outage_until > now
            states[key] = TargetState(
                name=f"Service list {service_list.name}",
                source=Source("none", "none"),
                outage=outage,
            )
            return state.runtime_profile, outage

        return build_service_list_rules(repository, profiles, roll=roll)

    def _roll(
        self,
        *,
        key: str,
        selected_profile: Profile,
        source_key: str,
        source_profiles: object,
        now: float,
        reroll_each_tick: bool = False,
        random_website_outcomes: Optional[List[RandomWebsiteOutcome]] = None,
        generator_key: Optional[str] = None,
    ) -> _DeviceRollState:
        """Advance the rerolls and outage checks kept under one key.

        `random_website_outcomes` collects the kept and removed website choices
        of a scheduled reroll for the daily analytics.
        """

        generator_key = key if generator_key is None else generator_key
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
                seed=(secrets.randbits(53) if selected_profile.random_mode == "fresh" and not self._preview else selected_profile.random_seed or 0),
                session_id=secrets.token_hex(16) if not self._preview else "preview",
                started_at=now,
                random_mode=selected_profile.random_mode,
            )
            self._rolls[key] = roll
            self._record_pattern(roll, selected_profile, generator_key, now, "start")

        seed_changed = roll.random_mode != selected_profile.random_mode or (
            selected_profile.random_mode == "repeatable"
            and roll.seed != (selected_profile.random_seed or 0)
        )
        if seed_changed:
            roll.random_mode = selected_profile.random_mode
            roll.seed = (
                secrets.randbits(53) if roll.random_mode == "fresh" and not self._preview
                else selected_profile.random_seed or 0
            )

        if reroll_each_tick:
            generator = seeded_generator(
                profile=selected_profile,
                device_id=generator_key,
                roll_number=roll.roll_number,
                session_seed=roll.seed,
            )
            roll.runtime_profile = reroll_profile(
                profile=selected_profile,
                generator=generator,
            )

        if selected_profile.reroll_interval_seconds and now >= roll.next_roll_at:
            generator = seeded_generator(
                profile=selected_profile,
                device_id=generator_key,
                roll_number=roll.roll_number,
                session_seed=roll.seed,
            )
            website_choices: List[Tuple[str, bool]] = []
            roll.runtime_profile = reroll_profile(
                profile=selected_profile,
                generator=generator,
                website_outcomes=(
                    website_choices if random_website_outcomes is not None else None
                ),
            )
            if random_website_outcomes is not None:
                if (
                    len(random_website_outcomes) + len(website_choices)
                    <= MAX_RANDOM_WEBSITE_ANALYTICS_BATCH
                ):
                    random_website_outcomes.extend(
                        RandomWebsiteOutcome(
                            device_id=key,
                            domain=domain,
                            kept=kept,
                            happened_at=int(now),
                        )
                        for domain, kept in website_choices
                    )
                else:
                    self._analytics_batch_overflowed = True
            roll.roll_number += 1
            self._record_pattern(roll, selected_profile, generator_key, now, "reroll")
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
                device_id=generator_key,
                roll_number=100_000 + roll.outage_check_number,
                session_seed=roll.seed,
            )
            roll.outage_check_number += 1
            if outage_is_selected(profile=selected_profile, generator=generator) and now >= roll.outage_until:
                duration = outage_duration_seconds(
                    profile=selected_profile,
                    generator=generator,
                )
                roll.outage_until = now + duration if duration else now
            roll.next_outage_check_at = max(now, roll.outage_until) + max(
                1,
                outage_interval_seconds(profile=selected_profile, generator=generator),
            )
            self._record_pattern(roll, selected_profile, generator_key, now, "outage-check")
        if seed_changed:
            self._record_pattern(roll, selected_profile, generator_key, now, "seed-change")
        return roll

    def _record_pattern(self, roll: _DeviceRollState, profile: Profile, scope: str, now: float, kind: str) -> None:
        if not self._preview and len(self._pending_events) < 1000:
            self._pending_events.append({
                "session_id": roll.session_id, "profile_id": profile.id, "scope": scope,
                "happened_at": int(now), "kind": kind, "seed": str(roll.seed),
                "elapsed_seconds": max(0.0, now - roll.started_at),
                "roll_number": roll.roll_number, "outage_number": roll.outage_check_number,
                "outage_until": roll.outage_until, "outage_active": roll.outage_until > now,
                "upload": asdict(roll.runtime_profile.upload), "download": asdict(roll.runtime_profile.download),
            })

    def _save_patterns(self, repository: ConfigRepository, now: float) -> None:
        active_sessions = {roll.session_id for roll in self._rolls.values()}
        self._pending_events = [event for event in self._pending_events if event["session_id"] in active_sessions]
        starts = {event["session_id"] for event in self._pending_events if event["kind"] == "start"}
        for key, roll in self._rolls.items():
            if not roll.history_started and roll.session_id not in starts:
                scope = GENERAL_QUEUE_ID if key == "@general" else key
                self._record_pattern(roll, roll.runtime_profile, scope, now, "start")
        if not self._pending_events and now - self._last_pattern_cleanup_at < 3600:
            return
        try:
            repository.save_pattern_events(self._pending_events, now=int(now))
            self._last_pattern_cleanup_at = now
            for roll in self._rolls.values():
                roll.history_started = True
        except StorageError:
            LOGGER.warning("could not save pattern history", exc_info=True)
        self._pending_events = []

    def _service_profile(self, profile: Profile, key: str, now: float) -> Profile:
        if profile.website_mode != "unrestricted":
            raise PlanError("Service profiles cannot use a profile website allowlist")
        roll = self._roll(key=key, selected_profile=profile, source_key=profile.id,
                          source_profiles=profile, now=now)
        if roll.outage_until > now:
            return replace(roll.runtime_profile,
                           upload=replace(roll.runtime_profile.upload, loss_percent=100),
                           download=replace(roll.runtime_profile.download, loss_percent=100))
        return roll.runtime_profile

    def _build_service_quota_rules(
        self,
        *,
        repository: ConfigRepository,
        devices: Sequence[Device],
        profiles: Dict[str, Profile],
        now: float,
        active_keys: set,
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
            if quota.used_bytes >= quota.limit_bytes:
                key = f"quota:{quota.id}"
                active_keys.add(key)
                fallback_profile = self._service_profile(fallback_profile, key, now)
            rules.append(
                ServiceQuotaRule(
                    quota_id=quota.id,
                    device_id=device.id,
                    device_ip=device.ipv4_address,
                    destination_addresses=destination_addresses,
                    mark=0,
                    upload_impairment=fallback_profile.upload,
                    download_impairment=fallback_profile.download,
                    homepage_bounce_percent=fallback_profile.homepage_bounce_percent,
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
            if self._time_quota_window_is_active(quota, now) and quota.used_seconds >= quota.limit_seconds:
                key = f"quota:{time_quota_counter_id(quota.id)}"
                active_keys.add(key)
                fallback_profile = self._service_profile(fallback_profile, key, now)
            rules.append(
                ServiceQuotaRule(
                    quota_id=time_quota_counter_id(quota.id),
                    device_id=device.id,
                    device_ip=device.ipv4_address,
                    destination_addresses=destination_addresses,
                    mark=0,
                    upload_impairment=fallback_profile.upload,
                    download_impairment=fallback_profile.download,
                    homepage_bounce_percent=fallback_profile.homepage_bounce_percent,
                    active=(
                        self._time_quota_window_is_active(quota, now)
                        and quota.used_seconds >= quota.limit_seconds
                    ),
                )
            )
        return tuple(rules)

    def _sample_traffic_if_needed(self, *, repository: ConfigRepository, now: float) -> None:
        """Persist a bounded DNS sample when a traffic progression is active."""

        if _only_everyone_runs(repository) or now - self._last_traffic_sample_at < 5:
            return
        progressions = repository.list_active_progressions(now=int(now))
        # Beside Everyone only the devices that run their own profile run progressions.
        # The others are paused, so their progressions neither sample nor advance.
        running_ids = repository.get_everyone_exceptions() if _general_profile_runs(
            repository) else None
        if running_ids is not None:
            progressions = tuple(item for item in progressions if item.device_id in running_ids)
        if not any(progression.mode == "traffic" for progression in progressions):
            return
        observe_dns_queries = getattr(self._helper_client, "observe_dns_queries", None)
        if observe_dns_queries is None:
            return
        self._last_traffic_sample_at = now
        try:
            macs = protected_macs(repository)
            observations = observe_dns_queries(macs)
            stations = [
                station
                for station in station_snapshot(self._helper_client).get("devices", ())
                if isinstance(station, dict)
            ]
            blocked = protected_addresses(repository, stations)
            macs_by_address = station_macs_by_address(stations)
            observed_at = int(now)
            if running_ids is not None:
                running_addresses = {
                    str(device.ipv4_address)
                    for device in repository.list_devices() if device.id in running_ids
                }
                observations = [
                    observation for observation in observations
                    if str(observation["ipv4_address"]) in running_addresses
                ]
            sampled = tuple(
                DomainObservation(
                    ipv4_address=observation["ipv4_address"],
                    domain=observation["domain"],
                    query_count=observation["query_count"],
                    first_seen=observed_at,
                    last_seen=observed_at,
                    mac_address=macs_by_address.get(str(observation["ipv4_address"])),
                )
                for observation in observations
            )
            repository.save_domain_observations(
                tuple(
                    observation
                    for observation in sampled
                    if not observation_is_protected(observation, set(macs), blocked)
                ),
                now=observed_at,
            )
            self._last_traffic_sample_error = None
        except (KeyError, TypeError, ValueError, StorageError, HelperClientError) as error:
            # The sampler retries every few seconds so say it once for each new reason.
            reason = "%s: %s" % (type(error).__name__, error)
            if reason != self._last_traffic_sample_error:
                self._last_traffic_sample_error = reason
                LOGGER.warning(
                    "traffic progression sample failed: %s",
                    error,
                    exc_info=not isinstance(error, HelperClientError),
                )

    def counter_device_ids(self, *, repository: ConfigRepository) -> set:
        """Use the installed rule membership when adding shared counters."""

        if _only_everyone_runs(repository):
            return set()
        protected = {item.mac_address for item in repository.list_unaffected_devices()}
        allowed = {
            device.id for device in repository.list_devices() if device.mac_address not in protected
        }
        if _general_profile_runs(repository):
            # Beside Everyone only the devices that run their own profile have counters.
            allowed &= repository.get_everyone_exceptions()
        payload = self._current_payload
        if isinstance(payload, dict) and isinstance(payload.get("mark_rules"), list):
            return allowed & {rule["device_id"] for rule in payload["mark_rules"]}
        snapshot = station_snapshot(self._helper_client) if protected else None
        return {
            device.id for device in eligible_clients(repository, snapshot).devices
            if device.id in allowed
        }

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
            eligible_ids = self.counter_device_ids(repository=repository)
            excluded = {
                device.id for device in repository.list_devices() if device.id not in eligible_ids
            }
            device_counters = {
                key: value
                for key, value in counters.get("devices", {}).items()
                if key in eligible_ids
            }
            excluded_services = {
                quota.id for quota in service_quotas if quota.device_id in excluded
            } | {
                time_quota_counter_id(quota.id)
                for quota in time_quotas
                if quota.device_id in excluded
            }
            service_counters = {
                key: value
                for key, value in counters.get("services", {}).items()
                if key not in excluded_services
            }
            group_members = {
                group.id: tuple(
                    device_id for device_id in group.device_ids if device_id not in excluded
                )
                for group in groups_by_id.values()
            }
            for device_id, counter in device_counters.items():
                previous = self._last_device_counters.get(device_id)
                if previous is None:
                    self._device_traffic_bytes[device_id] = (
                        self._device_traffic_bytes.get(device_id, 0) + counter
                    )
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
                    device_id not in device_counters for device_id in group_members[group.id]
                ):
                    continue
                aggregate_counter = sum(
                    device_counters[device_id] for device_id in group_members[group.id]
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
                if quota.device_id in excluded:
                    repository.rebase_time_quota_sample(quota.id, now=int(now))
                    continue
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
                    device_id in device_counters for device_id in group_members[group.id]
                ):
                    aggregate_counter = sum(
                        device_counters[device_id] for device_id in group_members[group.id]
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
            except Exception as error:
                # Keep schedules, timers, outages and quotas running; the next
                # tick retries with fresh state. The same reason is logged once.
                if self._is_new_failure("tick", error):
                    LOGGER.exception("scheduler tick failed unexpectedly")
            else:
                with self._lock:
                    self._reported_failures.pop("tick", None)

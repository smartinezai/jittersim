"""Background runtime for schedules, randomized rerolls and outages."""

from dataclasses import dataclass, replace
from datetime import datetime
import logging
from pathlib import Path
import threading
import time
from typing import Dict, Optional

from jittersim.config.models import Device, Profile
from jittersim.config.storage import ConfigRepository, DatabasePath, StorageError
from jittersim.networking.helper import HelperClient, HelperClientError

from .codec import plan_to_payload
from .compiler import PlanError, compile_plan
from .randomizer import (
    outage_duration_seconds,
    outage_interval_seconds,
    outage_is_selected,
    reroll_interval_seconds,
    reroll_profile,
    seeded_generator,
)
from .schedules import active_schedule
from .website import build_website_filters


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

    def tick(
        self,
        *,
        repository: Optional[ConfigRepository] = None,
        now: Optional[float] = None,
    ) -> bool:
        """Recompute and apply a changed runtime plan once."""

        with self._lock:
            if self._paused or self._helper_client is None:
                return False
        current_time = time.time() if now is None else now
        owned_repository = repository is None
        active_repository = repository or ConfigRepository(self._database_path)
        try:
            plan = self._build_plan(repository=active_repository, now=current_time)
            payload = plan_to_payload(plan)
            with self._lock:
                if payload == self._current_payload:
                    return False
            self._helper_client.apply(plan=plan)
            with self._lock:
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
        active_timers = {}
        for timer in repository.list_active_timers(now=int(now)):
            active_timers.setdefault(timer.device_id, timer)
        moment = datetime.fromtimestamp(now).replace(second=0, microsecond=0)
        runtime_devices = []
        runtime_profiles = {}
        blocked_addresses = []
        active_device_ids = set()

        for device in repository.list_devices():
            timer = active_timers.get(device.id)
            schedule = None if timer is not None else active_schedule(
                schedules=schedules,
                device_id=device.id,
                moment=moment,
            )
            selected_profile_id = (
                timer.profile_id
                if timer is not None
                else device.profile_id if schedule is None else schedule.profile_id
            )
            selected_profile = profiles.get(selected_profile_id)
            if selected_profile is None:
                raise PlanError(f"device {device.id} references missing profile {selected_profile_id}")
            active_device_ids.add(device.id)
            roll = self._rolls.get(device.id)
            if roll is None or roll.profile_id != selected_profile.id:
                roll = _DeviceRollState(
                    profile_id=selected_profile.id,
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
        )
        return compile_plan(
            devices=runtime_devices,
            profiles=runtime_profiles,
            mac_policy=repository.get_mac_policy(),
            global_blocked_addresses=global_blocked,
            website_filters=website_filters,
            blocked_device_addresses=blocked_addresses,
        )

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_seconds):
            self.tick()

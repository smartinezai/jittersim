"""SQLite persistence for validated jittersim configuration."""

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import time
from typing import Optional, Sequence, Union

from .models import (
    Device,
    DomainObservation,
    Impairment,
    MacPolicy,
    Profile,
    Randomness,
    RuntimeState,
    Schedule,
    Timer,
    ValidationError,
    WebsitePolicy,
)


class StorageError(RuntimeError):
    """Raised when stored configuration cannot be read or written safely."""


DatabasePath = Union[str, Path]
_DOMAIN_RETENTION_SECONDS = 30 * 24 * 60 * 60
_MAX_DOMAIN_OBSERVATIONS = 20_000
_MAX_OBSERVATIONS_PER_DEVICE = 50


def _serialize_impairment(impairment: Impairment) -> str:
    return json.dumps(asdict(impairment), sort_keys=True)


def _deserialize_randomness(data: dict, field_name: str) -> None:
    value = data.get(field_name)
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must contain an object")
    data[field_name] = Randomness(**value)


def _deserialize_impairment(value: str) -> Impairment:
    try:
        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError("impairment JSON must contain an object")
        for field_name in (
            "delay_randomness",
            "jitter_randomness",
            "loss_randomness",
            "bandwidth_randomness",
            "reorder_randomness",
            "duplicate_randomness",
            "corrupt_randomness",
        ):
            _deserialize_randomness(data, field_name)
        return Impairment(**data)
    except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
        raise StorageError("stored impairment is malformed") from error


def _serialize_profile_settings(profile: Profile) -> str:
    settings = asdict(profile)
    for field_name in ("id", "name", "upload", "download", "random_seed"):
        settings.pop(field_name, None)
    return json.dumps(settings, sort_keys=True)


def _deserialize_profile_settings(value: str) -> dict:
    data = json.loads(value or "{}")
    if not isinstance(data, dict):
        raise ValueError("profile settings JSON must contain an object")
    for field_name in (
        "reroll_interval_randomness",
        "outage_duration_randomness",
        "outage_interval_randomness",
        "website_randomness",
    ):
        _deserialize_randomness(data, field_name)
    return data


class ConfigRepository:
    """Store profiles and device assignments in a local SQLite database."""

    def __init__(self, database_path: DatabasePath) -> None:
        self._connection = sqlite3.connect(str(database_path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize_schema()

    def __enter__(self) -> "ConfigRepository":
        return self

    def __exit__(self, _exception_type: object, _exception: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the database connection owned by this repository."""

        self._connection.close()

    def _initialize_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                upload_json TEXT NOT NULL,
                download_json TEXT NOT NULL,
                random_seed INTEGER,
                settings_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                ipv4_address TEXT NOT NULL UNIQUE,
                profile_id TEXT NOT NULL REFERENCES profiles(id),
                mac_address TEXT
            );
            CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                days_json TEXT NOT NULL,
                start_minute INTEGER NOT NULL,
                end_minute INTEGER NOT NULL,
                enabled INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS timers (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                started_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                enabled INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS network_policies (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                mac_mode TEXT NOT NULL,
                mac_allowlist_json TEXT NOT NULL,
                mac_denylist_json TEXT NOT NULL,
                website_mode TEXT NOT NULL,
                website_allowed_json TEXT NOT NULL,
                website_blocked_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runtime_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                configuration_version INTEGER NOT NULL,
                observed_state TEXT NOT NULL,
                last_action TEXT,
                last_result TEXT,
                last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS domain_observations (
                ipv4_address TEXT NOT NULL,
                domain TEXT NOT NULL,
                query_count INTEGER NOT NULL,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                PRIMARY KEY (ipv4_address, domain)
            );
            CREATE INDEX IF NOT EXISTS domain_observations_last_seen_idx
                ON domain_observations (last_seen);
            INSERT OR IGNORE INTO runtime_state (
                id, configuration_version, observed_state
            ) VALUES (1, 0, 'unknown');
            INSERT OR IGNORE INTO network_policies (
                id, mac_mode, mac_allowlist_json, mac_denylist_json,
                website_mode, website_allowed_json, website_blocked_json
            ) VALUES (1, 'disabled', '[]', '[]', 'unrestricted', '[]', '[]');
            """
        )
        self._add_column_if_missing("profiles", "settings_json", "TEXT NOT NULL DEFAULT '{}'")
        self._add_column_if_missing("devices", "mac_address", "TEXT")
        self._connection.commit()

    def _add_column_if_missing(self, table_name: str, column_name: str, column_definition: str) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            self._connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )

    def save_profile(self, profile: Profile) -> None:
        """Insert or update one validated profile."""

        try:
            self._connection.execute(
                """
                INSERT INTO profiles (
                    id, name, upload_json, download_json, random_seed, settings_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    upload_json = excluded.upload_json,
                    download_json = excluded.download_json,
                    random_seed = excluded.random_seed,
                    settings_json = excluded.settings_json
                """,
                (
                    profile.id,
                    profile.name,
                    _serialize_impairment(profile.upload),
                    _serialize_impairment(profile.download),
                    profile.random_seed,
                    _serialize_profile_settings(profile),
                ),
            )
            self._bump_configuration_version()
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save profile {profile.id}") from error

    def save_device(self, device: Device) -> None:
        """Insert or update one validated device assignment."""

        try:
            self._connection.execute(
                """
                INSERT INTO devices (id, name, ipv4_address, profile_id, mac_address)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    ipv4_address = excluded.ipv4_address,
                    profile_id = excluded.profile_id,
                    mac_address = excluded.mac_address
                """,
                (
                    device.id,
                    device.name,
                    str(device.ipv4_address),
                    device.profile_id,
                    device.mac_address,
                ),
            )
            self._bump_configuration_version()
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save device {device.id}") from error

    def get_runtime_state(self) -> RuntimeState:
        """Return the desired-configuration version and observed helper state."""

        row = self._connection.execute(
            """
            SELECT configuration_version, observed_state, last_action, last_result, last_error
            FROM runtime_state
            WHERE id = 1
            """
        ).fetchone()
        if row is None:
            raise StorageError("runtime state is missing")
        try:
            return RuntimeState(
                configuration_version=row["configuration_version"],
                observed_state=row["observed_state"],
                last_action=row["last_action"],
                last_result=row["last_result"],
                last_error=row["last_error"],
            )
        except (KeyError, TypeError, ValidationError) as error:
            raise StorageError("runtime state is malformed") from error

    def update_runtime_state(
        self,
        *,
        observed_state: str,
        last_action: Optional[str],
        last_result: Optional[str],
        last_error: Optional[str],
    ) -> RuntimeState:
        """Persist one observed helper state without changing desired configuration."""

        current = self.get_runtime_state()
        updated = RuntimeState(
            configuration_version=current.configuration_version,
            observed_state=observed_state,
            last_action=last_action,
            last_result=last_result,
            last_error=last_error,
        )
        self._connection.execute(
            """
            UPDATE runtime_state
            SET observed_state = ?, last_action = ?, last_result = ?, last_error = ?
            WHERE id = 1
            """,
            (
                updated.observed_state,
                updated.last_action,
                updated.last_result,
                updated.last_error,
            ),
        )
        self._connection.commit()
        return updated

    def _bump_configuration_version(self) -> None:
        self._connection.execute(
            """
            UPDATE runtime_state
            SET configuration_version = configuration_version + 1,
                observed_state = 'unknown',
                last_action = NULL,
                last_result = NULL,
                last_error = NULL
            WHERE id = 1
            """
        )

    def get_profile(self, profile_id: str) -> Optional[Profile]:
        """Return one profile or `None` when it is not stored."""

        row = self._connection.execute(
            """
            SELECT id, name, upload_json, download_json, random_seed, settings_json
            FROM profiles WHERE id = ?
            """,
            (profile_id,),
        ).fetchone()
        if row is None:
            return None
        return self._profile_from_row(row)

    def get_device(self, device_id: str) -> Optional[Device]:
        """Return one device assignment or `None` when it is not stored."""

        row = self._connection.execute(
            "SELECT id, name, ipv4_address, profile_id, mac_address FROM devices WHERE id = ?",
            (device_id,),
        ).fetchone()
        if row is None:
            return None
        return self._device_from_row(row)

    def get_device_by_ipv4_address(self, ipv4_address: str) -> Optional[Device]:
        """Return the device assignment using one IPv4 address or `None`."""

        row = self._connection.execute(
            "SELECT id, name, ipv4_address, profile_id, mac_address FROM devices WHERE ipv4_address = ?",
            (ipv4_address,),
        ).fetchone()
        if row is None:
            return None
        return self._device_from_row(row)

    def list_profiles(self) -> tuple:
        """Return all stored profiles in stable identifier order."""

        rows = self._connection.execute(
            "SELECT id, name, upload_json, download_json, random_seed "
            ", settings_json "
            "FROM profiles ORDER BY id"
        ).fetchall()
        return tuple(self._profile_from_row(row) for row in rows)

    def list_devices(self) -> tuple:
        """Return all stored device assignments in stable identifier order."""

        rows = self._connection.execute(
            "SELECT id, name, ipv4_address, profile_id, mac_address FROM devices ORDER BY id"
        ).fetchall()
        return tuple(self._device_from_row(row) for row in rows)

    def save_schedule(self, schedule: Schedule) -> None:
        """Insert or update one repeating schedule."""

        try:
            self._connection.execute(
                """
                INSERT INTO schedules (
                    id, device_id, profile_id, days_json, start_minute, end_minute, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    device_id = excluded.device_id,
                    profile_id = excluded.profile_id,
                    days_json = excluded.days_json,
                    start_minute = excluded.start_minute,
                    end_minute = excluded.end_minute,
                    enabled = excluded.enabled
                """,
                (
                    schedule.id,
                    schedule.device_id,
                    schedule.profile_id,
                    json.dumps(list(schedule.days_of_week)),
                    schedule.start_minute,
                    schedule.end_minute,
                    int(schedule.enabled),
                ),
            )
            self._bump_configuration_version()
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save schedule {schedule.id}") from error

    def delete_schedule(self, schedule_id: str) -> None:
        """Delete one schedule when it exists."""

        cursor = self._connection.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        if cursor.rowcount:
            self._bump_configuration_version()
            self._connection.commit()

    def list_schedules(self) -> tuple:
        """Return all schedules in stable identifier order."""

        rows = self._connection.execute(
            """
            SELECT id, device_id, profile_id, days_json, start_minute, end_minute, enabled
            FROM schedules ORDER BY id
            """
        ).fetchall()
        return tuple(self._schedule_from_row(row) for row in rows)

    def save_timer(self, timer: Timer) -> None:
        """Persist one temporary profile override."""

        try:
            self._connection.execute(
                "UPDATE timers SET enabled = 0 WHERE device_id = ? AND enabled = 1",
                (timer.device_id,),
            )
            self._connection.execute(
                """
                INSERT INTO timers (
                    id, device_id, profile_id, started_at, expires_at, enabled
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    device_id = excluded.device_id,
                    profile_id = excluded.profile_id,
                    started_at = excluded.started_at,
                    expires_at = excluded.expires_at,
                    enabled = excluded.enabled
                """,
                (
                    timer.id,
                    timer.device_id,
                    timer.profile_id,
                    timer.started_at,
                    timer.expires_at,
                    int(timer.enabled),
                ),
            )
            self._bump_configuration_version()
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save timer {timer.id}") from error

    def delete_timer(self, timer_id: str) -> bool:
        """Disable one temporary profile override when it exists."""

        cursor = self._connection.execute(
            "UPDATE timers SET enabled = 0 WHERE id = ? AND enabled = 1",
            (timer_id,),
        )
        if cursor.rowcount:
            self._bump_configuration_version()
            self._connection.commit()
            return True
        return False

    def list_timers(self) -> tuple:
        """Return saved timers in newest-first order."""

        rows = self._connection.execute(
            """
            SELECT id, device_id, profile_id, started_at, expires_at, enabled
            FROM timers ORDER BY started_at DESC, id DESC
            """
        ).fetchall()
        return tuple(self._timer_from_row(row) for row in rows)

    def list_active_timers(self, *, now: int) -> tuple:
        """Return enabled timers that are active at one Unix timestamp."""

        rows = self._connection.execute(
            """
            SELECT id, device_id, profile_id, started_at, expires_at, enabled
            FROM timers
            WHERE enabled = 1 AND started_at <= ? AND expires_at > ?
            ORDER BY started_at DESC, id DESC
            """,
            (now, now),
        ).fetchall()
        return tuple(self._timer_from_row(row) for row in rows)

    def get_mac_policy(self) -> MacPolicy:
        """Return the saved MAC filtering policy."""

        row = self._connection.execute(
            """
            SELECT mac_mode, mac_allowlist_json, mac_denylist_json
            FROM network_policies WHERE id = 1
            """
        ).fetchone()
        if row is None:
            raise StorageError("network policy is missing")
        try:
            return MacPolicy(
                mode=row["mac_mode"],
                allowlist=tuple(json.loads(row["mac_allowlist_json"])),
                denylist=tuple(json.loads(row["mac_denylist_json"])),
            )
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
            raise StorageError("stored MAC policy is malformed") from error

    def save_mac_policy(self, policy: MacPolicy) -> None:
        """Persist MAC filtering rules."""

        self._connection.execute(
            """
            UPDATE network_policies
            SET mac_mode = ?, mac_allowlist_json = ?, mac_denylist_json = ?
            WHERE id = 1
            """,
            (policy.mode, json.dumps(list(policy.allowlist)), json.dumps(list(policy.denylist))),
        )
        self._bump_configuration_version()
        self._connection.commit()

    def get_website_policy(self) -> WebsitePolicy:
        """Return the saved global website policy."""

        row = self._connection.execute(
            """
            SELECT website_mode, website_allowed_json, website_blocked_json
            FROM network_policies WHERE id = 1
            """
        ).fetchone()
        if row is None:
            raise StorageError("network policy is missing")
        try:
            return WebsitePolicy(
                mode=row["website_mode"],
                allowed_domains=tuple(json.loads(row["website_allowed_json"])),
                blocked_domains=tuple(json.loads(row["website_blocked_json"])),
            )
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
            raise StorageError("stored website policy is malformed") from error

    def save_website_policy(self, policy: WebsitePolicy) -> None:
        """Persist global website rules."""

        self._connection.execute(
            """
            UPDATE network_policies
            SET website_mode = ?, website_allowed_json = ?, website_blocked_json = ?
            WHERE id = 1
            """,
            (
                policy.mode,
                json.dumps(list(policy.allowed_domains)),
                json.dumps(list(policy.blocked_domains)),
            ),
        )
        self._bump_configuration_version()
        self._connection.commit()

    def save_domain_observations(
        self,
        observations: Sequence[DomainObservation],
        *,
        now: Optional[int] = None,
    ) -> None:
        """Merge one bounded DNS sample without changing desired configuration."""

        current_time = int(time.time()) if now is None else now
        try:
            for observation in observations:
                self._connection.execute(
                    """
                    INSERT INTO domain_observations (
                        ipv4_address, domain, query_count, first_seen, last_seen
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(ipv4_address, domain) DO UPDATE SET
                        query_count = MIN(
                            domain_observations.query_count + excluded.query_count,
                            ?
                        ),
                        first_seen = MIN(
                            domain_observations.first_seen, excluded.first_seen
                        ),
                        last_seen = MAX(
                            domain_observations.last_seen, excluded.last_seen
                        )
                    """,
                    (
                        str(observation.ipv4_address),
                        observation.domain,
                        observation.query_count,
                        observation.first_seen,
                        observation.last_seen,
                        1_000_000_000,
                    ),
                )
            self._prune_domain_observations(now=current_time)
            self._connection.commit()
        except (sqlite3.IntegrityError, sqlite3.OperationalError) as error:
            self._connection.rollback()
            raise StorageError("could not save domain observations") from error

    def list_domain_observations(self) -> tuple:
        """Return recent domain aggregates ordered by device and query count."""

        rows = self._connection.execute(
            """
            SELECT ipv4_address, domain, query_count, first_seen, last_seen
            FROM domain_observations
            ORDER BY ipv4_address, query_count DESC, domain
            """
        ).fetchall()
        observations = []
        counts_by_device = {}
        for row in rows:
            ipv4_address = row["ipv4_address"]
            count = counts_by_device.get(ipv4_address, 0)
            if count >= _MAX_OBSERVATIONS_PER_DEVICE:
                continue
            observations.append(
                DomainObservation(
                    ipv4_address=ipv4_address,
                    domain=row["domain"],
                    query_count=row["query_count"],
                    first_seen=row["first_seen"],
                    last_seen=row["last_seen"],
                )
            )
            counts_by_device[ipv4_address] = count + 1
        return tuple(observations)

    def _prune_domain_observations(self, *, now: int) -> None:
        cutoff = max(0, now - _DOMAIN_RETENTION_SECONDS)
        self._connection.execute(
            "DELETE FROM domain_observations WHERE last_seen < ?",
            (cutoff,),
        )
        self._connection.execute(
            """
            DELETE FROM domain_observations
            WHERE rowid IN (
                SELECT rowid
                FROM domain_observations
                ORDER BY last_seen DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (_MAX_DOMAIN_OBSERVATIONS,),
        )

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> Profile:
        try:
            return Profile(
                id=row["id"],
                name=row["name"],
                upload=_deserialize_impairment(row["upload_json"]),
                download=_deserialize_impairment(row["download_json"]),
                random_seed=row["random_seed"],
                **_deserialize_profile_settings(row["settings_json"]),
            )
        except (KeyError, TypeError, ValidationError) as error:
            raise StorageError("stored profile is malformed") from error

    @staticmethod
    def _device_from_row(row: sqlite3.Row) -> Device:
        try:
            return Device(
                id=row["id"],
                name=row["name"],
                ipv4_address=row["ipv4_address"],
                profile_id=row["profile_id"],
                mac_address=row["mac_address"],
            )
        except (KeyError, TypeError, ValidationError) as error:
            raise StorageError("stored device is malformed") from error

    @staticmethod
    def _schedule_from_row(row: sqlite3.Row) -> Schedule:
        try:
            days = json.loads(row["days_json"])
            if not isinstance(days, list):
                raise ValueError("schedule days must be a list")
            return Schedule(
                id=row["id"],
                device_id=row["device_id"],
                profile_id=row["profile_id"],
                days_of_week=tuple(days),
                start_minute=row["start_minute"],
                end_minute=row["end_minute"],
                enabled=bool(row["enabled"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
            raise StorageError("stored schedule is malformed") from error

    @staticmethod
    def _timer_from_row(row: sqlite3.Row) -> Timer:
        try:
            return Timer(
                id=row["id"],
                device_id=row["device_id"],
                profile_id=row["profile_id"],
                started_at=row["started_at"],
                expires_at=row["expires_at"],
                enabled=bool(row["enabled"]),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise StorageError("stored timer is malformed") from error

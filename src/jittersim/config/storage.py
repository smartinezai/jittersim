"""SQLite persistence for validated jittersim configuration."""

import json
import sqlite3
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Sequence, Union

from .models import (
    DataLimit,
    Device,
    DeviceGroup,
    DomainObservation,
    GroupDataLimit,
    Impairment,
    MacPolicy,
    Profile,
    Progression,
    Randomness,
    RuntimeState,
    Schedule,
    ServiceQuota,
    TimeQuota,
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
            CREATE TABLE IF NOT EXISTS device_groups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                profile_id TEXT REFERENCES profiles(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS device_group_members (
                group_id TEXT NOT NULL REFERENCES device_groups(id) ON DELETE CASCADE,
                device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE
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
            CREATE TABLE IF NOT EXISTS progressions (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                start_profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                end_profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                started_at INTEGER NOT NULL,
                duration_seconds INTEGER NOT NULL,
                mode TEXT NOT NULL,
                traffic_target_queries INTEGER NOT NULL,
                traffic_baseline_queries INTEGER NOT NULL,
                traffic_target_bytes INTEGER NOT NULL DEFAULT 0,
                traffic_baseline_bytes INTEGER NOT NULL DEFAULT 0,
                website_domains_json TEXT NOT NULL,
                enabled INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS data_limits (
                device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                limit_bytes INTEGER NOT NULL,
                reset_mode TEXT NOT NULL,
                reset_interval_seconds INTEGER NOT NULL,
                reset_times_json TEXT NOT NULL,
                used_bytes INTEGER NOT NULL,
                counter_bytes INTEGER NOT NULL,
                last_reset_at INTEGER NOT NULL,
                enabled INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS group_data_limits (
                group_id TEXT PRIMARY KEY REFERENCES device_groups(id) ON DELETE CASCADE,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                limit_bytes INTEGER NOT NULL,
                reset_mode TEXT NOT NULL,
                reset_interval_seconds INTEGER NOT NULL,
                reset_times_json TEXT NOT NULL,
                used_bytes INTEGER NOT NULL,
                counter_bytes INTEGER NOT NULL,
                last_reset_at INTEGER NOT NULL,
                enabled INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS service_quotas (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                domains_json TEXT NOT NULL,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                limit_bytes INTEGER NOT NULL,
                weight_percent INTEGER NOT NULL DEFAULT 100,
                reset_mode TEXT NOT NULL,
                reset_interval_seconds INTEGER NOT NULL,
                reset_times_json TEXT NOT NULL,
                used_bytes INTEGER NOT NULL,
                counter_bytes INTEGER NOT NULL,
                last_reset_at INTEGER NOT NULL,
                enabled INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS time_quotas (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                domains_json TEXT NOT NULL,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                limit_seconds INTEGER NOT NULL,
                window_duration_seconds INTEGER NOT NULL,
                reset_mode TEXT NOT NULL,
                reset_interval_seconds INTEGER NOT NULL,
                reset_times_json TEXT NOT NULL,
                used_seconds INTEGER NOT NULL,
                counter_bytes INTEGER NOT NULL,
                last_sample_at INTEGER NOT NULL,
                last_reset_at INTEGER NOT NULL,
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
            CREATE TABLE IF NOT EXISTS action_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                happened_at INTEGER NOT NULL,
                action TEXT NOT NULL,
                status_code INTEGER NOT NULL
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
        self._add_column_if_missing("progressions", "traffic_target_bytes", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("progressions", "traffic_baseline_bytes", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("service_quotas", "weight_percent", "INTEGER NOT NULL DEFAULT 100")
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

    def record_action(self, *, action: str, status_code: int) -> None:
        """Store a bounded action name and outcome without request contents."""

        if (
            not isinstance(action, str)
            or not action.startswith("/api/")
            or len(action) > 100
        ):
            raise StorageError("action name is invalid")
        if isinstance(status_code, bool) or not isinstance(status_code, int):
            raise StorageError("action status is invalid")
        if status_code < 100 or status_code > 599:
            raise StorageError("action status is invalid")
        try:
            self._connection.execute(
                "INSERT INTO action_history (happened_at, action, status_code) VALUES (?, ?, ?)",
                (int(time.time()), action, status_code),
            )
            self._connection.execute(
                "DELETE FROM action_history WHERE id NOT IN "
                "(SELECT id FROM action_history ORDER BY id DESC LIMIT 500)"
            )
            self._connection.commit()
        except sqlite3.Error as error:
            self._connection.rollback()
            raise StorageError("could not record action history") from error

    def list_recent_actions(self, *, limit: int = 50) -> tuple:
        """Return the newest bounded action records without sensitive request data."""

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise StorageError("action history limit must be between 1 and 100")
        try:
            rows = self._connection.execute(
                "SELECT happened_at, action, status_code FROM action_history "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return tuple(dict(row) for row in rows)
        except sqlite3.Error as error:
            raise StorageError("could not read action history") from error

    def backup_bytes(self) -> bytes:
        """Create a consistent SQLite snapshot for an administrator download."""

        temporary_path = None
        target = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="jittersim-backup-", suffix=".db", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
            target = sqlite3.connect(str(temporary_path))
            self._connection.backup(target)
            target.close()
            target = None
            return temporary_path.read_bytes()
        except (OSError, sqlite3.Error) as error:
            raise StorageError("could not create configuration backup") from error
        finally:
            if target is not None:
                target.close()
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _bump_configuration_version(self) -> None:
        # Keep the last network action: startup recovery uses it to re-apply
        # a plan or keep an explicit reset after later settings changes.
        self._connection.execute(
            """
            UPDATE runtime_state
            SET configuration_version = configuration_version + 1,
                observed_state = 'unknown'
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

    def get_device_group(self, group_id: str) -> Optional[DeviceGroup]:
        """Return one device group or `None` when it is not stored."""

        row = self._connection.execute(
            """
            SELECT id, name, profile_id,
                   COALESCE(
                       (SELECT json_group_array(device_id)
                        FROM device_group_members
                        WHERE group_id = device_groups.id),
                       '[]'
                   ) AS device_ids_json
            FROM device_groups WHERE id = ?
            """,
            (group_id,),
        ).fetchone()
        return None if row is None else self._device_group_from_row(row)

    def list_device_groups(self) -> tuple:
        """Return device groups in stable identifier order."""

        rows = self._connection.execute(
            """
            SELECT id, name, profile_id,
                   COALESCE(
                       (SELECT json_group_array(device_id)
                        FROM device_group_members
                        WHERE group_id = device_groups.id),
                       '[]'
                   ) AS device_ids_json
            FROM device_groups ORDER BY id
            """
        ).fetchall()
        return tuple(self._device_group_from_row(row) for row in rows)

    def save_device_group(self, group: DeviceGroup) -> None:
        """Insert or update one group and its exclusive device membership."""

        missing_devices = [device_id for device_id in group.device_ids if self.get_device(device_id) is None]
        if missing_devices:
            raise StorageError(f"device group references missing devices: {', '.join(missing_devices)}")
        try:
            self._connection.execute(
                """
                INSERT INTO device_groups (id, name, profile_id)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    profile_id = excluded.profile_id
                """,
                (group.id, group.name, group.profile_id),
            )
            self._connection.execute(
                "DELETE FROM device_group_members WHERE group_id = ?",
                (group.id,),
            )
            self._connection.executemany(
                "INSERT INTO device_group_members (group_id, device_id) VALUES (?, ?)",
                ((group.id, device_id) for device_id in group.device_ids),
            )
            self._bump_configuration_version()
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save device group {group.id}") from error

    def delete_device_group(self, group_id: str) -> bool:
        """Delete one group and release its device members."""

        cursor = self._connection.execute("DELETE FROM device_groups WHERE id = ?", (group_id,))
        if cursor.rowcount:
            self._bump_configuration_version()
            self._connection.commit()
            return True
        return False

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

    def save_progression(self, progression: Progression) -> None:
        """Persist one progression and replace other active overrides for its device."""

        try:
            self._connection.execute(
                "UPDATE progressions SET enabled = 0 WHERE device_id = ? AND enabled = 1",
                (progression.device_id,),
            )
            self._connection.execute(
                "UPDATE timers SET enabled = 0 WHERE device_id = ? AND enabled = 1",
                (progression.device_id,),
            )
            self._connection.execute(
                """
                INSERT INTO progressions (
                    id, device_id, start_profile_id, end_profile_id, started_at,
                    duration_seconds, mode, traffic_target_queries,
                    traffic_baseline_queries, traffic_target_bytes,
                    traffic_baseline_bytes, website_domains_json, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    device_id = excluded.device_id,
                    start_profile_id = excluded.start_profile_id,
                    end_profile_id = excluded.end_profile_id,
                    started_at = excluded.started_at,
                    duration_seconds = excluded.duration_seconds,
                    mode = excluded.mode,
                    traffic_target_queries = excluded.traffic_target_queries,
                    traffic_baseline_queries = excluded.traffic_baseline_queries,
                    traffic_target_bytes = excluded.traffic_target_bytes,
                    traffic_baseline_bytes = excluded.traffic_baseline_bytes,
                    website_domains_json = excluded.website_domains_json,
                    enabled = excluded.enabled
                """,
                (
                    progression.id,
                    progression.device_id,
                    progression.start_profile_id,
                    progression.end_profile_id,
                    progression.started_at,
                    progression.duration_seconds,
                    progression.mode,
                    progression.traffic_target_queries,
                    progression.traffic_baseline_queries,
                    progression.traffic_target_bytes,
                    progression.traffic_baseline_bytes,
                    json.dumps(list(progression.website_domains)),
                    int(progression.enabled),
                ),
            )
            self._bump_configuration_version()
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save progression {progression.id}") from error

    def delete_progression(self, progression_id: str) -> bool:
        """Disable one progression when it is active."""

        cursor = self._connection.execute(
            "UPDATE progressions SET enabled = 0 WHERE id = ? AND enabled = 1",
            (progression_id,),
        )
        if cursor.rowcount:
            self._bump_configuration_version()
            self._connection.commit()
            return True
        return False

    def list_progressions(self) -> tuple:
        """Return saved progressions in newest-first order."""

        rows = self._connection.execute(
            """
            SELECT id, device_id, start_profile_id, end_profile_id, started_at,
                   duration_seconds, mode, traffic_target_queries,
                   traffic_baseline_queries, traffic_target_bytes,
                   traffic_baseline_bytes, website_domains_json, enabled
            FROM progressions ORDER BY started_at DESC, id DESC
            """
        ).fetchall()
        return tuple(self._progression_from_row(row) for row in rows)

    def list_active_progressions(self, *, now: int) -> tuple:
        """Return enabled progressions that have not reached their time limit."""

        rows = self._connection.execute(
            """
            SELECT id, device_id, start_profile_id, end_profile_id, started_at,
                   duration_seconds, mode, traffic_target_queries,
                   traffic_baseline_queries, traffic_target_bytes,
                   traffic_baseline_bytes, website_domains_json, enabled
            FROM progressions
            WHERE enabled = 1 AND started_at <= ?
              AND started_at + duration_seconds > ?
            ORDER BY started_at DESC, id DESC
            """,
            (now, now),
        ).fetchall()
        return tuple(self._progression_from_row(row) for row in rows)

    def total_domain_queries(self, ipv4_address: str) -> int:
        """Return the bounded DNS query total for one observed client."""

        row = self._connection.execute(
            """
            SELECT COALESCE(SUM(query_count), 0) AS total
            FROM domain_observations WHERE ipv4_address = ?
            """,
            (ipv4_address,),
        ).fetchone()
        return int(row["total"] if row is not None else 0)

    def get_data_limit(self, device_id: str) -> Optional[DataLimit]:
        """Return one device quota or `None` when it is not stored."""

        row = self._connection.execute(
            """
            SELECT device_id, profile_id, limit_bytes, reset_mode,
                   reset_interval_seconds, reset_times_json, used_bytes,
                   counter_bytes, last_reset_at, enabled
            FROM data_limits WHERE device_id = ?
            """,
            (device_id,),
        ).fetchone()
        return None if row is None else self._data_limit_from_row(row)

    def list_data_limits(self) -> tuple:
        """Return device quotas in stable device order."""

        rows = self._connection.execute(
            """
            SELECT device_id, profile_id, limit_bytes, reset_mode,
                   reset_interval_seconds, reset_times_json, used_bytes,
                   counter_bytes, last_reset_at, enabled
            FROM data_limits ORDER BY device_id
            """
        ).fetchall()
        return tuple(self._data_limit_from_row(row) for row in rows)

    def save_data_limit(self, data_limit: DataLimit) -> None:
        """Insert or update one device quota without discarding its usage."""

        try:
            self._connection.execute(
                """
                INSERT INTO data_limits (
                    device_id, profile_id, limit_bytes, reset_mode,
                    reset_interval_seconds, reset_times_json, used_bytes,
                    counter_bytes, last_reset_at, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    profile_id = excluded.profile_id,
                    limit_bytes = excluded.limit_bytes,
                    reset_mode = excluded.reset_mode,
                    reset_interval_seconds = excluded.reset_interval_seconds,
                    reset_times_json = excluded.reset_times_json,
                    used_bytes = excluded.used_bytes,
                    counter_bytes = excluded.counter_bytes,
                    last_reset_at = excluded.last_reset_at,
                    enabled = excluded.enabled
                """,
                (
                    data_limit.device_id,
                    data_limit.profile_id,
                    data_limit.limit_bytes,
                    data_limit.reset_mode,
                    data_limit.reset_interval_seconds,
                    json.dumps(list(data_limit.reset_times)),
                    data_limit.used_bytes,
                    data_limit.counter_bytes,
                    data_limit.last_reset_at,
                    int(data_limit.enabled),
                ),
            )
            self._bump_configuration_version()
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save data limit for {data_limit.device_id}") from error

    def restart_usage_counters(self) -> None:
        """Record that every helper traffic counter restarted at zero."""

        for table in ("data_limits", "group_data_limits", "service_quotas", "time_quotas"):
            self._connection.execute(f"UPDATE {table} SET counter_bytes = 0")
        self._connection.commit()

    def reset_data_limit_usage(
        self,
        device_id: str,
        *,
        counter_bytes: Optional[int] = None,
        reset_at: Optional[int] = None,
    ) -> bool:
        """Reset one device quota while preserving its limit and fallback profile."""

        values = []
        assignments = ["used_bytes = 0"]
        if counter_bytes is not None:
            assignments.append("counter_bytes = ?")
            values.append(counter_bytes)
        if reset_at is not None:
            assignments.append("last_reset_at = ?")
            values.append(reset_at)
        values.append(device_id)
        cursor = self._connection.execute(
            f"UPDATE data_limits SET {', '.join(assignments)} WHERE device_id = ?",
            tuple(values),
        )
        if cursor.rowcount:
            self._connection.commit()
            return True
        return False

    def record_data_usage(self, device_id: str, counter_bytes: int) -> Optional[DataLimit]:
        """Merge one absolute device counter into the persisted quota usage."""

        data_limit = self.get_data_limit(device_id)
        if data_limit is None:
            return None
        if not isinstance(counter_bytes, int) or counter_bytes < 0:
            raise StorageError("device traffic counter is invalid")
        delta = counter_bytes if counter_bytes < data_limit.counter_bytes else counter_bytes - data_limit.counter_bytes
        updated_used = min(2**63 - 1, data_limit.used_bytes + delta)
        self._connection.execute(
            """
            UPDATE data_limits SET used_bytes = ?, counter_bytes = ?
            WHERE device_id = ?
            """,
            (updated_used, counter_bytes, device_id),
        )
        self._connection.commit()
        return DataLimit(
            device_id=data_limit.device_id,
            profile_id=data_limit.profile_id,
            limit_bytes=data_limit.limit_bytes,
            reset_mode=data_limit.reset_mode,
            reset_interval_seconds=data_limit.reset_interval_seconds,
            reset_times=data_limit.reset_times,
            used_bytes=updated_used,
            counter_bytes=counter_bytes,
            last_reset_at=data_limit.last_reset_at,
            enabled=data_limit.enabled,
        )

    def get_group_data_limit(self, group_id: str) -> Optional[GroupDataLimit]:
        """Return one shared group quota or `None` when it is not stored."""

        row = self._connection.execute(
            """
            SELECT group_id, profile_id, limit_bytes, reset_mode,
                   reset_interval_seconds, reset_times_json, used_bytes,
                   counter_bytes, last_reset_at, enabled
            FROM group_data_limits WHERE group_id = ?
            """,
            (group_id,),
        ).fetchone()
        return None if row is None else self._group_data_limit_from_row(row)

    def list_group_data_limits(self) -> tuple:
        """Return shared group quotas in stable group order."""

        rows = self._connection.execute(
            """
            SELECT group_id, profile_id, limit_bytes, reset_mode,
                   reset_interval_seconds, reset_times_json, used_bytes,
                   counter_bytes, last_reset_at, enabled
            FROM group_data_limits ORDER BY group_id
            """
        ).fetchall()
        return tuple(self._group_data_limit_from_row(row) for row in rows)

    def save_group_data_limit(self, data_limit: GroupDataLimit) -> None:
        """Insert or update one shared group quota without discarding usage."""

        try:
            self._connection.execute(
                """
                INSERT INTO group_data_limits (
                    group_id, profile_id, limit_bytes, reset_mode,
                    reset_interval_seconds, reset_times_json, used_bytes,
                    counter_bytes, last_reset_at, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    profile_id = excluded.profile_id,
                    limit_bytes = excluded.limit_bytes,
                    reset_mode = excluded.reset_mode,
                    reset_interval_seconds = excluded.reset_interval_seconds,
                    reset_times_json = excluded.reset_times_json,
                    used_bytes = excluded.used_bytes,
                    counter_bytes = excluded.counter_bytes,
                    last_reset_at = excluded.last_reset_at,
                    enabled = excluded.enabled
                """,
                (
                    data_limit.group_id,
                    data_limit.profile_id,
                    data_limit.limit_bytes,
                    data_limit.reset_mode,
                    data_limit.reset_interval_seconds,
                    json.dumps(list(data_limit.reset_times)),
                    data_limit.used_bytes,
                    data_limit.counter_bytes,
                    data_limit.last_reset_at,
                    int(data_limit.enabled),
                ),
            )
            self._bump_configuration_version()
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(
                f"could not save group data limit for {data_limit.group_id}"
            ) from error

    def reset_group_data_limit_usage(
        self,
        group_id: str,
        *,
        counter_bytes: Optional[int] = None,
        reset_at: Optional[int] = None,
    ) -> bool:
        """Reset one shared group quota while preserving its configuration."""

        values = []
        assignments = ["used_bytes = 0"]
        if counter_bytes is not None:
            assignments.append("counter_bytes = ?")
            values.append(counter_bytes)
        if reset_at is not None:
            assignments.append("last_reset_at = ?")
            values.append(reset_at)
        values.append(group_id)
        cursor = self._connection.execute(
            f"UPDATE group_data_limits SET {', '.join(assignments)} WHERE group_id = ?",
            tuple(values),
        )
        if cursor.rowcount:
            self._connection.commit()
            return True
        return False

    def record_group_data_usage(
        self,
        group_id: str,
        counter_bytes: int,
    ) -> Optional[GroupDataLimit]:
        """Merge one absolute aggregate counter into a shared group quota."""

        data_limit = self.get_group_data_limit(group_id)
        if data_limit is None:
            return None
        if not isinstance(counter_bytes, int) or counter_bytes < 0:
            raise StorageError("group traffic counter is invalid")
        delta = counter_bytes if counter_bytes < data_limit.counter_bytes else counter_bytes - data_limit.counter_bytes
        updated_used = min(2**63 - 1, data_limit.used_bytes + delta)
        self._connection.execute(
            """
            UPDATE group_data_limits SET used_bytes = ?, counter_bytes = ?
            WHERE group_id = ?
            """,
            (updated_used, counter_bytes, group_id),
        )
        self._connection.commit()
        return GroupDataLimit(
            group_id=data_limit.group_id,
            profile_id=data_limit.profile_id,
            limit_bytes=data_limit.limit_bytes,
            reset_mode=data_limit.reset_mode,
            reset_interval_seconds=data_limit.reset_interval_seconds,
            reset_times=data_limit.reset_times,
            used_bytes=updated_used,
            counter_bytes=counter_bytes,
            last_reset_at=data_limit.last_reset_at,
            enabled=data_limit.enabled,
        )

    def get_service_quota(self, quota_id: str) -> Optional[ServiceQuota]:
        """Return one service quota or `None` when it is not stored."""

        row = self._connection.execute(
            """
            SELECT id, device_id, name, domains_json, profile_id, limit_bytes,
                   weight_percent, reset_mode, reset_interval_seconds, reset_times_json,
                   used_bytes, counter_bytes, last_reset_at, enabled
            FROM service_quotas WHERE id = ?
            """,
            (quota_id,),
        ).fetchone()
        return None if row is None else self._service_quota_from_row(row)

    def list_service_quotas(self) -> tuple:
        """Return service quotas in stable identifier order."""

        rows = self._connection.execute(
            """
            SELECT id, device_id, name, domains_json, profile_id, limit_bytes,
                   weight_percent, reset_mode, reset_interval_seconds, reset_times_json,
                   used_bytes, counter_bytes, last_reset_at, enabled
            FROM service_quotas ORDER BY id
            """
        ).fetchall()
        return tuple(self._service_quota_from_row(row) for row in rows)

    def save_service_quota(self, quota: ServiceQuota) -> None:
        """Insert or update one service quota without discarding its usage."""

        try:
            self._connection.execute(
                """
                INSERT INTO service_quotas (
                    id, device_id, name, domains_json, profile_id, limit_bytes,
                    weight_percent, reset_mode, reset_interval_seconds, reset_times_json,
                    used_bytes, counter_bytes, last_reset_at, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    device_id = excluded.device_id,
                    name = excluded.name,
                    domains_json = excluded.domains_json,
                    profile_id = excluded.profile_id,
                    limit_bytes = excluded.limit_bytes,
                    weight_percent = excluded.weight_percent,
                    reset_mode = excluded.reset_mode,
                    reset_interval_seconds = excluded.reset_interval_seconds,
                    reset_times_json = excluded.reset_times_json,
                    used_bytes = excluded.used_bytes,
                    counter_bytes = excluded.counter_bytes,
                    last_reset_at = excluded.last_reset_at,
                    enabled = excluded.enabled
                """,
                (
                    quota.id,
                    quota.device_id,
                    quota.name,
                    json.dumps(list(quota.domains)),
                    quota.profile_id,
                    quota.limit_bytes,
                    quota.weight_percent,
                    quota.reset_mode,
                    quota.reset_interval_seconds,
                    json.dumps(list(quota.reset_times)),
                    quota.used_bytes,
                    quota.counter_bytes,
                    quota.last_reset_at,
                    int(quota.enabled),
                ),
            )
            self._bump_configuration_version()
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save service quota {quota.id}") from error

    def reset_service_quota_usage(
        self,
        quota_id: str,
        *,
        counter_bytes: Optional[int] = None,
        reset_at: Optional[int] = None,
    ) -> bool:
        """Reset one service quota while preserving its configuration."""

        values = []
        assignments = ["used_bytes = 0"]
        if counter_bytes is not None:
            assignments.append("counter_bytes = ?")
            values.append(counter_bytes)
        if reset_at is not None:
            assignments.append("last_reset_at = ?")
            values.append(reset_at)
        values.append(quota_id)
        cursor = self._connection.execute(
            f"UPDATE service_quotas SET {', '.join(assignments)} WHERE id = ?",
            tuple(values),
        )
        if cursor.rowcount:
            self._connection.commit()
            return True
        return False

    def record_service_usage(
        self,
        quota_id: str,
        counter_bytes: int,
    ) -> Optional[ServiceQuota]:
        """Merge one absolute service counter into persisted quota usage."""

        quota = self.get_service_quota(quota_id)
        if quota is None:
            return None
        if not isinstance(counter_bytes, int) or counter_bytes < 0:
            raise StorageError("service traffic counter is invalid")
        delta = counter_bytes if counter_bytes < quota.counter_bytes else counter_bytes - quota.counter_bytes
        weighted_delta = (delta * quota.weight_percent + 50) // 100
        updated_used = min(2**63 - 1, quota.used_bytes + weighted_delta)
        self._connection.execute(
            """
            UPDATE service_quotas SET used_bytes = ?, counter_bytes = ?
            WHERE id = ?
            """,
            (updated_used, counter_bytes, quota_id),
        )
        self._connection.commit()
        return ServiceQuota(
            id=quota.id,
            device_id=quota.device_id,
            name=quota.name,
            domains=quota.domains,
            profile_id=quota.profile_id,
            limit_bytes=quota.limit_bytes,
            weight_percent=quota.weight_percent,
            reset_mode=quota.reset_mode,
            reset_interval_seconds=quota.reset_interval_seconds,
            reset_times=quota.reset_times,
            used_bytes=updated_used,
            counter_bytes=counter_bytes,
            last_reset_at=quota.last_reset_at,
            enabled=quota.enabled,
        )

    def get_time_quota(self, quota_id: str) -> Optional[TimeQuota]:
        """Return one timed service quota or `None` when it is not stored."""

        row = self._connection.execute(
            """
            SELECT id, device_id, name, domains_json, profile_id, limit_seconds,
                   window_duration_seconds, reset_mode, reset_interval_seconds,
                   reset_times_json, used_seconds, counter_bytes, last_sample_at,
                   last_reset_at, started_at, expires_at, enabled
            FROM time_quotas WHERE id = ?
            """,
            (quota_id,),
        ).fetchone()
        return None if row is None else self._time_quota_from_row(row)

    def list_time_quotas(self) -> tuple:
        """Return timed service quotas in stable identifier order."""

        rows = self._connection.execute(
            """
            SELECT id, device_id, name, domains_json, profile_id, limit_seconds,
                   window_duration_seconds, reset_mode, reset_interval_seconds,
                   reset_times_json, used_seconds, counter_bytes, last_sample_at,
                   last_reset_at, started_at, expires_at, enabled
            FROM time_quotas ORDER BY id
            """
        ).fetchall()
        return tuple(self._time_quota_from_row(row) for row in rows)

    def save_time_quota(self, quota: TimeQuota) -> None:
        """Insert or update one timed service quota without discarding usage."""

        try:
            self._connection.execute(
                """
                INSERT INTO time_quotas (
                    id, device_id, name, domains_json, profile_id, limit_seconds,
                    window_duration_seconds, reset_mode, reset_interval_seconds,
                    reset_times_json, used_seconds, counter_bytes, last_sample_at,
                    last_reset_at, started_at, expires_at, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    device_id = excluded.device_id,
                    name = excluded.name,
                    domains_json = excluded.domains_json,
                    profile_id = excluded.profile_id,
                    limit_seconds = excluded.limit_seconds,
                    window_duration_seconds = excluded.window_duration_seconds,
                    reset_mode = excluded.reset_mode,
                    reset_interval_seconds = excluded.reset_interval_seconds,
                    reset_times_json = excluded.reset_times_json,
                    used_seconds = excluded.used_seconds,
                    counter_bytes = excluded.counter_bytes,
                    last_sample_at = excluded.last_sample_at,
                    last_reset_at = excluded.last_reset_at,
                    started_at = excluded.started_at,
                    expires_at = excluded.expires_at,
                    enabled = excluded.enabled
                """,
                (
                    quota.id,
                    quota.device_id,
                    quota.name,
                    json.dumps(list(quota.domains)),
                    quota.profile_id,
                    quota.limit_seconds,
                    quota.window_duration_seconds,
                    quota.reset_mode,
                    quota.reset_interval_seconds,
                    json.dumps(list(quota.reset_times)),
                    quota.used_seconds,
                    quota.counter_bytes,
                    quota.last_sample_at,
                    quota.last_reset_at,
                    quota.started_at,
                    quota.expires_at,
                    int(quota.enabled),
                ),
            )
            self._bump_configuration_version()
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save time quota {quota.id}") from error

    def reset_time_quota_usage(
        self,
        quota_id: str,
        *,
        counter_bytes: Optional[int] = None,
        reset_at: Optional[int] = None,
    ) -> bool:
        """Reset one timed service quota while preserving its configuration."""

        values = []
        assignments = ["used_seconds = 0"]
        if counter_bytes is not None:
            assignments.append("counter_bytes = ?")
            values.append(counter_bytes)
        if reset_at is not None:
            assignments.extend(("last_sample_at = ?", "last_reset_at = ?"))
            values.extend((reset_at, reset_at))
        values.append(quota_id)
        cursor = self._connection.execute(
            f"UPDATE time_quotas SET {', '.join(assignments)} WHERE id = ?",
            tuple(values),
        )
        if cursor.rowcount:
            self._connection.commit()
            return True
        return False

    def start_time_quota(
        self,
        quota_id: str,
        *,
        started_at: int,
        expires_at: int,
        counter_bytes: Optional[int] = None,
    ) -> Optional[TimeQuota]:
        """Start a fresh timed quota window and clear its current usage."""

        quota = self.get_time_quota(quota_id)
        if quota is None:
            return None
        if (
            not isinstance(started_at, int)
            or not isinstance(expires_at, int)
            or started_at < 1
            or expires_at <= started_at
            or expires_at - started_at > quota.window_duration_seconds
        ):
            raise StorageError("time quota timer is invalid")
        self._connection.execute(
            """
            UPDATE time_quotas
            SET used_seconds = 0,
                counter_bytes = ?,
                last_sample_at = ?,
                last_reset_at = ?,
                started_at = ?,
                expires_at = ?
            WHERE id = ?
            """,
            (
                counter_bytes if counter_bytes is not None else quota.counter_bytes,
                started_at,
                started_at,
                started_at,
                expires_at,
                quota_id,
            ),
        )
        self._connection.commit()
        return self.get_time_quota(quota_id)

    def stop_time_quota(self, quota_id: str) -> bool:
        """Stop one timed service quota window without deleting its usage."""

        cursor = self._connection.execute(
            "UPDATE time_quotas SET started_at = 0, expires_at = 0 WHERE id = ?",
            (quota_id,),
        )
        if cursor.rowcount:
            self._connection.commit()
            return True
        return False

    def record_time_usage(
        self,
        quota_id: str,
        counter_bytes: int,
        sampled_at: int,
        *,
        active: bool,
    ) -> Optional[TimeQuota]:
        """Merge one traffic sample into a timed quota's active seconds."""

        quota = self.get_time_quota(quota_id)
        if quota is None:
            return None
        if (
            not isinstance(counter_bytes, int)
            or counter_bytes < 0
            or not isinstance(sampled_at, int)
            or sampled_at < 0
        ):
            raise StorageError("time quota traffic sample is invalid")
        counter_reset = counter_bytes < quota.counter_bytes
        traffic_seen = counter_bytes > quota.counter_bytes and not counter_reset
        elapsed = max(0, sampled_at - quota.last_sample_at)
        active_seconds = (
            min(60, elapsed)
            if active and traffic_seen and quota.last_sample_at > 0
            else 0
        )
        updated_used = min(2**63 - 1, quota.used_seconds + active_seconds)
        self._connection.execute(
            """
            UPDATE time_quotas
            SET used_seconds = ?, counter_bytes = ?, last_sample_at = ?
            WHERE id = ?
            """,
            (updated_used, counter_bytes, sampled_at, quota_id),
        )
        self._connection.commit()
        return TimeQuota(
            id=quota.id,
            device_id=quota.device_id,
            name=quota.name,
            domains=quota.domains,
            profile_id=quota.profile_id,
            limit_seconds=quota.limit_seconds,
            window_duration_seconds=quota.window_duration_seconds,
            reset_mode=quota.reset_mode,
            reset_interval_seconds=quota.reset_interval_seconds,
            reset_times=quota.reset_times,
            used_seconds=updated_used,
            counter_bytes=counter_bytes,
            last_sample_at=sampled_at,
            last_reset_at=quota.last_reset_at,
            started_at=quota.started_at,
            expires_at=quota.expires_at,
            enabled=quota.enabled,
        )

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
    def _device_group_from_row(row: sqlite3.Row) -> DeviceGroup:
        try:
            device_ids = tuple(json.loads(row["device_ids_json"]))
            return DeviceGroup(
                id=row["id"],
                name=row["name"],
                device_ids=device_ids,
                profile_id=row["profile_id"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
            raise StorageError("stored device group is malformed") from error

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

    @staticmethod
    def _progression_from_row(row: sqlite3.Row) -> Progression:
        try:
            return Progression(
                id=row["id"],
                device_id=row["device_id"],
                start_profile_id=row["start_profile_id"],
                end_profile_id=row["end_profile_id"],
                started_at=row["started_at"],
                duration_seconds=row["duration_seconds"],
                mode=row["mode"],
                traffic_target_queries=row["traffic_target_queries"],
                traffic_baseline_queries=row["traffic_baseline_queries"],
                traffic_target_bytes=row["traffic_target_bytes"],
                traffic_baseline_bytes=row["traffic_baseline_bytes"],
                website_domains=tuple(json.loads(row["website_domains_json"])),
                enabled=bool(row["enabled"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
            raise StorageError("stored progression is malformed") from error

    @staticmethod
    def _data_limit_from_row(row: sqlite3.Row) -> DataLimit:
        try:
            return DataLimit(
                device_id=row["device_id"],
                profile_id=row["profile_id"],
                limit_bytes=row["limit_bytes"],
                reset_mode=row["reset_mode"],
                reset_interval_seconds=row["reset_interval_seconds"],
                reset_times=tuple(json.loads(row["reset_times_json"])),
                used_bytes=row["used_bytes"],
                counter_bytes=row["counter_bytes"],
                last_reset_at=row["last_reset_at"],
                enabled=bool(row["enabled"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
            raise StorageError("stored data limit is malformed") from error

    @staticmethod
    def _group_data_limit_from_row(row: sqlite3.Row) -> GroupDataLimit:
        try:
            return GroupDataLimit(
                group_id=row["group_id"],
                profile_id=row["profile_id"],
                limit_bytes=row["limit_bytes"],
                reset_mode=row["reset_mode"],
                reset_interval_seconds=row["reset_interval_seconds"],
                reset_times=tuple(json.loads(row["reset_times_json"])),
                used_bytes=row["used_bytes"],
                counter_bytes=row["counter_bytes"],
                last_reset_at=row["last_reset_at"],
                enabled=bool(row["enabled"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
            raise StorageError("stored group data limit is malformed") from error

    @staticmethod
    def _service_quota_from_row(row: sqlite3.Row) -> ServiceQuota:
        try:
            return ServiceQuota(
                id=row["id"],
                device_id=row["device_id"],
                name=row["name"],
                domains=tuple(json.loads(row["domains_json"])),
                profile_id=row["profile_id"],
                limit_bytes=row["limit_bytes"],
                weight_percent=row["weight_percent"],
                reset_mode=row["reset_mode"],
                reset_interval_seconds=row["reset_interval_seconds"],
                reset_times=tuple(json.loads(row["reset_times_json"])),
                used_bytes=row["used_bytes"],
                counter_bytes=row["counter_bytes"],
                last_reset_at=row["last_reset_at"],
                enabled=bool(row["enabled"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
            raise StorageError("stored service quota is malformed") from error

    @staticmethod
    def _time_quota_from_row(row: sqlite3.Row) -> TimeQuota:
        try:
            return TimeQuota(
                id=row["id"],
                device_id=row["device_id"],
                name=row["name"],
                domains=tuple(json.loads(row["domains_json"])),
                profile_id=row["profile_id"],
                limit_seconds=row["limit_seconds"],
                window_duration_seconds=row["window_duration_seconds"],
                reset_mode=row["reset_mode"],
                reset_interval_seconds=row["reset_interval_seconds"],
                reset_times=tuple(json.loads(row["reset_times_json"])),
                used_seconds=row["used_seconds"],
                counter_bytes=row["counter_bytes"],
                last_sample_at=row["last_sample_at"],
                last_reset_at=row["last_reset_at"],
                started_at=row["started_at"],
                expires_at=row["expires_at"],
                enabled=bool(row["enabled"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
            raise StorageError("stored time quota is malformed") from error

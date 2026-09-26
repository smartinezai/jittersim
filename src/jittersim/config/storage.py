"""SQLite persistence for validated jittersim configuration."""

import json
import sqlite3
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple, Union

from .device_labels import (
    MAX_SAVED_CONNECTED_DEVICE_LABELS,
    normalize_device_display_name,
    normalize_device_mac,
    normalize_device_type,
)
from .models import (
    MAX_UNAFFECTED_DEVICES,
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
    SavedSettings,
    Scenario,
    ScenarioRun,
    ScenarioStep,
    Schedule,
    ServiceAssignment,
    ServiceQuota,
    TimeQuota,
    Timer,
    UnaffectedDevice,
    ValidationError,
    WebsitePolicy,
)


class StorageError(RuntimeError):
    """Raised when stored configuration cannot be read or written safely."""


class TrafficRateSettingsError(StorageError):
    """Raised when a graph-only ISP rate reference is invalid."""


DatabasePath = Union[str, Path]
_DOMAIN_RETENTION_SECONDS = 30 * 24 * 60 * 60
_MAX_DOMAIN_OBSERVATIONS = 20_000
_MAX_OBSERVATIONS_PER_DEVICE = 50
MIN_TRAFFIC_RATE_KBIT = 100
MAX_TRAFFIC_RATE_KBIT = 1_000_000
TRAFFIC_RATE_STEP_KBIT = 100
DEFAULT_ISP_DOWNLOAD_KBIT = 100_000
# A schedule targets exactly one device, group or everyone else. Older
# databases get this table through _upgrade_schedules_table.
_SCHEDULES_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS {name} (
        id TEXT PRIMARY KEY,
        device_id TEXT REFERENCES devices(id) ON DELETE CASCADE,
        group_id TEXT REFERENCES device_groups(id) ON DELETE CASCADE,
        everyone_else INTEGER NOT NULL DEFAULT 0,
        profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        days_json TEXT NOT NULL,
        start_minute INTEGER NOT NULL,
        end_minute INTEGER NOT NULL,
        enabled INTEGER NOT NULL,
        CHECK ((device_id IS NOT NULL) + (group_id IS NOT NULL) + (everyone_else = 1) = 1)
    )
"""


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
        self._deferring_commits = False
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
            CREATE TABLE IF NOT EXISTS connected_device_labels (
                mac_address TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                device_type TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS traffic_rate_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                upload_kbit INTEGER,
                download_kbit INTEGER,
                CHECK (
                    upload_kbit IS NULL OR (
                        typeof(upload_kbit) = 'integer'
                        AND upload_kbit BETWEEN 100 AND 1000000
                        AND upload_kbit % 100 = 0
                    )
                ),
                CHECK (
                    download_kbit IS NULL OR (
                        typeof(download_kbit) = 'integer'
                        AND download_kbit BETWEEN 100 AND 1000000
                        AND download_kbit % 100 = 0
                    )
                )
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
            CREATE TABLE IF NOT EXISTS scenarios (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                loop INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scenario_runs (
                id TEXT PRIMARY KEY,
                scenario_id TEXT NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
                device_id TEXT REFERENCES devices(id) ON DELETE CASCADE,
                group_id TEXT REFERENCES device_groups(id) ON DELETE CASCADE,
                everyone_else INTEGER NOT NULL DEFAULT 0,
                started_at INTEGER NOT NULL,
                enabled INTEGER NOT NULL,
                CHECK ((device_id IS NOT NULL) + (group_id IS NOT NULL) + (everyone_else = 1) = 1)
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
            CREATE TABLE IF NOT EXISTS service_assignments (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                domains_json TEXT NOT NULL,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
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
            CREATE TABLE IF NOT EXISTS everyone_else_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                -- The API refuses to delete this profile. Older versions don't
                -- know about everyone else, so a delete there clears the choice.
                profile_id TEXT REFERENCES profiles(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS unaffected_devices (
                mac_address TEXT PRIMARY KEY,
                name TEXT NOT NULL
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
            -- Running query totals per client. Pruning old observations must
            -- not lower them, because DNS-driven progressions measure from them.
            CREATE TABLE IF NOT EXISTS domain_query_totals (
                ipv4_address TEXT PRIMARY KEY,
                query_count INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO domain_query_totals (ipv4_address, query_count)
                SELECT ipv4_address, SUM(query_count)
                FROM domain_observations
                WHERE NOT EXISTS (SELECT 1 FROM domain_query_totals)
                GROUP BY ipv4_address;
            INSERT OR IGNORE INTO runtime_state (
                id, configuration_version, observed_state
            ) VALUES (1, 0, 'unknown');
            INSERT OR IGNORE INTO traffic_rate_settings (
                id, upload_kbit, download_kbit
            ) VALUES (1, NULL, 100000);
            INSERT OR IGNORE INTO network_policies (
                id, mac_mode, mac_allowlist_json, mac_denylist_json,
                website_mode, website_allowed_json, website_blocked_json
            ) VALUES (1, 'disabled', '[]', '[]', 'unrestricted', '[]', '[]');
            INSERT OR IGNORE INTO everyone_else_settings (id, profile_id) VALUES (1, NULL);
            """
        )
        self._connection.execute(_SCHEDULES_TABLE_SQL.format(name="schedules"))
        self._upgrade_schedules_table()
        self._add_column_if_missing("profiles", "settings_json", "TEXT NOT NULL DEFAULT '{}'")
        self._add_column_if_missing("devices", "mac_address", "TEXT")
        self._add_column_if_missing("progressions", "traffic_target_bytes", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("progressions", "traffic_baseline_bytes", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("service_quotas", "weight_percent", "INTEGER NOT NULL DEFAULT 100")
        self._commit()

    def _upgrade_schedules_table(self) -> None:
        """Let older schedules tables hold group and everyone-else schedules."""

        def upgraded() -> bool:
            columns = self._connection.execute("PRAGMA table_info(schedules)").fetchall()
            return any(column["name"] == "group_id" for column in columns)

        if upgraded():
            return
        # SQLite can't drop NOT NULL from device_id, so the rows move to a new
        # table. No other table refers to schedules.
        try:
            self._connection.commit()
            # The write lock comes first, and the check again under it, so two
            # processes that open an old database at once don't both copy it.
            self._connection.execute("BEGIN IMMEDIATE")
            if not upgraded():
                self._connection.execute(_SCHEDULES_TABLE_SQL.format(name="schedules_upgrade"))
                # A schedule whose device or profile is gone applies to nothing,
                # and copying it would fail the foreign keys.
                self._connection.execute(
                    """
                    INSERT INTO schedules_upgrade (
                        id, device_id, profile_id, days_json, start_minute, end_minute, enabled
                    )
                    SELECT id, device_id, profile_id, days_json, start_minute, end_minute, enabled
                    FROM schedules
                    WHERE device_id IN (SELECT id FROM devices)
                      AND profile_id IN (SELECT id FROM profiles)
                    """
                )
                self._connection.execute("DROP TABLE schedules")
                self._connection.execute("ALTER TABLE schedules_upgrade RENAME TO schedules")
            self._connection.commit()
        except sqlite3.Error as error:
            self._connection.rollback()
            raise StorageError("could not upgrade the saved schedules") from error

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
            self._commit()
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
            self._commit()
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
        self._commit()
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
            self._commit()
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

    def record_schedule_result(self, *, success: bool) -> bool:
        """Record a scheduled apply, unless a reset was recorded meanwhile.

        The check and the write are one statement, so a reset made from
        another process, such as `jittersim reset`, is never overwritten.
        """

        cursor = self._connection.execute(
            """
            UPDATE runtime_state
            SET observed_state = ?, last_action = 'schedule', last_result = ?, last_error = ?
            WHERE id = 1 AND (last_action IS NULL OR last_action != 'reset')
            """,
            (
                "applied" if success else "error",
                "success" if success else "failure",
                None if success else "scheduled network plan failed",
            ),
        )
        self._commit()
        return bool(cursor.rowcount)
    def get_traffic_rate_settings(self) -> dict:
        """Return the saved ISP rate references in integer kbit/s."""

        try:
            row = self._connection.execute(
                "SELECT upload_kbit, download_kbit "
                "FROM traffic_rate_settings WHERE id = 1"
            ).fetchone()
            if row is None:
                raise StorageError("traffic rate settings are missing")
            return {
                "upload_kbit": row["upload_kbit"],
                "download_kbit": row["download_kbit"],
            }
        except sqlite3.Error as error:
            raise StorageError("could not read traffic rate settings") from error

    def save_traffic_rate_settings(
        self,
        *,
        upload_kbit: Optional[int],
        download_kbit: Optional[int],
    ) -> dict:
        """Save graph-only ISP rate references without changing the network plan."""

        for value in (upload_kbit, download_kbit):
            if value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not MIN_TRAFFIC_RATE_KBIT <= value <= MAX_TRAFFIC_RATE_KBIT
                or value % TRAFFIC_RATE_STEP_KBIT
            ):
                raise TrafficRateSettingsError("traffic rate is invalid")
        try:
            cursor = self._connection.execute(
                "UPDATE traffic_rate_settings "
                "SET upload_kbit = ?, download_kbit = ? WHERE id = 1",
                (upload_kbit, download_kbit),
            )
            if cursor.rowcount != 1:
                raise StorageError("traffic rate settings are missing")
            self._connection.commit()
        except sqlite3.Error as error:
            self._connection.rollback()
            raise StorageError("could not save traffic rate settings") from error
        return {
            "upload_kbit": upload_kbit,
            "download_kbit": download_kbit,
        }

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

    def get_device_by_mac_address(self, mac_address: str) -> Optional[Device]:
        """Return the device assignment saved with one MAC address or `None`."""

        row = self._connection.execute(
            "SELECT id, name, ipv4_address, profile_id, mac_address FROM devices "
            "WHERE mac_address = ? ORDER BY id",
            (mac_address,),
        ).fetchone()
        return None if row is None else self._device_from_row(row)

    def get_everyone_else_profile_id(self) -> Optional[str]:
        """Return the profile for test Wi-Fi clients that aren't saved or unaffected."""

        row = self._connection.execute(
            "SELECT profile_id FROM everyone_else_settings WHERE id = 1"
        ).fetchone()
        return None if row is None else row["profile_id"]

    def save_everyone_else_profile_id(self, profile_id: Optional[str]) -> None:
        """Choose the everyone-else profile, or None for a normal connection."""

        try:
            self._connection.execute(
                "UPDATE everyone_else_settings SET profile_id = ? WHERE id = 1", (profile_id,)
            )
            self._bump_configuration_version()
            self._commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"profile {profile_id} does not exist") from error

    def list_unaffected_devices(self) -> tuple:
        """Return the devices that keep a normal connection, in address order."""

        rows = self._connection.execute(
            "SELECT mac_address, name FROM unaffected_devices ORDER BY mac_address"
        ).fetchall()
        try:
            return tuple(UnaffectedDevice(row["mac_address"], row["name"]) for row in rows)
        except ValidationError as error:
            raise StorageError("stored unaffected device is malformed") from error

    def save_unaffected_device(self, device: UnaffectedDevice) -> None:
        """Add or rename one unaffected device."""

        exists = self._connection.execute(
            "SELECT 1 FROM unaffected_devices WHERE mac_address = ?", (device.mac_address,)
        ).fetchone()
        count = self._connection.execute("SELECT COUNT(*) FROM unaffected_devices").fetchone()[0]
        if exists is None and count >= MAX_UNAFFECTED_DEVICES:
            raise StorageError(
                f"the unaffected list holds at most {MAX_UNAFFECTED_DEVICES} devices"
            )
        self._connection.execute(
            "INSERT INTO unaffected_devices (mac_address, name) VALUES (?, ?) "
            "ON CONFLICT(mac_address) DO UPDATE SET name = excluded.name",
            (device.mac_address, device.name),
        )
        self._bump_configuration_version()
        self._commit()

    def delete_unaffected_device(self, mac_address: str) -> bool:
        """Remove one device from the unaffected list."""

        return self._delete_row("unaffected_devices", "mac_address", mac_address)

    def list_connected_device_labels(self) -> Dict[str, Dict[str, str]]:
        """Read locally saved names and types for Wi-Fi client addresses."""

        try:
            rows = self._connection.execute(
                "SELECT mac_address, display_name, device_type "
                "FROM connected_device_labels ORDER BY mac_address"
            ).fetchall()
            return {
                row["mac_address"]: {
                    "display_name": row["display_name"],
                    "device_type": row["device_type"],
                }
                for row in rows
            }
        except sqlite3.Error as error:
            raise StorageError("could not read connected-device labels") from error

    def save_connected_device_label(
        self, *, mac_address: str, display_name: str, device_type: str
    ) -> None:
        """Save a validated label without changing traffic configuration."""

        try:
            mac = normalize_device_mac(mac_address)
            name = normalize_device_display_name(display_name)
            kind = normalize_device_type(device_type)
        except ValueError as error:
            raise StorageError("connected-device label is invalid") from error
        try:
            exists = self._connection.execute(
                "SELECT 1 FROM connected_device_labels WHERE mac_address = ?", (mac,)
            ).fetchone()
            count = self._connection.execute(
                "SELECT COUNT(*) FROM connected_device_labels"
            ).fetchone()[0]
        except sqlite3.Error as error:
            raise StorageError("could not check connected-device label limit") from error
        if exists is None and count >= MAX_SAVED_CONNECTED_DEVICE_LABELS:
            raise StorageError("saved device label limit reached; remove an older label")
        try:
            self._connection.execute(
                "INSERT INTO connected_device_labels "
                "(mac_address, display_name, device_type) VALUES (?, ?, ?) "
                "ON CONFLICT(mac_address) DO UPDATE SET "
                "display_name = excluded.display_name, "
                "device_type = excluded.device_type",
                (mac, name, kind),
            )
            self._connection.commit()
        except sqlite3.Error as error:
            self._connection.rollback()
            raise StorageError("could not save connected-device label") from error

    def delete_connected_device_label(self, *, mac_address: str) -> None:
        """Remove a locally saved label for one Wi-Fi address."""

        try:
            mac = normalize_device_mac(mac_address)
        except ValueError as error:
            raise StorageError("connected-device address is invalid") from error
        try:
            self._connection.execute(
                "DELETE FROM connected_device_labels WHERE mac_address = ?", (mac,)
            )
            self._connection.commit()
        except sqlite3.Error as error:
            self._connection.rollback()
            raise StorageError("could not remove connected-device label") from error

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

    def profile_references(self, profile_id: str, *, now: int) -> tuple:
        """Describe every device, rule and limit that still uses one profile."""

        device_names = {device.id: device.name for device in self.list_devices()}
        groups = self.list_device_groups()
        group_names = {group.id: group.name for group in groups}
        references = [
            f"device {device.name}"
            for device in self.list_devices()
            if device.profile_id == profile_id
        ]
        references.extend(
            f"group {group.name}" for group in groups if group.profile_id == profile_id
        )
        references.extend(
            f"schedule {schedule.id}"
            for schedule in self.list_schedules()
            if schedule.profile_id == profile_id
        )
        references.extend(
            f"timer for {device_names.get(timer.device_id, timer.device_id)}"
            for timer in self.list_active_timers(now=now)
            if timer.profile_id == profile_id
        )
        references.extend(
            f"progression for {device_names.get(progression.device_id, progression.device_id)}"
            for progression in self.list_active_progressions(now=now)
            if profile_id in (progression.start_profile_id, progression.end_profile_id)
        )
        references.extend(
            f"data limit for {device_names.get(limit.device_id, limit.device_id)}"
            for limit in self.list_data_limits()
            if limit.profile_id == profile_id
        )
        references.extend(
            f"shared limit for {group_names.get(limit.group_id, limit.group_id)}"
            for limit in self.list_group_data_limits()
            if limit.profile_id == profile_id
        )
        references.extend(
            f"browser service profile {assignment.name}"
            for assignment in self.list_service_assignments()
            if assignment.profile_id == profile_id
        )
        references.extend(
            f"service limit {quota.name}"
            for quota in self.list_service_quotas()
            if quota.profile_id == profile_id
        )
        references.extend(
            f"time limit {quota.name}"
            for quota in self.list_time_quotas()
            if quota.profile_id == profile_id
        )
        references.extend(
            f"scenario {scenario.name}"
            for scenario in self.list_scenarios()
            if any(step.profile_id == profile_id for step in scenario.steps)
        )
        if self.get_everyone_else_profile_id() == profile_id:
            references.append("everyone else")
        return tuple(references)

    def delete_profile(self, profile_id: str) -> bool:
        """Delete one profile that no device uses. Check profile_references first."""

        try:
            return self._delete_row("profiles", "id", profile_id)
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"profile {profile_id} is still in use") from error

    def delete_device(self, device_id: str) -> bool:
        """Delete one device with its schedules, timers, progressions and limits.

        A group left without devices is deleted too, with its shared limit,
        because a group must contain at least one device.
        """

        cursor = self._connection.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        if not cursor.rowcount:
            return False
        self._connection.execute(
            "DELETE FROM device_groups WHERE id NOT IN (SELECT group_id FROM device_group_members)"
        )
        self._bump_configuration_version()
        self._commit()
        return True

    def _commit(self) -> None:
        # replace_settings defers the commits of the save methods it calls so
        # the whole replacement is one transaction.
        if not self._deferring_commits:
            self._connection.commit()

    def saved_settings(self) -> SavedSettings:
        """Return every setting that an export includes."""

        return SavedSettings(
            profiles=self.list_profiles(),
            devices=self.list_devices(),
            groups=self.list_device_groups(),
            schedules=self.list_schedules(),
            mac_policy=self.get_mac_policy(),
            website_policy=self.get_website_policy(),
            data_limits=self.list_data_limits(),
            group_data_limits=self.list_group_data_limits(),
            service_quotas=self.list_service_quotas(),
            time_quotas=self.list_time_quotas(),
            service_assignments=self.list_service_assignments(),
            everyone_else_profile_id=self.get_everyone_else_profile_id(),
            unaffected_devices=self.list_unaffected_devices(),
            scenarios=self.list_scenarios(),
        )

    def replace_settings(self, settings: SavedSettings) -> None:
        """Replace every saved setting at once. On failure nothing changes.

        DNS observations, action history and the runtime state stay. Timers,
        progressions and scenario runs are removed because they belong to the
        replaced devices, groups and scenarios.
        """

        self._deferring_commits = True
        try:
            # The everyone-else profile refers to a profile that is replaced below.
            self._connection.execute("UPDATE everyone_else_settings SET profile_id = NULL")
            for table in (
                "scenario_runs",
                "scenarios",
                "unaffected_devices",
                "service_assignments",
                "time_quotas",
                "service_quotas",
                "group_data_limits",
                "data_limits",
                "schedules",
                "timers",
                "progressions",
                "device_groups",
                "devices",
                "profiles",
            ):
                self._connection.execute(f"DELETE FROM {table}")
            for profile in settings.profiles:
                self.save_profile(profile)
            for device in settings.devices:
                self.save_device(device)
            for group in settings.groups:
                self.save_device_group(group)
            for schedule in settings.schedules:
                self.save_schedule(schedule)
            for scenario in settings.scenarios:
                self.save_scenario(scenario)
            self.save_mac_policy(settings.mac_policy)
            self.save_website_policy(settings.website_policy)
            for data_limit in settings.data_limits:
                self.save_data_limit(data_limit)
            for group_data_limit in settings.group_data_limits:
                self.save_group_data_limit(group_data_limit)
            for service_quota in settings.service_quotas:
                self.save_service_quota(service_quota)
            for time_quota in settings.time_quotas:
                self.save_time_quota(time_quota)
            for assignment in settings.service_assignments:
                self.save_service_assignment(assignment)
            for unaffected in settings.unaffected_devices:
                self.save_unaffected_device(unaffected)
            self.save_everyone_else_profile_id(settings.everyone_else_profile_id)
        except BaseException as error:
            # Any error, not only a storage one, must not leave a half-replaced
            # configuration for the next save to commit.
            self._connection.rollback()
            if isinstance(error, sqlite3.Error):
                raise StorageError("could not replace the saved settings") from error
            raise
        finally:
            self._deferring_commits = False
        self._connection.commit()

    def _delete_row(self, table: str, key_column: str, key: str) -> bool:
        cursor = self._connection.execute(f"DELETE FROM {table} WHERE {key_column} = ?", (key,))
        if not cursor.rowcount:
            return False
        self._bump_configuration_version()
        self._commit()
        return True

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
            self._commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save device group {group.id}") from error

    def delete_device_group(self, group_id: str) -> bool:
        """Delete one group and release its device members."""

        return self._delete_row("device_groups", "id", group_id)

    def save_schedule(self, schedule: Schedule) -> None:
        """Insert or update one repeating schedule."""

        try:
            self._connection.execute(
                """
                INSERT INTO schedules (
                    id, device_id, group_id, everyone_else, profile_id, days_json,
                    start_minute, end_minute, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    device_id = excluded.device_id,
                    group_id = excluded.group_id,
                    everyone_else = excluded.everyone_else,
                    profile_id = excluded.profile_id,
                    days_json = excluded.days_json,
                    start_minute = excluded.start_minute,
                    end_minute = excluded.end_minute,
                    enabled = excluded.enabled
                """,
                (
                    schedule.id,
                    schedule.device_id,
                    schedule.group_id,
                    int(schedule.everyone_else),
                    schedule.profile_id,
                    json.dumps(list(schedule.days_of_week)),
                    schedule.start_minute,
                    schedule.end_minute,
                    int(schedule.enabled),
                ),
            )
            self._bump_configuration_version()
            self._commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save schedule {schedule.id}") from error

    def delete_schedule(self, schedule_id: str) -> bool:
        """Delete one schedule when it exists."""

        return self._delete_row("schedules", "id", schedule_id)

    def list_schedules(self) -> tuple:
        """Return all schedules in stable identifier order."""

        rows = self._connection.execute(
            """
            SELECT id, device_id, group_id, everyone_else, profile_id, days_json,
                   start_minute, end_minute, enabled
            FROM schedules ORDER BY id
            """
        ).fetchall()
        return tuple(self._schedule_from_row(row) for row in rows)

    def save_timer(self, timer: Timer) -> None:
        """Persist one timer and replace other active overrides for its device."""

        try:
            self._connection.execute(
                "UPDATE timers SET enabled = 0 WHERE device_id = ? AND enabled = 1",
                (timer.device_id,),
            )
            self._connection.execute(
                "UPDATE scenario_runs SET enabled = 0 WHERE device_id = ? AND enabled = 1",
                (timer.device_id,),
            )
            self._connection.execute(
                "UPDATE progressions SET enabled = 0 WHERE device_id = ? AND enabled = 1",
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
            self._commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save timer {timer.id}") from error

    def save_scenario(self, scenario: Scenario, *, now: Optional[float] = None) -> None:
        """Insert or update one scenario. Its running copies follow the new steps.

        Runs that already finished stay finished, even when the steps get
        longer or start repeating.
        """

        steps = [
            {"profile_id": step.profile_id, "duration_seconds": step.duration_seconds}
            for step in scenario.steps
        ]
        previous = self.get_scenario(scenario.id)
        try:
            if previous is not None and not previous.loop:
                self._connection.execute(
                    """
                    UPDATE scenario_runs SET enabled = 0
                    WHERE scenario_id = ? AND enabled = 1 AND started_at + ? <= ?
                    """,
                    (scenario.id, previous.total_seconds, time.time() if now is None else now),
                )
            self._connection.execute(
                """
                INSERT INTO scenarios (id, name, steps_json, loop) VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    steps_json = excluded.steps_json,
                    loop = excluded.loop
                """,
                (scenario.id, scenario.name, json.dumps(steps), int(scenario.loop)),
            )
            self._bump_configuration_version()
            self._commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save scenario {scenario.id}") from error

    def get_scenario(self, scenario_id: str) -> Optional[Scenario]:
        """Return one scenario or `None` when it is not stored."""

        row = self._connection.execute(
            "SELECT id, name, steps_json, loop FROM scenarios WHERE id = ?", (scenario_id,)
        ).fetchone()
        return None if row is None else self._scenario_from_row(row)

    def list_scenarios(self) -> tuple:
        """Return scenarios in stable identifier order."""

        rows = self._connection.execute(
            "SELECT id, name, steps_json, loop FROM scenarios ORDER BY id"
        ).fetchall()
        return tuple(self._scenario_from_row(row) for row in rows)

    def delete_scenario(self, scenario_id: str) -> bool:
        """Delete one scenario, which also ends its runs."""

        return self._delete_row("scenarios", "id", scenario_id)

    def start_scenario_run(self, run: ScenarioRun) -> None:
        """Start one run, replacing the other run for its target.

        For a device it also replaces the device's timer and progression, as a
        timer does.
        """

        try:
            if run.device_id is not None:
                target = ("device_id = ?", (run.device_id,))
                for table in ("timers", "progressions"):
                    self._connection.execute(
                        f"UPDATE {table} SET enabled = 0 WHERE device_id = ? AND enabled = 1",
                        (run.device_id,),
                    )
            elif run.group_id is not None:
                target = ("group_id = ?", (run.group_id,))
            else:
                target = ("everyone_else = 1", ())
            self._connection.execute(
                f"UPDATE scenario_runs SET enabled = 0 WHERE {target[0]} AND enabled = 1",
                target[1],
            )
            self._connection.execute(
                """
                INSERT INTO scenario_runs (
                    id, scenario_id, device_id, group_id, everyone_else, started_at, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.scenario_id,
                    run.device_id,
                    run.group_id,
                    int(run.everyone_else),
                    run.started_at,
                    int(run.enabled),
                ),
            )
            self._bump_configuration_version()
            self._commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not start scenario run {run.id}") from error

    def stop_scenario_run(self, run_id: str) -> bool:
        """Stop one running scenario when it is still enabled."""

        cursor = self._connection.execute(
            "UPDATE scenario_runs SET enabled = 0 WHERE id = ? AND enabled = 1", (run_id,)
        )
        if cursor.rowcount:
            self._bump_configuration_version()
            self._commit()
            return True
        return False

    def list_scenario_runs(self) -> tuple:
        """Return (run, scenario) for enabled runs, newest first, finished or not."""

        rows = self._connection.execute(
            """
            SELECT scenario_runs.id AS run_id, scenario_id, device_id, group_id,
                   everyone_else, started_at, enabled,
                   scenarios.id, scenarios.name, scenarios.steps_json, scenarios.loop
            FROM scenario_runs JOIN scenarios ON scenarios.id = scenario_runs.scenario_id
            WHERE enabled = 1
            ORDER BY started_at DESC, run_id DESC
            """
        ).fetchall()
        runs = []
        for row in rows:
            try:
                run = ScenarioRun(
                    id=row["run_id"],
                    scenario_id=row["scenario_id"],
                    started_at=row["started_at"],
                    device_id=row["device_id"],
                    group_id=row["group_id"],
                    everyone_else=bool(row["everyone_else"]),
                )
            except ValidationError as error:
                raise StorageError("stored scenario run is malformed") from error
            runs.append((run, self._scenario_from_row(row)))
        return tuple(runs)

    def delete_timer(self, timer_id: str) -> bool:
        """Disable one temporary profile override when it exists."""

        cursor = self._connection.execute(
            "UPDATE timers SET enabled = 0 WHERE id = ? AND enabled = 1",
            (timer_id,),
        )
        if cursor.rowcount:
            self._bump_configuration_version()
            self._commit()
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
                "UPDATE scenario_runs SET enabled = 0 WHERE device_id = ? AND enabled = 1",
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
            self._commit()
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
            self._commit()
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

    def move_device_addresses(self, moves: Dict[str, Tuple[str, str]]) -> bool:
        """Move saved devices from one address to another, all at once.

        Returns False and changes nothing when any device no longer has the
        address the caller read, for example because it was edited meanwhile.
        DNS queries are counted by address, so a traffic progression's baseline
        moves with its device and keeps the progress made.
        """

        try:
            # Park each device on a unique placeholder first: addresses are
            # unique, and devices that swap or pass addresses on would collide.
            for device_id, (old_address, _) in moves.items():
                cursor = self._connection.execute(
                    "UPDATE devices SET ipv4_address = ? WHERE id = ? AND ipv4_address = ?",
                    (f"moving:{device_id}", device_id, old_address),
                )
                if cursor.rowcount != 1:
                    self._connection.rollback()
                    return False
            for device_id, (old_address, new_address) in moves.items():
                self._connection.execute(
                    "UPDATE devices SET ipv4_address = ? WHERE id = ?",
                    (new_address, device_id),
                )
                self._connection.execute(
                    """
                    UPDATE progressions
                    SET traffic_baseline_queries = traffic_baseline_queries + ?
                    WHERE device_id = ? AND mode = 'traffic' AND enabled = 1
                    """,
                    (
                        self.total_domain_queries(new_address)
                        - self.total_domain_queries(old_address),
                        device_id,
                    ),
                )
            self._bump_configuration_version()
            self._commit()
            return True
        except sqlite3.Error as error:
            self._connection.rollback()
            raise StorageError("could not move saved devices to their new addresses") from error

    def total_domain_queries(self, ipv4_address: str) -> int:
        """Return the running DNS query total for one observed client."""

        row = self._connection.execute(
            "SELECT query_count FROM domain_query_totals WHERE ipv4_address = ?",
            (ipv4_address,),
        ).fetchone()
        return int(row["query_count"]) if row is not None else 0

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
            self._commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save data limit for {data_limit.device_id}") from error

    def restart_usage_counters(self) -> None:
        """Record that every helper traffic counter restarted at zero."""

        for table in ("data_limits", "group_data_limits", "service_quotas", "time_quotas"):
            self._connection.execute(f"UPDATE {table} SET counter_bytes = 0")
        self._commit()

    def delete_data_limit(self, device_id: str) -> bool:
        """Delete one device quota when it exists."""

        return self._delete_row("data_limits", "device_id", device_id)

    def delete_group_data_limit(self, group_id: str) -> bool:
        """Delete one shared group quota when it exists."""

        return self._delete_row("group_data_limits", "group_id", group_id)

    def delete_service_quota(self, quota_id: str) -> bool:
        """Delete one service quota when it exists."""

        return self._delete_row("service_quotas", "id", quota_id)

    def delete_time_quota(self, quota_id: str) -> bool:
        """Delete one timed service quota when it exists."""

        return self._delete_row("time_quotas", "id", quota_id)

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
            self._commit()
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
        self._commit()
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
            self._commit()
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
            self._commit()
            return True
        return False

    def rebaseline_group_data_usage(self, group_id: str, counter_bytes: int) -> None:
        """Start counting a group's traffic from a new aggregate without charging it."""

        if not isinstance(counter_bytes, int) or counter_bytes < 0:
            raise StorageError("group traffic counter is invalid")
        self._connection.execute(
            "UPDATE group_data_limits SET counter_bytes = ? WHERE group_id = ?",
            (counter_bytes, group_id),
        )
        self._commit()

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
        self._commit()
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
            self._commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save service quota {quota.id}") from error

    def list_service_assignments(self) -> tuple:
        """Return immediate service assignments in stable identifier order."""

        rows = self._connection.execute(
            """
            SELECT id, device_id, name, domains_json, profile_id, enabled
            FROM service_assignments ORDER BY id
            """
        ).fetchall()
        return tuple(self._service_assignment_from_row(row) for row in rows)

    def save_service_assignment(self, assignment: ServiceAssignment) -> None:
        """Insert or update an immediate service assignment."""

        try:
            self._connection.execute(
                """
                INSERT INTO service_assignments (
                    id, device_id, name, domains_json, profile_id, enabled
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    device_id = excluded.device_id,
                    name = excluded.name,
                    domains_json = excluded.domains_json,
                    profile_id = excluded.profile_id,
                    enabled = excluded.enabled
                """,
                (
                    assignment.id,
                    assignment.device_id,
                    assignment.name,
                    json.dumps(list(assignment.domains)),
                    assignment.profile_id,
                    int(assignment.enabled),
                ),
            )
            self._bump_configuration_version()
            self._commit()
        except sqlite3.IntegrityError as error:
            self._connection.rollback()
            raise StorageError(f"could not save service assignment {assignment.id}") from error

    def delete_service_assignment(self, assignment_id: str) -> bool:
        """Remove one assignment and its matching rules on the next apply."""

        cursor = self._connection.execute(
            "DELETE FROM service_assignments WHERE id = ?", (assignment_id,)
        )
        if not cursor.rowcount:
            return False
        self._bump_configuration_version()
        self._connection.commit()
        return True

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
            self._commit()
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
        self._commit()
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
            self._commit()
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
            self._commit()
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
        self._commit()
        return self.get_time_quota(quota_id)

    def stop_time_quota(self, quota_id: str) -> bool:
        """Stop one timed service quota window without deleting its usage."""

        cursor = self._connection.execute(
            "UPDATE time_quotas SET started_at = 0, expires_at = 0 WHERE id = ?",
            (quota_id,),
        )
        if cursor.rowcount:
            self._commit()
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
        self._commit()
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
        self._commit()

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
        self._commit()

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
                self._connection.execute(
                    """
                    INSERT INTO domain_query_totals (ipv4_address, query_count)
                    VALUES (?, ?)
                    ON CONFLICT(ipv4_address) DO UPDATE SET
                        query_count = MIN(
                            domain_query_totals.query_count + excluded.query_count,
                            ?
                        )
                    """,
                    (str(observation.ipv4_address), observation.query_count, 2**62),
                )
            self._prune_domain_observations(now=current_time)
            self._commit()
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
    def _scenario_from_row(row: sqlite3.Row) -> Scenario:
        try:
            steps = json.loads(row["steps_json"])
            if not isinstance(steps, list):
                raise ValueError("scenario steps must be a list")
            return Scenario(
                id=row["id"],
                name=row["name"],
                steps=tuple(
                    ScenarioStep(
                        profile_id=step["profile_id"], duration_seconds=step["duration_seconds"]
                    )
                    for step in steps
                ),
                loop=bool(row["loop"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
            raise StorageError("stored scenario is malformed") from error

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
                group_id=row["group_id"],
                everyone_else=bool(row["everyone_else"]),
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
    def _service_assignment_from_row(row: sqlite3.Row) -> ServiceAssignment:
        try:
            return ServiceAssignment(
                id=row["id"],
                device_id=row["device_id"],
                name=row["name"],
                domains=tuple(json.loads(row["domains_json"])),
                profile_id=row["profile_id"],
                enabled=bool(row["enabled"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
            raise StorageError("stored service assignment is malformed") from error

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

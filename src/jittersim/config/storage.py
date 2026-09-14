"""SQLite persistence for validated jittersim configuration."""

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
from typing import Optional, Union

from .models import Device, Impairment, Profile, RuntimeState, ValidationError


class StorageError(RuntimeError):
    """Raised when stored configuration cannot be read or written safely."""


DatabasePath = Union[str, Path]


def _serialize_impairment(impairment: Impairment) -> str:
    return json.dumps(asdict(impairment), sort_keys=True)


def _deserialize_impairment(value: str) -> Impairment:
    try:
        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError("impairment JSON must contain an object")
        return Impairment(**data)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise StorageError("stored impairment is malformed") from error


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
                random_seed INTEGER
            );
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                ipv4_address TEXT NOT NULL UNIQUE,
                profile_id TEXT NOT NULL REFERENCES profiles(id)
            );
            CREATE TABLE IF NOT EXISTS runtime_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                configuration_version INTEGER NOT NULL,
                observed_state TEXT NOT NULL,
                last_action TEXT,
                last_result TEXT,
                last_error TEXT
            );
            INSERT OR IGNORE INTO runtime_state (
                id, configuration_version, observed_state
            ) VALUES (1, 0, 'unknown');
            """
        )
        self._connection.commit()

    def save_profile(self, profile: Profile) -> None:
        """Insert or update one validated profile."""

        try:
            self._connection.execute(
                """
                INSERT INTO profiles (id, name, upload_json, download_json, random_seed)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    upload_json = excluded.upload_json,
                    download_json = excluded.download_json,
                    random_seed = excluded.random_seed
                """,
                (
                    profile.id,
                    profile.name,
                    _serialize_impairment(profile.upload),
                    _serialize_impairment(profile.download),
                    profile.random_seed,
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
                INSERT INTO devices (id, name, ipv4_address, profile_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    ipv4_address = excluded.ipv4_address,
                    profile_id = excluded.profile_id
                """,
                (device.id, device.name, str(device.ipv4_address), device.profile_id),
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
            "SELECT id, name, upload_json, download_json, random_seed FROM profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            return None
        return self._profile_from_row(row)

    def get_device(self, device_id: str) -> Optional[Device]:
        """Return one device assignment or `None` when it is not stored."""

        row = self._connection.execute(
            "SELECT id, name, ipv4_address, profile_id FROM devices WHERE id = ?",
            (device_id,),
        ).fetchone()
        if row is None:
            return None
        return self._device_from_row(row)

    def list_profiles(self) -> tuple:
        """Return all stored profiles in stable identifier order."""

        rows = self._connection.execute(
            "SELECT id, name, upload_json, download_json, random_seed "
            "FROM profiles ORDER BY id"
        ).fetchall()
        return tuple(self._profile_from_row(row) for row in rows)

    def list_devices(self) -> tuple:
        """Return all stored device assignments in stable identifier order."""

        rows = self._connection.execute(
            "SELECT id, name, ipv4_address, profile_id FROM devices ORDER BY id"
        ).fetchall()
        return tuple(self._device_from_row(row) for row in rows)

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> Profile:
        try:
            return Profile(
                id=row["id"],
                name=row["name"],
                upload=_deserialize_impairment(row["upload_json"]),
                download=_deserialize_impairment(row["download_json"]),
                random_seed=row["random_seed"],
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
            )
        except (KeyError, TypeError, ValidationError) as error:
            raise StorageError("stored device is malformed") from error

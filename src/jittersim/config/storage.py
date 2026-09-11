"""SQLite persistence for validated jittersim configuration."""

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
from typing import Optional, Union

from .models import Device, Impairment, Profile, ValidationError


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
            """
        )
        self._connection.commit()

    def save_profile(self, profile: Profile) -> None:
        """Insert or update one validated profile."""

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
        self._connection.commit()

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
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise StorageError(f"could not save device {device.id}") from error

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

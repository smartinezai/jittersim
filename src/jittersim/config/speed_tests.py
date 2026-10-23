"""Saved device speed test results (see docs/implementation-plan-device-speed-test-2026-10-09.md).

The rows live in the configuration database next to the activity log. They hold what the Pi
measured, the profile that was in force and the numbers a person typed in from the device's
own speed test page. They never hold a token, a password or the dashboard's address.
"""

import json
import logging
import sqlite3
import unicodedata
from typing import Any, Dict, List, Optional

from .storage import StorageError

LOGGER = logging.getLogger(__name__)

MAX_RESULTS_PER_DEVICE = 50
MAX_RESULTS_TOTAL = 500
MAX_LABEL_LENGTH = 80
MAX_JSON_BYTES = 16_384
# "fault" is a run that stopped after an error or could not read the counters most of the time.
RESULT_STATUSES = frozenset(("complete", "finished-early", "changed", "fault"))
REPORTED_FIELDS = ("download_mbit", "upload_mbit", "latency_ms", "jitter_ms")
REPORTED_MAXIMUM = 100_000.0

SPEED_TABLE = "speed_test_results"
# Connection checks (docs/implementation-plan-connection-check-2026-10-17.md) keep their rows in a
# table of their own with the same columns, so the speed test list never shows them and an older
# version of the program that does not know the table simply leaves it alone.
CHECK_TABLE = "connection_check_results"
TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at INTEGER NOT NULL,
    finished_at INTEGER NOT NULL,
    mac_address TEXT NOT NULL,
    device_id TEXT,
    device_name TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    provider TEXT NOT NULL,
    source TEXT NOT NULL,
    protected INTEGER NOT NULL DEFAULT 0,
    profile_json TEXT NOT NULL DEFAULT '{{}}',
    summary_json TEXT NOT NULL DEFAULT '{{}}',
    reported_json TEXT NOT NULL DEFAULT '{{}}'
);
CREATE INDEX IF NOT EXISTS {table}_device_idx
    ON {table}(mac_address, id DESC);
"""


def ensure_speed_test_schema(connection: sqlite3.Connection) -> None:
    """Create the tables and their indexes when missing. Safe to run on every start."""

    for table in (SPEED_TABLE, CHECK_TABLE):
        connection.executescript(TABLE_SCHEMA.format(table=table))


JOINERS = frozenset("\u200c\u200d")


def clean_label(value: Any) -> str:
    """A short label without control or format characters, or ValueError.

    Unicode categories Cc (including the C1 controls) and Cf (bidirectional overrides, zero
    width characters and the byte order mark) can hide or reorder text in a table or a CSV file.
    The zero width joiner and non-joiner are the exception: emoji sequences and Persian or
    Indic words need them, and they cannot reorder text.
    """

    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Label must be text")
    cleaned = " ".join(value.split())
    if any(unicodedata.category(character) in ("Cc", "Cf") and character not in JOINERS
           for character in cleaned):
        raise ValueError("Label contains a control character")
    if len(cleaned) > MAX_LABEL_LENGTH:
        raise ValueError(f"Label must be at most {MAX_LABEL_LENGTH} characters")
    return cleaned


def clean_reported(value: Any) -> Dict[str, float]:
    """Numbers typed in from the device's speed test page, or ValueError."""

    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - set(REPORTED_FIELDS):
        raise ValueError("Device numbers may only hold " + ", ".join(REPORTED_FIELDS))
    cleaned = {}
    for field in REPORTED_FIELDS:
        number = value.get(field)
        if number is None or number == "":
            continue
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError(f"{field} must be a number")
        if not 0 <= number <= REPORTED_MAXIMUM or number != number:
            raise ValueError(f"{field} must be between 0 and {REPORTED_MAXIMUM:g}")
        cleaned[field] = round(float(number), 2)
    return cleaned


def _encoded(value: Dict[str, Any], noun: str) -> str:
    body = json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)
    if len(body.encode("utf-8")) > MAX_JSON_BYTES:
        raise StorageError(f"{noun} is too large")
    return body


class SpeedTestStore:
    """Read and write speed test rows on the repository's connection and lock."""

    table = SPEED_TABLE
    noun = "speed test result"

    def __init__(self, repository):
        self.repository = repository
        self.db = repository._connection

    def save(self, row: Dict[str, Any]) -> Dict[str, Any]:
        if row["status"] not in RESULT_STATUSES:
            raise StorageError(f"{self.noun} status is invalid")
        values = (
            int(row["started_at"]),
            int(row["finished_at"]),
            row["mac_address"],
            row.get("device_id"),
            row["device_name"][:64],
            clean_label(row.get("label")),
            row["status"],
            row["provider"],
            row["source"],
            1 if row.get("protected") else 0,
            _encoded(row.get("profile") or {}, self.noun),
            _encoded(row.get("summary") or {}, self.noun),
            _encoded(clean_reported(row.get("reported")), self.noun),
        )
        with self.repository._lock:
            try:
                cursor = self.db.execute(
                    f"INSERT INTO {self.table} (started_at, finished_at, mac_address, "
                    "device_id, device_name, label, status, provider, source, protected, "
                    "profile_json, summary_json, reported_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
                identifier = cursor.lastrowid
                self.db.execute(
                    f"DELETE FROM {self.table} WHERE mac_address = ? AND id NOT IN "
                    f"(SELECT id FROM {self.table} WHERE mac_address = ? "
                    "ORDER BY id DESC LIMIT ?)",
                    (row["mac_address"], row["mac_address"], MAX_RESULTS_PER_DEVICE),
                )
                self.db.execute(
                    f"DELETE FROM {self.table} WHERE id NOT IN "
                    f"(SELECT id FROM {self.table} ORDER BY id DESC LIMIT ?)",
                    (MAX_RESULTS_TOTAL,),
                )
                self.repository._commit()
            except sqlite3.Error as error:
                self.repository._rollback()
                raise StorageError(f"could not save the {self.noun}") from error
        return self.get(identifier)

    def get(self, identifier: int) -> Optional[Dict[str, Any]]:
        try:
            with self.repository._lock:
                row = self.db.execute(
                    f"SELECT * FROM {self.table} WHERE id = ?", (identifier,)
                ).fetchone()
        except sqlite3.Error as error:
            raise StorageError(f"could not read the {self.noun}") from error
        return None if row is None else _result(row, self.noun)

    def list(self, mac_address: Optional[str] = None, *, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            with self.repository._lock:
                if mac_address is None:
                    rows = self.db.execute(
                        f"SELECT * FROM {self.table} ORDER BY id DESC LIMIT ?", (limit,)
                    ).fetchall()
                else:
                    rows = self.db.execute(
                        f"SELECT * FROM {self.table} WHERE mac_address = ? "
                        "ORDER BY id DESC LIMIT ?",
                        (mac_address, limit),
                    ).fetchall()
        except sqlite3.Error as error:
            raise StorageError(f"could not read the {self.noun}s") from error
        results = []
        for row in rows:
            try:
                results.append(_result(row, self.noun))
            except StorageError:
                # One damaged row must not hide the others. The keep-newest limits remove it
                # in the end.
                LOGGER.warning("skipping a malformed %s (id %s)", self.noun, row["id"])
        return results

    def update(self, identifier: int, *, label=None, reported=None) -> Optional[Dict[str, Any]]:
        changes = []
        values: List[Any] = []
        if label is not None:
            changes.append("label = ?")
            values.append(clean_label(label))
        if reported is not None:
            changes.append("reported_json = ?")
            values.append(_encoded(clean_reported(reported), self.noun))
        with self.repository._lock:
            if changes:
                try:
                    cursor = self.db.execute(
                        f"UPDATE {self.table} SET {', '.join(changes)} WHERE id = ?",
                        (*values, identifier),
                    )
                    self.repository._commit()
                except sqlite3.Error as error:
                    self.repository._rollback()
                    raise StorageError(f"could not update the {self.noun}") from error
                if cursor.rowcount == 0:
                    return None
            return self.get(identifier)

    def delete(self, identifier: int) -> bool:
        with self.repository._lock:
            try:
                cursor = self.db.execute(
                    f"DELETE FROM {self.table} WHERE id = ?", (identifier,)
                )
                self.repository._commit()
            except sqlite3.Error as error:
                self.repository._rollback()
                raise StorageError(f"could not delete the {self.noun}") from error
        return cursor.rowcount > 0


def _object(text: str) -> Dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


class ConnectionCheckStore(SpeedTestStore):
    """The rows of the connection check: the same columns and bounds, another table."""

    table = CHECK_TABLE
    noun = "connection check result"


def _result(row: sqlite3.Row, noun: str) -> Dict[str, Any]:
    try:
        return {
            "id": row["id"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "mac_address": row["mac_address"],
            "device_id": row["device_id"],
            "device_name": row["device_name"],
            "label": row["label"],
            "status": row["status"],
            "provider": row["provider"],
            "source": row["source"],
            "protected": bool(row["protected"]),
            "profile": _object(row["profile_json"]),
            "summary": _object(row["summary_json"]),
            "reported": _object(row["reported_json"]),
        }
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise StorageError(f"stored {noun} is malformed") from error

"""Saved device speed test results (see docs/implementation-plan-device-speed-test-2026-10-09.md).

The rows live in the configuration database next to the activity log. They hold what the Pi
measured, the profile that was in force and the numbers a person typed in from the device's
own speed test page. They never hold a token, a password or the dashboard's address.
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional

from .storage import StorageError

MAX_RESULTS_PER_DEVICE = 50
MAX_RESULTS_TOTAL = 500
MAX_LABEL_LENGTH = 80
MAX_JSON_BYTES = 16_384
RESULT_STATUSES = frozenset(("complete", "finished-early", "changed"))
REPORTED_FIELDS = ("download_mbit", "upload_mbit", "latency_ms", "jitter_ms")
REPORTED_MAXIMUM = 100_000.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS speed_test_results (
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
    profile_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    reported_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS speed_test_results_device_idx
    ON speed_test_results(mac_address, id DESC);
"""


def ensure_speed_test_schema(connection: sqlite3.Connection) -> None:
    """Create the table and its index when missing. Safe to run on every start."""

    connection.executescript(SCHEMA)


def clean_label(value: Any) -> str:
    """A short label without control characters, or ValueError."""

    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Label must be text")
    cleaned = " ".join(value.split())
    if any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
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


def _encoded(value: Dict[str, Any]) -> str:
    body = json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)
    if len(body.encode("utf-8")) > MAX_JSON_BYTES:
        raise StorageError("speed test result is too large")
    return body


class SpeedTestStore:
    """Read and write speed test rows on the repository's connection and lock."""

    def __init__(self, repository):
        self.repository = repository
        self.db = repository._connection

    def save(self, row: Dict[str, Any]) -> Dict[str, Any]:
        if row["status"] not in RESULT_STATUSES:
            raise StorageError("speed test status is invalid")
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
            _encoded(row.get("profile") or {}),
            _encoded(row.get("summary") or {}),
            _encoded(clean_reported(row.get("reported"))),
        )
        with self.repository._lock:
            try:
                cursor = self.db.execute(
                    "INSERT INTO speed_test_results (started_at, finished_at, mac_address, "
                    "device_id, device_name, label, status, provider, source, protected, "
                    "profile_json, summary_json, reported_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
                identifier = cursor.lastrowid
                self.db.execute(
                    "DELETE FROM speed_test_results WHERE mac_address = ? AND id NOT IN "
                    "(SELECT id FROM speed_test_results WHERE mac_address = ? "
                    "ORDER BY id DESC LIMIT ?)",
                    (row["mac_address"], row["mac_address"], MAX_RESULTS_PER_DEVICE),
                )
                self.db.execute(
                    "DELETE FROM speed_test_results WHERE id NOT IN "
                    "(SELECT id FROM speed_test_results ORDER BY id DESC LIMIT ?)",
                    (MAX_RESULTS_TOTAL,),
                )
                self.repository._commit()
            except sqlite3.Error as error:
                self.repository._rollback()
                raise StorageError("could not save the speed test result") from error
        return self.get(identifier)

    def get(self, identifier: int) -> Optional[Dict[str, Any]]:
        with self.repository._lock:
            row = self.db.execute(
                "SELECT * FROM speed_test_results WHERE id = ?", (identifier,)
            ).fetchone()
        return None if row is None else _result(row)

    def list(self, mac_address: Optional[str] = None, *, limit: int = 50) -> List[Dict[str, Any]]:
        with self.repository._lock:
            if mac_address is None:
                rows = self.db.execute(
                    "SELECT * FROM speed_test_results ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT * FROM speed_test_results WHERE mac_address = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (mac_address, limit),
                ).fetchall()
        return [_result(row) for row in rows]

    def update(self, identifier: int, *, label=None, reported=None) -> Optional[Dict[str, Any]]:
        changes = []
        values: List[Any] = []
        if label is not None:
            changes.append("label = ?")
            values.append(clean_label(label))
        if reported is not None:
            changes.append("reported_json = ?")
            values.append(_encoded(clean_reported(reported)))
        with self.repository._lock:
            if changes:
                cursor = self.db.execute(
                    f"UPDATE speed_test_results SET {', '.join(changes)} WHERE id = ?",
                    (*values, identifier),
                )
                self.repository._commit()
                if cursor.rowcount == 0:
                    return None
            return self.get(identifier)

    def delete(self, identifier: int) -> bool:
        with self.repository._lock:
            cursor = self.db.execute("DELETE FROM speed_test_results WHERE id = ?", (identifier,))
            self.repository._commit()
        return cursor.rowcount > 0


def _result(row: sqlite3.Row) -> Dict[str, Any]:
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
            "profile": json.loads(row["profile_json"]),
            "summary": json.loads(row["summary_json"]),
            "reported": json.loads(row["reported_json"]),
        }
    except (KeyError, TypeError, ValueError) as error:
        raise StorageError("stored speed test result is malformed") from error

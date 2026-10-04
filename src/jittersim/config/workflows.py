"""Bounded configuration history and test records kept in the local database."""

import functools
import hashlib
import inspect
import json
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from jittersim.measure import _is_measurement, comparison_table

from .device_names import resolve_device_name
from .storage import StorageError
from .transfer import settings_to_payload

MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_SESSIONS = 50
MAX_SESSION_EVENTS = 500
MAX_MEASUREMENTS = 20


def encoded(value, limit=MAX_SNAPSHOT_BYTES):
    body = json.dumps(value, sort_keys=True, allow_nan=False)
    if len(body.encode()) > limit:
        raise ValueError("record is too large")
    return body


def settings_snapshot(repository):
    return settings_to_payload(repository.saved_settings(), exported_at=0)


def target_title(repository, target):
    """Name the target of a test session the way the dashboard's target menu does."""

    if target == "everyone-else":
        return "Everyone else"
    kind, _, identifier = str(target).partition(":")
    if kind == "group":
        group = repository.get_device_group(identifier)
        return f"Group {group.name}" if group is not None else f"Deleted group ({identifier})"
    if kind == "device":
        device = repository.get_device(identifier)
        if device is None:
            return f"Deleted device ({identifier})"
        label = repository.list_connected_device_labels().get(device.mac_address or "", {})
        name = resolve_device_name(saved_label=label.get("display_name"), saved_name=device.name)
        return f"{name} ({device.ipv4_address})"
    return str(target)


def short_text(value, label, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(f"{label} must contain {'0' if empty else '1'} to {limit} characters")
    return value.strip()


class WorkflowStore:
    """Use the repository's connection so snapshots share its transaction."""

    def __init__(self, repository):
        self.repository = repository
        self.db = repository._connection

    def checkpoint(self, label):
        payload = settings_snapshot(self.repository)
        try:
            body = encoded(payload)
        except ValueError:
            self.db.execute(
                "INSERT OR REPLACE INTO settings_history_status VALUES (1, ?)",
                (
                    "Saved settings exceed the 1 MB history limit. Export a settings backup. "
                    "New history versions cannot be retained until the settings are smaller.",
                ),
            )
            self.repository._commit()
            self.capture_session_state(label)
            return None
        self.db.execute("DELETE FROM settings_history_status")
        digest = hashlib.sha256(body.encode()).hexdigest()
        previous = self.db.execute(
            "SELECT digest FROM settings_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if previous is None or previous[0] != digest:
            self.db.execute(
                "INSERT INTO settings_history (happened_at, label, digest, settings_json) "
                "VALUES (?, ?, ?, ?)",
                (int(time.time()), label[:120], digest, body),
            )
            self.db.execute(
                "DELETE FROM settings_history WHERE id NOT IN "
                "(SELECT id FROM settings_history ORDER BY id DESC LIMIT 20)"
            )
        self.repository._commit()
        self.capture_session_state(label)
        return digest

    def history_warning(self):
        row = self.db.execute("SELECT warning FROM settings_history_status WHERE id = 1").fetchone()
        return row[0] if row else None

    def capture_session_state(self, label):
        row = self.db.execute(
            "SELECT id, captures_json FROM test_sessions WHERE ended_at IS NULL"
        ).fetchone()
        if row is None:
            return
        captures = json.loads(row["captures_json"])
        now = int(time.time())
        state = {
            "settings": settings_snapshot(self.repository),
            "timers": [asdict(item) for item in self.repository.list_active_timers(now=now)],
            "progressions": [
                asdict(item) for item in self.repository.list_active_progressions(now=now)
            ],
            "scenario_runs": [asdict(run) for run, _ in self.repository.list_scenario_runs()],
        }
        if captures and captures[-1]["state"] == state:
            return
        captures.append({"at": now, "reason": label[:120], "state": state})
        try:
            body = encoded(captures, 2 * MAX_SNAPSHOT_BYTES)
            if len(captures) > 20:
                raise ValueError("too many changes")
        except ValueError:
            self.db.execute(
                "UPDATE test_sessions SET captures_truncated = 1 WHERE id = ?", (row["id"],)
            )
        else:
            self.db.execute(
                "UPDATE test_sessions SET captures_json = ? WHERE id = ?", (body, row["id"])
            )
        self.repository._commit()

    def history(self):
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT id, happened_at, label, digest FROM settings_history ORDER BY id DESC LIMIT 20"
            )
        ]

    def history_item(self, identifier):
        row = self.db.execute(
            "SELECT settings_json FROM settings_history WHERE id = ?", (identifier,)
        ).fetchone()
        if row is None:
            raise ValueError("settings version no longer exists")
        return json.loads(row[0])

    def start_session(self, payload):
        name = short_text(payload.get("name"), "name", 120)
        notes = short_text(payload.get("notes", ""), "notes", 8000, empty=True)
        target = short_text(payload.get("target"), "target", 160)
        available = {"everyone-else"}
        available.update("device:" + device.id for device in self.repository.list_devices())
        available.update("group:" + group.id for group in self.repository.list_device_groups())
        if target not in available:
            raise ValueError("choose an existing device or group")
        if self.db.execute("SELECT 1 FROM test_sessions WHERE ended_at IS NULL").fetchone():
            raise ValueError("finish the current test session first")
        snapshot = encoded(settings_snapshot(self.repository))
        identifier = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO test_sessions (id, name, target, notes, started_at, settings_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (identifier, name, target, notes, int(time.time()), snapshot),
        )
        self.db.execute(
            "DELETE FROM test_sessions WHERE id NOT IN "
            "(SELECT id FROM test_sessions ORDER BY started_at DESC, rowid DESC LIMIT ?)",
            (MAX_SESSIONS,),
        )
        self.repository._commit()
        self.capture_session_state("Session started")
        return self.session(identifier)

    def sessions(self):
        return [
            {**dict(row), "target_name": target_title(self.repository, row["target"])}
            for row in self.db.execute(
                "SELECT id, name, target, started_at, ended_at FROM test_sessions "
                "ORDER BY started_at DESC, rowid DESC LIMIT ?",
                (MAX_SESSIONS,),
            )
        ]

    def session(self, identifier):
        row = self.db.execute("SELECT * FROM test_sessions WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("test session no longer exists")
        result = dict(row)
        result["target_name"] = target_title(self.repository, result["target"])
        result["settings"] = json.loads(result.pop("settings_json"))
        result["captures"] = json.loads(result.pop("captures_json"))
        result["measurements"] = json.loads(result.pop("measurements_json"))
        result["events"] = [
            dict(event)
            for event in self.db.execute(
                "SELECT happened_at, kind, message FROM test_session_events "
                "WHERE session_id = ? ORDER BY id LIMIT ?",
                (identifier, MAX_SESSION_EVENTS),
            )
        ]
        result["events_truncated"] = bool(result["events_truncated"])
        return result

    def edit_session(self, payload):
        identifier = short_text(payload.get("id"), "session ID", 64)
        current = self.session(identifier)
        notes = short_text(payload.get("notes", current["notes"]), "notes", 8000, empty=True)
        measurements = payload.get("measurements", current["measurements"])
        if not isinstance(measurements, list) or len(measurements) > MAX_MEASUREMENTS:
            raise ValueError("a session can hold at most 20 measurements")
        for run in measurements:
            if not _is_measurement(run) or len(run.get("samples") or []) > 2000:
                raise ValueError("measurement must be valid and contain at most 2000 samples")
            short_text(run["label"], "measurement label", 120)
            short_text(run["target"], "measurement target", 255)
            summary = run["summary"]
            if (
                not 0 <= summary["loss_percent"] <= 100
                or any(
                    summary.get(key) is not None and summary[key] < 0
                    for key in ("rtt_avg_ms", "rtt_p95_ms", "jitter_ms")
                )
                or any(sample["rtt_ms"] < 0 for sample in run.get("samples") or [])
            ):
                raise ValueError("measurement delay and loss values are out of range")
            if run.get("download") and run["download"]["mbit_per_s"] < 0:
                raise ValueError("measurement download speed cannot be negative")
        body = encoded(measurements)
        # Finishing is idempotent. A completed session never starts capturing again.
        ended = current["ended_at"]
        if payload.get("finish") is True and ended is None:
            ended = int(time.time())
        expected = payload.get("revision")
        if not isinstance(expected, int) or isinstance(expected, bool):
            raise ValueError("reload the session before saving")
        cursor = self.db.execute(
            "UPDATE test_sessions SET notes = ?, measurements_json = ?, ended_at = ?, "
            "revision = revision + 1 WHERE id = ? AND revision = ?",
            (notes, body, ended, identifier, expected),
        )
        if not cursor.rowcount:
            raise StorageError("session changed in another window")
        self.repository._commit()
        return self.session(identifier)


def _under_repository_lock(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.repository._lock:
            return method(self, *args, **kwargs)

    return wrapper


# The store shares the repository connection, so its methods share the repository lock.
for _name, _member in tuple(vars(WorkflowStore).items()):
    if inspect.isfunction(_member) and _name != "__init__":
        setattr(WorkflowStore, _name, _under_repository_lock(_member))


def session_report(session):
    def line(value):
        return str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|")

    def moment(stamp):
        return datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp else "Still running"

    lines = [
        "# " + line(session["name"]),
        "",
        "Target: " + line(session.get("target_name") or session["target"]),
        "Started: " + moment(session["started_at"]),
        "Finished: " + moment(session["ended_at"]),
        "",
        "## Notes",
        "",
        session["notes"],
        "",
        "## Measurements",
        "",
        comparison_table(session["measurements"]),
        "",
        "## Network events",
        "",
        "Events cover the whole test network.",
        "",
    ]
    lines.extend(
        moment(event["happened_at"]) + " — " + line(event["message"]) for event in session["events"]
    )
    if session["events_truncated"]:
        lines.append("Event limit reached. Later events were not retained.")
    lines += [
        "",
        "## Settings and temporary runs during the session",
        "",
        "```json",
        encoded(session["captures"], 2 * MAX_SNAPSHOT_BYTES),
        "```",
    ]
    if session["captures_truncated"]:
        lines.append("Settings snapshot limit reached. Some later changes are not retained.")
    lines += [
        "",
        "## Settings at the start",
        "",
        "```json",
        encoded(session["settings"]),
        "```",
        "",
        "A seed repeats configured randomness, not physical network conditions.",
    ]
    return "\n".join(lines)

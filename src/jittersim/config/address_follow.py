"""Stored evidence, history and the profile move for following private address changes.

Host names are personal data, so this keeps only what the matching rules need:
the last host name, IPv4 address and time seen of addresses that belong to a
saved device, a saved name or protection. Rows end 30 days after the address was
last seen, when the address is no longer saved, or when the owner turns the
feature off. The history keeps reason codes, never a host name. None of it is in
the settings export, import or version history. The database backup copies the
whole database and so includes it. See
docs/implementation-plan-follow-address-changes-2026-10-17.md.
"""

import json
import sqlite3
from dataclasses import asdict, replace
from typing import Callable, Dict, List, Optional, Set, Tuple

from jittersim.engine.address_follow import (
    SIGHTING_RETENTION_SECONDS,
    STABLE_SECONDS,
    Arrival,
    build_identities,
    find_suggestions,
    is_private_address,
    normalize_hostname,
)

from .storage import DeviceConflictError, StorageError

MODES = ("off", "ask", "automatic")
DEFAULT_MODE = "ask"
HISTORY_LIMIT = 100
HISTORY_RETENTION_SECONDS = 90 * 24 * 60 * 60
UNDO_SECONDS = 7 * 24 * 60 * 60
DISMISSAL_LIMIT = 256
VETO_LIMIT = 1024
# Sightings change at most this often per address, so a page poll does not write every time.
SIGHTING_WRITE_SECONDS = 25
AUTOMATIC_PER_DAY = 3
AUTOMATIC_GAP_SECONDS = 10 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS address_follow_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mode TEXT NOT NULL CHECK (mode IN ('off', 'ask', 'automatic'))
);
CREATE TABLE IF NOT EXISTS address_sightings (
    mac_address TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    ipv4_address TEXT,
    last_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS address_follow_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    happened_at INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('ask', 'automatic')),
    device_id TEXT,
    name TEXT NOT NULL,
    old_mac TEXT NOT NULL,
    new_mac TEXT NOT NULL,
    device_before TEXT,
    device_after TEXT,
    label_after TEXT,
    protection_move_id TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL,
    undone_at INTEGER
);
CREATE TABLE IF NOT EXISTS address_follow_dismissals (
    old_mac TEXT NOT NULL,
    new_mac TEXT NOT NULL,
    until INTEGER NOT NULL,
    PRIMARY KEY (old_mac, new_mac)
);
CREATE TABLE IF NOT EXISTS address_follow_vetoes (
    old_mac TEXT NOT NULL,
    new_mac TEXT NOT NULL,
    seen_at INTEGER NOT NULL,
    PRIMARY KEY (old_mac, new_mac)
);
"""

HISTORY_COLUMNS = (
    "id", "happened_at", "mode", "device_id", "name", "old_mac", "new_mac", "device_before",
    "device_after", "label_after", "protection_move_id", "evidence", "undone_at",
)
JSON_COLUMNS = ("device_before", "device_after", "label_after", "evidence")


class FollowConflict(ValueError):
    """Something changed, so nothing was moved or undone."""


class AddressFollowStore:
    """Use the repository's connection and lock, like the workflow store."""

    def __init__(self, repository):
        self.repository = repository
        self.db = repository._connection
        if not getattr(repository, "_address_follow_schema", False):
            try:
                with repository.transaction():
                    for statement in SCHEMA.split(";"):
                        if statement.strip():
                            self.db.execute(statement)
            except sqlite3.Error as error:
                raise StorageError("could not prepare address follow tables") from error
            repository._address_follow_schema = True

    def _read(self, sql: str, parameters=()) -> list:
        try:
            with self.repository._lock:
                return self.db.execute(sql, parameters).fetchall()
        except sqlite3.Error as error:
            raise StorageError("could not read address follow data") from error

    def mode(self) -> str:
        rows = self._read("SELECT mode FROM address_follow_settings WHERE id = 1")
        return rows[0]["mode"] if rows else DEFAULT_MODE

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError("mode must be off, ask or automatic")
        try:
            with self.repository.transaction():
                self.db.execute(
                    "INSERT INTO address_follow_settings (id, mode) VALUES (1, ?) "
                    "ON CONFLICT(id) DO UPDATE SET mode = excluded.mode",
                    (mode,),
                )
                if mode == "off":
                    # Off keeps no host names and no observations.
                    for table in ("address_sightings", "address_follow_dismissals",
                                  "address_follow_vetoes"):
                        self.db.execute(f"DELETE FROM {table}")
        except sqlite3.Error as error:
            raise StorageError("could not save the address follow setting") from error

    def sightings(self) -> Dict[str, Tuple[str, Optional[str], int]]:
        return {
            row["mac_address"]: (row["hostname"], row["ipv4_address"], row["last_seen"])
            for row in self._read(
                "SELECT mac_address, hostname, ipv4_address, last_seen FROM address_sightings"
            )
        }

    def observe(self, rows: List[dict], *, saved: Set[str], now: int) -> None:
        """Remember connected saved addresses, note pairs seen together, forget the rest.

        A missing host name keeps the one seen before, because a device sends it
        only when it asks for a lease. A saved address and an unknown private
        address that are connected at the same time are two devices, so that pair
        is vetoed for 30 days even if one of them reconnects later.
        """

        known = self.sightings()
        present_saved = [row for row in rows if row["mac_address"] in saved]
        present_new = [
            row["mac_address"] for row in rows
            if row["mac_address"] not in saved and is_private_address(row["mac_address"])
        ]
        try:
            with self.repository.transaction():
                for row in present_saved:
                    mac = row["mac_address"]
                    name = row["hostname"]
                    ipv4 = row["ipv4_address"] if row["ipv4_verified"] else None
                    previous = known.get(mac)
                    if previous is not None:
                        name = name or previous[0]
                        ipv4 = ipv4 or previous[1]
                        if ((name, ipv4) == previous[:2]
                                and 0 <= now - previous[2] < SIGHTING_WRITE_SECONDS):
                            continue
                    self.db.execute(
                        "INSERT INTO address_sightings (mac_address, hostname, ipv4_address, "
                        "last_seen) VALUES (?, ?, ?, ?) ON CONFLICT(mac_address) DO UPDATE SET "
                        "hostname = excluded.hostname, ipv4_address = excluded.ipv4_address, "
                        "last_seen = excluded.last_seen",
                        (mac, name, ipv4, int(now)),
                    )
                for row in present_saved:
                    for new_mac in present_new:
                        self.db.execute(
                            "INSERT INTO address_follow_vetoes (old_mac, new_mac, seen_at) "
                            "VALUES (?, ?, ?) ON CONFLICT(old_mac, new_mac) DO UPDATE SET "
                            "seen_at = excluded.seen_at",
                            (row["mac_address"], new_mac, int(now)),
                        )
                for mac, (_, _, last_seen) in known.items():
                    if mac not in saved or now - last_seen > SIGHTING_RETENTION_SECONDS:
                        self.db.execute(
                            "DELETE FROM address_sightings WHERE mac_address = ?", (mac,)
                        )
                self.db.execute(
                    "DELETE FROM address_follow_vetoes WHERE seen_at < ? OR rowid NOT IN ("
                    "SELECT rowid FROM address_follow_vetoes ORDER BY seen_at DESC LIMIT ?)",
                    (int(now) - SIGHTING_RETENTION_SECONDS, VETO_LIMIT),
                )
        except sqlite3.Error as error:
            raise StorageError("could not save address sightings") from error

    def vetoed(self) -> Set[Tuple[str, str]]:
        return {
            (row["old_mac"], row["new_mac"])
            for row in self._read("SELECT old_mac, new_mac FROM address_follow_vetoes")
        }

    def dismissed(self, *, now: int) -> Set[Tuple[str, str]]:
        return {
            (row["old_mac"], row["new_mac"])
            for row in self._read(
                "SELECT old_mac, new_mac FROM address_follow_dismissals WHERE until > ?", (now,)
            )
        }

    def dismiss(self, old_mac: str, new_mac: str, *, until: int, now: int) -> None:
        try:
            with self.repository.transaction():
                self.db.execute("DELETE FROM address_follow_dismissals WHERE until <= ?", (now,))
                self.db.execute(
                    "INSERT INTO address_follow_dismissals (old_mac, new_mac, until) "
                    "VALUES (?, ?, ?) ON CONFLICT(old_mac, new_mac) DO UPDATE SET "
                    "until = excluded.until",
                    (old_mac, new_mac, int(until)),
                )
                self.db.execute(
                    "DELETE FROM address_follow_dismissals WHERE rowid NOT IN ("
                    "SELECT rowid FROM address_follow_dismissals ORDER BY until DESC LIMIT ?)",
                    (DISMISSAL_LIMIT,),
                )
        except sqlite3.Error as error:
            raise StorageError("could not save the dismissal") from error

    def history_full(self, *, now: int) -> bool:
        """True when every kept entry is still undoable, so a new move would push one out."""

        rows = self._read(
            "SELECT COUNT(*) AS count FROM address_follow_history WHERE happened_at > ?",
            (int(now) - UNDO_SECONDS,),
        )
        return rows[0]["count"] >= HISTORY_LIMIT

    def add_history(self, entry: dict, *, now: int) -> int:
        """Write one entry. Call inside the move's transaction so both commit together.

        Old entries go after 90 days. Beyond 100 entries only those whose Undo
        has run out are removed, so a promised Undo never disappears early.
        """

        values = {column: entry.get(column) for column in HISTORY_COLUMNS if column != "id"}
        values["happened_at"] = int(now)
        values["protection_move_id"] = values["protection_move_id"] or ""
        values["evidence"] = list(values["evidence"] or ())
        for column in JSON_COLUMNS:
            if values[column] is not None:
                values[column] = json.dumps(values[column], sort_keys=True)
        columns = tuple(values)
        cursor = self.db.execute(
            f"INSERT INTO address_follow_history ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            tuple(values[column] for column in columns),
        )
        self.db.execute(
            "DELETE FROM address_follow_history WHERE happened_at < ? OR (happened_at < ? "
            "AND id NOT IN (SELECT id FROM address_follow_history ORDER BY id DESC LIMIT ?))",
            (int(now) - HISTORY_RETENTION_SECONDS, int(now) - UNDO_SECONDS, HISTORY_LIMIT),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _entry(row) -> dict:
        item = {column: row[column] for column in HISTORY_COLUMNS}
        for column in JSON_COLUMNS:
            item[column] = json.loads(item[column]) if item[column] else None
        item["evidence"] = item["evidence"] or []
        return item

    def history(self, *, limit: int = HISTORY_LIMIT) -> List[dict]:
        rows = self._read(
            f"SELECT {', '.join(HISTORY_COLUMNS)} FROM address_follow_history "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [self._entry(row) for row in rows]

    def history_entry(self, entry_id: int) -> Optional[dict]:
        rows = self._read(
            f"SELECT {', '.join(HISTORY_COLUMNS)} FROM address_follow_history WHERE id = ?",
            (entry_id,),
        )
        return self._entry(rows[0]) if rows else None

    def later_moves(self, entry: dict) -> bool:
        """True when a later move that is not undone touches the same device or addresses."""

        rows = self._read(
            "SELECT 1 FROM address_follow_history WHERE id > ? AND undone_at IS NULL AND ("
            "old_mac IN (?, ?) OR new_mac IN (?, ?) OR (device_id IS NOT NULL AND device_id = ?)) "
            "LIMIT 1",
            (entry["id"], entry["old_mac"], entry["new_mac"], entry["old_mac"], entry["new_mac"],
             entry["device_id"]),
        )
        return bool(rows)

    def mark_undone(self, entry_id: int, *, now: int) -> bool:
        cursor = self.db.execute(
            "UPDATE address_follow_history SET undone_at = ? WHERE id = ? AND undone_at IS NULL",
            (int(now), entry_id),
        )
        return cursor.rowcount == 1

    def recent_moves(self, device_id: str, *, since: int) -> List[int]:
        """When this saved device was moved since `since`, newest first, undone or not."""

        return [
            row["happened_at"]
            for row in self._read(
                "SELECT happened_at FROM address_follow_history WHERE device_id = ? "
                "AND happened_at >= ? ORDER BY happened_at DESC",
                (device_id, since),
            )
        ]


def station_rows(stations) -> List[dict]:
    """The verified Wi-Fi stations, with only the fields the rules use."""

    rows = []
    for station in stations or ():
        if not isinstance(station, dict) or station.get("association_verified") is not True:
            continue
        mac = station.get("mac_address")
        if not isinstance(mac, str):
            continue
        seconds = station.get("connected_seconds")
        ipv4 = station.get("ipv4_address")
        rows.append({
            "mac_address": mac.lower(),
            "hostname": normalize_hostname(station.get("hostname")),
            "ipv4_address": ipv4 if isinstance(ipv4, str) else None,
            "connected_seconds": seconds if type(seconds) is int and seconds >= 0 else None,
            "ipv4_verified": isinstance(ipv4, str) and station.get("neighbor_state") == "reachable",
        })
    # An IPv4 address that two stations claim confirms neither of them.
    claimed: Dict[str, int] = {}
    for row in rows:
        if row["ipv4_address"]:
            claimed[row["ipv4_address"]] = claimed.get(row["ipv4_address"], 0) + 1
    for row in rows:
        if row["ipv4_address"] and claimed[row["ipv4_address"]] > 1:
            row["ipv4_verified"] = False
    return rows


def saved_identities(repository, store: AddressFollowStore) -> list:
    """Every saved address with something to carry, with its remembered sighting.

    An address that a verified move already replaced is left out: its entries
    live at the new address now.
    """

    replaced = {move.old_mac for move in repository.list_address_moves()}
    labels = {
        mac: label.get("display_name") or ""
        for mac, label in repository.list_connected_device_labels().items()
    }
    return [
        identity
        for identity in build_identities(
            devices=[
                (device.id, device.name, str(device.ipv4_address), device.mac_address)
                for device in repository.list_devices()
            ],
            protected={
                item.mac_address: item.display_name
                for item in repository.list_unaffected_devices()
            },
            labels=labels,
            sightings=store.sightings(),
        )
        if identity.mac_address not in replaced
    ]


def current_suggestions(repository, snapshot, *, now: int):
    """Observe one station snapshot and return (mode, suggestions).

    Without a current, verified station list nobody can be called absent, so
    nothing is suggested and nothing is recorded.
    """

    store = AddressFollowStore(repository)
    mode = store.mode()
    if (
        mode == "off"
        or not isinstance(snapshot, dict)
        or snapshot.get("association_status_available") is not True
        or snapshot.get("stale")
    ):
        return mode, []
    rows = station_rows(snapshot.get("devices"))
    saved = {identity.mac_address for identity in saved_identities(repository, store)}
    store.observe(rows, saved=saved, now=now)
    identities = saved_identities(repository, store)
    suggestions = find_suggestions(
        identities,
        [Arrival(**row) for row in rows],
        present={row["mac_address"] for row in rows},
        now=now,
        dismissed=store.dismissed(now=now),
        vetoed=store.vetoed(),
    )
    return mode, suggestions


def automatic_allowed(store: AddressFollowStore, suggestion, *, now: int) -> bool:
    """Strong evidence, a stable new address, a profile to move, no protection, within limits.

    A protected device's profile is dormant. Moving only the profile would start
    shaping the device, so a protected device always waits for the owner.
    """

    old = suggestion.old
    if not suggestion.evidence.strong or not old.device_id or old.protected:
        return False
    seconds = suggestion.new.connected_seconds
    if seconds is None or seconds < STABLE_SECONDS:
        return False
    moves = store.recent_moves(old.device_id, since=now - 24 * 60 * 60)
    return len(moves) < AUTOMATIC_PER_DAY and not (
        moves and now - moves[0] < AUTOMATIC_GAP_SECONDS
    )


def _device_values(device) -> dict:
    values = asdict(device)
    values["ipv4_address"] = str(device.ipv4_address)
    return values


def move_profile_and_label(
    repository,
    store,
    suggestion,
    *,
    mode: str,
    now: int,
    protection_move_id: str = "",
    allowed: Optional[Callable[[], bool]] = None,
) -> dict:
    """Point the saved device and its name at the new address in one transaction.

    Only the MAC address changes. This is the same change as editing the
    device's MAC address on the Devices page: save_device refuses a stale edit
    and a MAC another saved device uses. The scheduler's address check then
    moves the device to its new IPv4 address with the existing grouped rules,
    which handle swaps and keep traffic progressions in step.
    `allowed` is asked again just before the commit, so a Panic that started
    meanwhile stops an automatic move.
    """

    old, new = suggestion.old, suggestion.new
    with repository.transaction():
        if store.history_full(now=now):
            raise FollowConflict(
                "Too many recent moves to keep their Undo. Wait or undo one first."
            )
        device_before = device_after = None
        if old.device_id:
            device = repository.get_device(old.device_id)
            if device is None or device.mac_address != old.mac_address:
                raise FollowConflict("The saved device changed. Nothing was moved.")
            moved = replace(device, mac_address=new.mac_address)
            try:
                repository.save_device(moved, expected=device)
            except DeviceConflictError as error:
                raise FollowConflict(str(error)) from error
            device_before, device_after = _device_values(device), _device_values(moved)
        labels = repository.list_connected_device_labels()
        label_after = None
        if old.mac_address in labels and new.mac_address not in labels:
            label = labels[old.mac_address]
            repository.save_connected_device_label(
                mac_address=new.mac_address,
                display_name=label["display_name"],
                device_type=label["device_type"],
            )
            repository.delete_connected_device_label(mac_address=old.mac_address)
            label_after = dict(label)
        if not (device_after or label_after or protection_move_id):
            raise FollowConflict("There is nothing to move for that device.")
        entry_id = store.add_history({
            "mode": mode, "device_id": old.device_id, "name": old.name,
            "old_mac": old.mac_address, "new_mac": new.mac_address,
            "device_before": device_before, "device_after": device_after,
            "label_after": label_after, "protection_move_id": protection_move_id,
            "evidence": list(suggestion.evidence.reasons),
        }, now=now)
        what = " and ".join(
            part for part, done in (("profile", device_after), ("name", label_after),
                                    ("protection", protection_move_id)) if done
        )
        how = "automatically" if mode == "automatic" else "after the owner said yes"
        repository.record_activities(
            [("address", f"{old.name} uses a new private address {new.mac_address}. "
                         f"Its {what} moved {how}.")],
            now=now,
            device_ids=[(old.device_id,) if old.device_id else ()],
        )
        if allowed is not None and not allowed():
            raise FollowConflict("The network was stopped. Nothing was moved.")
    return {"id": entry_id, "moved_profile": device_after is not None,
            "moved_label": label_after is not None,
            "moved_protection": bool(protection_move_id)}


def check_undo(repository, store, entry: dict, *, now: int) -> None:
    """Refuse an Undo that would overwrite anything that changed since the move."""

    if entry["undone_at"] is not None:
        raise FollowConflict("This move was already undone.")
    if now - entry["happened_at"] > UNDO_SECONDS:
        raise FollowConflict("This move is too old to undo. Edit the device instead.")
    if store.later_moves(entry):
        raise FollowConflict("A later move changed this device. Undo that one first.")
    if entry["device_after"] is not None:
        device = repository.get_device(entry["device_id"])
        current = _device_values(device) if device is not None else None
        after = dict(entry["device_after"])
        if current is not None:
            # The scheduler's address check may have moved it to the new IPv4 address.
            after["ipv4_address"] = current["ipv4_address"]
        if current != after:
            raise FollowConflict("The saved device changed since the move. Nothing was undone.")
    label = entry["label_after"]
    if label is not None:
        labels = repository.list_connected_device_labels()
        if labels.get(entry["new_mac"]) != label or entry["old_mac"] in labels:
            raise FollowConflict("The device name changed since the move. Nothing was undone.")


def undo_profile_and_label(repository, store, entry: dict, *, now: int) -> None:
    """Point the saved device and its name back at the old address."""

    with repository.transaction():
        check_undo(repository, store, entry, now=now)
        if entry["device_after"] is not None:
            device = repository.get_device(entry["device_id"])
            restored = replace(device, mac_address=entry["old_mac"])
            try:
                repository.save_device(restored, expected=device)
            except DeviceConflictError as error:
                raise FollowConflict(str(error)) from error
        label = entry["label_after"]
        if label is not None:
            repository.save_connected_device_label(
                mac_address=entry["old_mac"],
                display_name=label["display_name"],
                device_type=label["device_type"],
            )
            repository.delete_connected_device_label(mac_address=entry["new_mac"])
        if not store.mark_undone(entry["id"], now=now):
            raise FollowConflict("This move was already undone.")
        repository.record_activities(
            [("address", f"Undid the move of {entry['name']} to {entry['new_mac']}.")],
            now=now,
            device_ids=[(entry["device_id"],) if entry["device_id"] else ()],
        )


def follow_automatically(repository, snapshot, *, now: int,
                         allowed: Optional[Callable[[], bool]] = None) -> list:
    """The scheduler's step: move strong, unique, stable matches when the owner chose Automatic.

    Returns the moves it made. Everything else waits for the owner.
    """

    mode, suggestions = current_suggestions(repository, snapshot, now=now)
    if mode != "automatic":
        return []
    store = AddressFollowStore(repository)
    done = []
    for suggestion in suggestions:
        if not automatic_allowed(store, suggestion, now=now):
            continue
        try:
            done.append(move_profile_and_label(repository, store, suggestion,
                                               mode="automatic", now=now, allowed=allowed))
        except (FollowConflict, StorageError):
            continue
    return done

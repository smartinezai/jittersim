"""Dashboard routes for following a saved device to its new private Wi-Fi address.

The matching rules live in jittersim.engine.address_follow and the stored data in
jittersim.config.address_follow. This module only reads the live station list,
checks a request against a suggestion computed again on the server and calls the
existing moves:

- the profile and the name move with the same database change as a device edit;
- protection moves only through ControlServer.move_device_address, the code the
  verified self-service move uses, with its own undo;
- a saved dashboard access grant never moves here. Only the device itself can
  move it with the verified move under Dashboard access.
"""

import json
import time

from jittersim.config.address_follow import (
    MODES,
    UNDO_SECONDS,
    AddressFollowStore,
    FollowConflict,
    StaleUndo,
    automatic_allowed,
    check_undo,
    current_suggestions,
    mark_protection_only_undone,
    move_profile_and_label,
    undo_profile_and_label,
)
from jittersim.config.exemptions import normalize_exempt_mac
from jittersim.config.storage import StorageError
from jittersim.engine.address_follow import describe
from jittersim.engine.eligibility import station_snapshot

ADDRESS_FOLLOW_READ = "/api/address-follow"
ADDRESS_FOLLOW_WRITES = frozenset(
    (
        "/api/address-follow/settings",
        "/api/address-follow/accept",
        "/api/address-follow/dismiss",
        "/api/address-follow/undo",
    )
)
HISTORY_SHOWN = 20
DISMISS_SECONDS = {"later": 24 * 60 * 60, "never": 90 * 24 * 60 * 60}
DISMISS_WARNING = "The move was undone, but this pair may be suggested again."


def _response(value, status=200):
    return status, "application/json", (json.dumps(value, allow_nan=False) + "\n").encode()


def _snapshot(server, mode):
    """The live station list. Off reads nothing from the helper."""

    if mode == "off" or server.helper_client is None:
        return None
    return station_snapshot(server.helper_client)


def _manual_only(server, repository, suggestions):
    """What stays at the old address for each suggestion, and why."""

    if not suggestions:
        return {}
    access = {
        str(entry.get("mac_address") or "").lower() for entry in server._active_access_entries()
    }
    try:
        policy = repository.get_mac_policy()
        listed = set(policy.allowlist) | set(policy.denylist)
        if policy.mode == "disabled":
            listed = set()
    except (StorageError, AttributeError):
        listed = set()
    return {
        item.old.mac_address: {
            "dashboard_access": item.old.mac_address in access,
            "mac_list": item.old.mac_address in listed,
        }
        for item in suggestions
    }


def _suggestion_payload(item, manual, store, *, now, mode):
    old, new = item.old, item.new
    waiting = None
    if mode == "automatic":
        if old.protected:
            waiting = "Protected devices are never moved automatically."
        elif not automatic_allowed(store, item, now=now):
            waiting = "The evidence is not strong enough to move it automatically."
    return {
        "old_mac": old.mac_address,
        "new_mac": new.mac_address,
        "name": old.name,
        "hostname": new.hostname,
        "new_ipv4": new.ipv4_address,
        "points": item.evidence.points,
        "strong": item.evidence.strong,
        "reasons": list(item.evidence.reasons),
        "evidence": describe(item.evidence, hostname=new.hostname),
        "moves": {
            "profile": bool(old.device_id),
            "name": old.labelled,
            "protection": old.protected,
        },
        "stays": manual.get(old.mac_address, {"dashboard_access": False, "mac_list": False}),
        "automatic_waiting": waiting,
    }


def _history_payload(store, *, now):
    return [
        {
            "id": entry["id"],
            "happened_at": entry["happened_at"],
            "mode": entry["mode"],
            "name": entry["name"],
            "old_mac": entry["old_mac"],
            "new_mac": entry["new_mac"],
            "moved_profile": entry["device_after"] is not None,
            "moved_label": entry["label_after"] is not None,
            "moved_protection": bool(entry["protection_move_id"]),
            "undone_at": entry["undone_at"],
            "can_undo": entry["undone_at"] is None and now - entry["happened_at"] <= UNDO_SECONDS,
        }
        for index, entry in enumerate(store.history())
        # Every move that can still be undone stays visible, plus the newest others.
        if index < HISTORY_SHOWN or (
            entry["undone_at"] is None and now - entry["happened_at"] <= UNDO_SECONDS)
    ]


def read_address_follow(server, path):
    if path != ADDRESS_FOLLOW_READ:
        return None
    repository = server.repository
    store = AddressFollowStore(repository)
    now = int(time.time())
    mode, suggestions = current_suggestions(
        repository, _snapshot(server, store.mode()), now=now
    )
    manual = _manual_only(server, repository, suggestions)
    return _response({
        "mode": mode,
        "suggestions": [
            _suggestion_payload(item, manual, store, now=now, mode=mode) for item in suggestions
        ],
        "history": _history_payload(store, now=now),
        "undo_days": UNDO_SECONDS // 86400,
    })


def _pair(payload, *keys):
    if set(payload) != set(keys) | {"old_mac", "new_mac"}:
        raise ValueError("Name the saved device and the new address.")
    try:
        return normalize_exempt_mac(payload["old_mac"]), normalize_exempt_mac(payload["new_mac"])
    except (TypeError, ValueError) as error:
        raise ValueError("Name the saved device and the new address.") from error


def _accept(server, store, payload, *, now):
    old_mac, new_mac = _pair(payload, "move_protection")
    if type(payload["move_protection"]) is not bool:
        raise ValueError("move_protection must be true or false")
    repository = server.repository
    mode, suggestions = current_suggestions(
        repository, _snapshot(server, store.mode()), now=now
    )
    item = next(
        (item for item in suggestions
         if item.old.mac_address == old_mac and item.new.mac_address == new_mac),
        None,
    )
    if mode == "off" or item is None:
        return _response({
            "error": "This suggestion is no longer current. Nothing was moved.", "saved": False,
        }, 409)
    if store.history_full(now=now):
        return _response({
            "error": "Too many recent moves to keep their Undo. Wait or undo one first.",
            "saved": False,
        }, 409)
    if not item.old.protected:
        try:
            done = move_profile_and_label(repository, store, item, mode="ask", now=now)
        except FollowConflict as error:
            return _response({"error": str(error), "saved": False}, 409)
        return _response({"saved": True, "moved": done})
    if not payload["move_protection"]:
        return _response({
            "error": "This device is protected. Moving it moves its protection too. "
                     "Confirm that to continue.",
            "saved": False,
        }, 409)
    # The profile, the name and the history entry commit inside the existing protection
    # move's own transaction, before it validates the plan. Everything is saved together
    # or nothing is, and the plan is checked in its final state.
    moved = {}

    def also(record):
        moved.update(move_profile_and_label(repository, store, item, mode="ask", now=now,
                                            protection_move_id=record.move_id))

    status, _, body = server.move_device_address(old_mac, new_mac, access_moved=False, also=also)
    result = json.loads(body)
    if result.get("saved") is not True:
        return _response({"error": result.get("error") or "Protection could not be moved.",
                          "saved": False}, status)
    return _response(_with_live_outcome({"saved": True, "moved": moved}, status, result))


def _with_live_outcome(response, status, result):
    """Keep what the existing move said about the network and dashboard access."""

    notes = [str(result[key]) for key in ("error", "warning") if result.get(key)]
    if status != 200 or notes:
        response["warning"] = " ".join(notes) or "The network update is not confirmed."
    for key in ("live_applied", "dashboard_access"):
        if key in result:
            response[key] = result[key]
    return response


def _undo(server, store, payload, *, now):
    if set(payload) != {"id"} or type(payload["id"]) is not int:
        raise ValueError("Choose a move to undo.")
    entry = store.history_entry(payload["id"])
    if entry is None:
        return _response({"error": "There is no such move.", "saved": False}, 404)
    repository = server.repository
    try:
        check_undo(repository, store, entry, now=now)
    except StaleUndo as error:
        # Protection can still go back on its own. Without protection there is nothing left.
        if not entry["protection_move_id"]:
            return _response({"error": str(error), "saved": False}, 409)
    except FollowConflict as error:
        return _response({"error": str(error), "saved": False}, 409)
    status, result = 200, {}
    if entry["protection_move_id"]:
        record = repository.get_address_move(entry["old_mac"])
        if (record is None or record.move_id != entry["protection_move_id"]
                or record.new_mac != entry["new_mac"] or record.moved_access):
            return _response({
                "error": "Protection changed since this move. Nothing was undone.", "saved": False,
            }, 409)
        # The profile and the name go back inside the existing protection Undo's transaction.
        hook = _LinkedUndo(repository, store, entry)
        status, _, body = server.undo_device_address_move(entry["old_mac"], "", also=hook)
        result = json.loads(body)
        if result.get("saved") is not True:
            again = store.history_entry(entry["id"])
            if again is not None and again["undone_at"] is not None:
                # Another Undo, for example under Dashboard access, finished first.
                return _response({"error": "This move was already undone.", "saved": False}, 409)
            return _response({"error": result.get("error") or "This move could not be undone.",
                              "saved": False}, status)
    else:
        try:
            undo_profile_and_label(repository, store, entry, now=now)
        except FollowConflict as error:
            return _response({"error": str(error), "saved": False}, 409)
        # The owner said this was wrong, so the same pair is not suggested again.
        try:
            store.dismiss(entry["old_mac"], entry["new_mac"],
                          until=now + DISMISS_SECONDS["never"], now=now)
        except StorageError:
            result = {**result, "warning": DISMISS_WARNING}
    return _response(_with_live_outcome({"saved": True, "undone": entry["id"]}, status, result))


class _LinkedUndo:
    """Put a card move's profile and name back inside the existing protection Undo.

    When they can no longer be put back (the move is older than 7 days, or the
    saved device was edited or deleted) protection still goes back on its own, so
    a wrong protection move never has to stay. The entry is closed and `warning`
    says what was left.
    """

    def __init__(self, repository, store, entry):
        self.repository, self.store, self.entry = repository, store, entry
        self.now = int(time.time())
        self.protection_only = False
        self.warning = None

    def check(self):
        # Before the protection Undo changes anything, so a refusal leaves no half state.
        try:
            check_undo(self.repository, self.store, self.entry, now=self.now)
        except StaleUndo as error:
            self.protection_only = True
            self.reason = str(error).replace(" Nothing was undone.", "").replace(
                " Edit the device instead.", "")

    def __call__(self, _record):
        if self.protection_only:
            mark_protection_only_undone(self.repository, self.store, self.entry, now=self.now)
            self.warning = (
                "Protection is back at the old address. The profile and the name were left as "
                "they are: " + self.reason + " Check the saved device on the Devices page and "
                "the Protected devices list, and change them there by hand."
            )
            return
        undo_profile_and_label(self.repository, self.store, self.entry, now=self.now)
        try:
            # The owner said this was wrong, so the same pair is not suggested again.
            self.store.dismiss(self.entry["old_mac"], self.entry["new_mac"],
                               until=self.now + DISMISS_SECONDS["never"], now=self.now)
        except StorageError:
            self.warning = DISMISS_WARNING


def linked_address_follow_undo(repository, move_id):
    """The hook for the Undo under Dashboard access, or None when no card move is linked."""

    store = AddressFollowStore(repository)
    entry = store.entry_for_protection_move(move_id)
    return None if entry is None else _LinkedUndo(repository, store, entry)


def write_address_follow(server, path, payload):
    if path not in ADDRESS_FOLLOW_WRITES:
        return None
    store = AddressFollowStore(server.repository)
    now = int(time.time())
    try:
        if path.endswith("/settings"):
            mode = payload.get("mode")
            if set(payload) != {"mode"} or mode not in MODES:
                raise ValueError("Choose Off, Ask or Automatic.")
            store.set_mode(mode)
            return _response({"saved": True, "mode": mode})
        if path.endswith("/dismiss"):
            old_mac, new_mac = _pair(payload, "scope")
            seconds = DISMISS_SECONDS.get(payload["scope"])
            if seconds is None:
                raise ValueError("scope must be later or never")
            store.dismiss(old_mac, new_mac, until=now + seconds, now=now)
            return _response({"saved": True})
        if path.endswith("/accept"):
            return _accept(server, store, payload, now=now)
        return _undo(server, store, payload, now=now)
    except ValueError as error:
        return _response({"error": str(error), "saved": False}, 400)
    except StorageError as error:
        return _response({"error": str(error), "saved": False}, 409)

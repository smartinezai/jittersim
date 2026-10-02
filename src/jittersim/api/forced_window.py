"""Authenticated operations for the forced test network window."""

import json
from datetime import datetime

from jittersim.config.forced_window import ForcedWindow
from jittersim.config.storage import StorageError
from jittersim.networking.helper import HelperClientError

FORCED_WINDOW_WRITES = frozenset(
    (
        "/api/forced-windows",
        "/api/forced-windows/delete",
        "/api/forced-windows/login",
        "/api/forced-windows/login/clear",
        "/api/forced-windows/end",
    )
)
MAX_WINDOWS = 20
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def response(value, status=200):
    return status, "application/json", (json.dumps(value, allow_nan=False) + "\n").encode()


def window_payload(window):
    return {
        "id": window.id,
        "start_day": window.start_day,
        "start_minute": window.start_minute,
        "end_day": window.end_day,
        "end_minute": window.end_minute,
        "enabled": window.enabled,
    }


def _notice(state):
    """The sentence the dashboard and the command line show while the router's Wi-Fi is off."""

    if state["router_wifi_off"] and state["ends_at"]:
        ends = datetime.fromtimestamp(state["ends_at"])
        return f"The router's Wi-Fi is off until {DAYS[ends.weekday()]} {ends:%H:%M}."
    return None


def read_forced_window_api(server, path):
    if path != "/api/forced-windows":
        return None
    service = getattr(server, "forced_window", None)
    state = service.status() if service is not None else None
    login_saved = None
    if state is not None and server.helper_client is not None:
        try:
            login_saved = bool(server.helper_client.window_login_status().get("saved"))
        except (HelperClientError, AttributeError):
            login_saved = None
    return response(
        {
            "available": service is not None,
            "windows": [window_payload(window) for window in server.repository.list_forced_windows()],
            "state": state,
            "notice": _notice(state) if state else None,
            "login_saved": login_saved,
            "time_zone": datetime.now().astimezone().tzname(),
        }
    )


def write_forced_window_api(server, path, payload):
    if path not in FORCED_WINDOW_WRITES:
        return None
    repository = server.repository
    if path == "/api/forced-windows":
        try:
            window = ForcedWindow(
                id=payload["id"],
                start_day=payload["start_day"],
                start_minute=payload["start_minute"],
                end_day=payload["end_day"],
                end_minute=payload["end_minute"],
                enabled=payload.get("enabled", True),
            )
            known = {item.id for item in repository.list_forced_windows()}
            if window.id not in known and len(known) >= MAX_WINDOWS:
                raise ValueError("too many windows")
            repository.save_forced_window(window)
        except (KeyError, TypeError, ValueError, StorageError) as error:
            return response({"error": f"invalid window: {error}"}, 400)
        return response({"window": window_payload(window)})
    if path == "/api/forced-windows/delete":
        window_id = payload.get("id")
        if not isinstance(window_id, str) or not repository.delete_forced_window(window_id):
            return response({"error": "window no longer exists"}, 404)
        return response({"deleted": True})
    client = server.helper_client
    if client is None:
        return response({"error": "A networking helper is required for forced windows."}, 503)
    service = getattr(server, "forced_window", None)
    if path == "/api/forced-windows/end":
        if service is None:
            return response({"error": "Forced windows are not running."}, 503)
        service.request_end()
        return response({"ending": True})
    try:
        if path == "/api/forced-windows/login":
            password = payload.get("admin_password")
            if not isinstance(password, str) or not password or len(password) > 1024:
                return response({"error": "router admin password is required"}, 400)
            client.window_login_save(admin_password=password)
            return response({"login_saved": True})
        client.window_login_clear()
        return response({"login_saved": False})
    except HelperClientError as error:
        return response({"error": str(error)}, 502)

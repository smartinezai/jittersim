"""The dashboard's display rules in Python so the terminal says what the page says.

Each function here copies one function of the dashboard script in `api/server.py` and keeps
its name in the docstring. The page computes these in the browser so the rules cannot move
to one shared place without changing the page. `tests/unit/test_dashboard_rules_parity.py`
runs both versions on the same inputs and fails when they drift apart.
"""

from typing import Any, Dict, Optional


# --- section: status

SIDEBAR_INPUTS = ("runtime", "general", "clients", "profiles", "lists")
SIDEBAR_LIST_LIMIT = 5


def sidebar_status(
    data: Dict[str, Any], limit: Optional[int] = SIDEBAR_LIST_LIMIT
) -> Dict[str, Any]:
    """sidebarStatus: the general profile line, the affected devices and the service lists.

    `limit` is how many affected devices are listed by name. The page shows five and counts the
    rest. The terminal passes None to list all of them.
    """

    visible = bool(data.get("adminTokenVerified"))
    loaded = data.get("loaded") or {}
    if not all(loaded.get(name) is True for name in SIDEBAR_INPUTS):
        return {
            "visible": visible,
            "loading": True,
            "general": {"text": "Loading…", "tone": "muted"},
            "devices": {"summary": "", "items": [], "more": 0},
            "lists": {"text": ""},
        }
    state = data.get("runtimeState") or "unknown"
    running = data.get("running") is True
    general_state = data.get("generalState") or None
    general_profile = general_state.get("profile") if general_state else None
    general_profile = general_profile or None
    profiles = data.get("profiles") or []
    clients = data.get("clients") or []
    service_lists = data.get("serviceLists") or []
    steady = running and state in ("applied", "unknown")
    general_in_effect = (
        general_profile is not None
        and steady
        and (general_state.get("active") is True or state == "unknown")
    )
    general_name = (
        (general_profile.get("name") or general_profile.get("id")) if general_profile else ""
    )

    general = {"text": "General profile: none", "tone": "muted"}
    if general_profile:
        if general_in_effect:
            general = {"text": "General profile: " + general_name + " · active", "tone": "on"}
        else:
            general = {
                "text": "General profile: " + general_name + " · "
                + ("paused" if state == "reset" else "saved, not applied"),
                "tone": "muted",
            }

    def quiet(summary: str) -> Dict[str, Any]:
        return {"summary": summary, "items": [], "more": 0}

    if state == "reset":
        devices = quiet("Profiles off")
    elif state in ("applying", "resetting"):
        devices = quiet("Changing…")
    elif state == "error":
        devices = quiet(
            "Last reset failed. The Pi may still be shaping."
            if data.get("lastAction") == "reset"
            else "Last network action failed"
        )
    elif not running:
        devices = quiet("Not running")
    elif data.get("clientsAvailable") is not True:
        devices = quiet("Connected devices unavailable")
    else:
        def name_of(client: Dict[str, Any]) -> str:
            return client.get("display_name") or client.get("mac_address") or "Unknown device"

        def profile_name_of(profile_id: Any) -> str:
            match = next((item for item in profiles if item.get("id") == profile_id), None)
            if match is not None:
                return match.get("name") or profile_id
            return profile_id or ""

        if general_in_effect:
            affected = [
                {"name": name_of(client), "profile": general_name}
                for client in clients
                if not client.get("unaffected") and client.get("association_verified") is not False
            ]
        else:
            affected = [
                {
                    "name": name_of(client),
                    "profile": profile_name_of(
                        client.get("group_profile_id") or client.get("assigned_profile_id")
                    ),
                }
                for client in clients
                if not client.get("unaffected") and client.get("assigned_device_id")
            ]
        unassigned = (
            not general_in_effect
            and len(clients) > 0
            and not any(client.get("assigned_device_id") for client in clients)
        )
        if affected:
            summary = str(len(affected)) + (
                " device affected" if len(affected) == 1 else " devices affected"
            )
        elif unassigned:
            summary = "No device has its own profile"
        else:
            summary = "No connected device is affected"
        shown = affected if limit is None else affected[:limit]
        devices = {"summary": summary, "items": shown, "more": len(affected) - len(shown)}

    enabled_lists = (
        [item.get("name") for item in service_lists if item.get("enabled")] if steady else []
    )
    return {
        "visible": visible,
        "loading": False,
        "general": general,
        "devices": devices,
        "lists": {
            "text": "Service lists: " + " · ".join(enabled_lists) + " (every unprotected device)"
            if enabled_lists
            else ""
        },
    }


def runtime_status_text(
    state: Optional[str], running: bool, context: Optional[Dict[str, Any]] = None
) -> Dict[str, str]:
    """runtimeStatusText: the one sentence the page shows about the network state."""

    options = context or {}
    if options.get("helperConfigured") is False:
        return {"text": "Needs the Pi helper to run.", "tone": "muted"}
    if state == "applying":
        return {"text": "Applying…", "tone": "working"}
    if state == "resetting":
        return {"text": "Resetting…", "tone": "working"}
    if running is True:
        if state == "applied":
            return {
                "text": "Running. Your saved settings are applied. New changes apply on their own.",
                "tone": "ok",
            }
        if state == "unknown":
            return {"text": "Running. Your latest change applies on its own.", "tone": "ok"}
        if state == "error":
            return {"text": "The last apply failed. Check the Pi.", "tone": "error"}
    else:
        if state == "reset":
            return {"text": "Off. No traffic shaping is running.", "tone": "muted"}
        if state == "unknown":
            return {
                "text": "JitterSim is not running. Choose Start with saved settings.",
                "tone": "pending",
            }
        if state == "error":
            if options.get("lastAction") == "reset":
                return {
                    "text": "The last reset failed. The Pi may still be shaping. Check it.",
                    "tone": "error",
                }
            return {"text": "The last start failed. Check the Pi and try again.", "tone": "error"}
        if state == "applied":
            return {"text": "Applied. Waiting for the scheduler.", "tone": "muted"}
    return {"text": "State unknown.", "tone": "muted"}


# --- end: status



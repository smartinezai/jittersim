"""The dashboard's display rules in Python so the terminal says what the page says.

Each function here copies one function of the dashboard script in `api/server.py` and keeps
its name in the docstring. The page computes these in the browser so the rules cannot move
to one shared place without changing the page. `tests/unit/test_dashboard_rules_parity.py`
runs both versions on the same inputs and fails when they drift apart.
"""

from typing import Any, Dict, List, Optional, Sequence

from jittersim.remote_client import js_number, js_round

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
    # A device-only start runs its own devices. Everyone and the rest stay paused.
    device_only = data.get("deviceOnly") if isinstance(data.get("deviceOnly"), list) else []
    # While Everyone runs, these saved devices run their own profile beside it.
    everyone_exceptions = (
        data.get("everyoneExceptions") if isinstance(data.get("everyoneExceptions"), list) else []
    )
    general_in_effect = (
        general_profile is not None
        and steady
        and not device_only
        and (general_state.get("active") is True or state == "unknown")
    )
    general_name = (
        (general_profile.get("name") or general_profile.get("id")) if general_profile else ""
    )

    general = {"text": "Everyone: none", "tone": "muted"}
    if general_profile:
        if general_in_effect:
            general = {"text": "Everyone: " + general_name + " · active", "tone": "on"}
        else:
            general = {
                "text": "Everyone: " + general_name + " · "
                + ("paused" if state == "reset" or device_only else "saved, not applied"),
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
                {
                    "name": name_of(client),
                    "profile": profile_name_of(
                        client.get("group_profile_id") or client.get("assigned_profile_id")
                    )
                    if client.get("assigned_device_id")
                    and client.get("assigned_device_id") in everyone_exceptions
                    else general_name,
                }
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
                and client.get("assigned_effective_state") not in ("unverified", "protected")
                and (not device_only or client.get("assigned_device_id") in device_only)
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
        [item.get("name") for item in service_lists if item.get("enabled")]
        if steady and not device_only else []
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
    device_only = options.get("deviceOnly")
    count = len(device_only) if isinstance(device_only, list) else 0
    if running is True and count > 0 and state in ("applied", "unknown"):
        return {
            "text": "Running for " + str(count) + (" device" if count == 1 else " devices")
            + " only. Everyone and other saved settings stay paused. Start resumes them all.",
            "tone": "ok",
        }
    exceptions = options.get("everyoneExceptions")
    beside = len(exceptions) if isinstance(exceptions, list) else 0
    if running is True and beside > 0 and state in ("applied", "unknown"):
        return {
            "text": "Running. Everyone covers all devices except " + str(beside)
            + (" device that runs its own profile" if beside == 1
               else " devices that run their own profile")
            + ". Start puts them back on Everyone.",
            "tone": "ok",
        }
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
                "text": "Nothing is running. Choose Start.",
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


# --- section: profiles


def _positive(value: Any) -> bool:
    """JavaScript's `value > 0` for a field that may be missing or null."""

    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def general_badge(general_state: Optional[Dict[str, Any]]) -> str:
    """The badge of the general profile in renderSavedProfiles."""

    general = general_state.get("profile") if general_state else None
    if general and general_state.get("active"):
        return "General · active"
    observed = ((general_state or {}).get("state") or {}).get("observed_state")
    return "General · paused" if observed == "reset" else "General · saved"


def profile_direction_summary(direction: Dict[str, Any]) -> str:
    """profileDirectionSummary."""

    parts: List[str] = []
    if _positive(direction.get("delay_ms")) or _positive(direction.get("jitter_ms")):
        jitter = direction.get("jitter_ms")
        parts.append(
            js_number(direction.get("delay_ms")) + " ms"
            + (" ±" + js_number(jitter) if _positive(jitter) else "")
        )
    if _positive(direction.get("loss_percent")):
        parts.append(js_number(direction["loss_percent"]) + "% loss")
    bandwidth = direction.get("bandwidth_kbit")
    if bandwidth:
        parts.append(
            js_number(js_round(bandwidth / 10) / 100) + " Mbit/s"
            if bandwidth >= 1000
            else js_number(bandwidth) + " kbit/s"
        )
    for field, word in (
        ("reorder_percent", "reorder"),
        ("duplicate_percent", "duplicate"),
        ("corrupt_percent", "corrupt"),
    ):
        if _positive(direction.get(field)):
            parts.append(word + " " + js_number(direction[field]) + "%")
    return " · ".join(parts) if parts else "no impairment"


def profile_summary(profile: Dict[str, Any]) -> str:
    """profileSummary: the one line under a saved profile."""

    upload = profile_direction_summary(profile.get("upload") or {})
    download = profile_direction_summary(profile.get("download") or {})
    parts = ["↕ " + upload if upload == download else "↑ " + upload + " · ↓ " + download]
    if _positive(profile.get("outage_probability")):
        parts.append("outages " + js_number(profile["outage_probability"]) + "%")
    if profile.get("website_mode") == "allow_only":
        sites = len(profile.get("website_domains") or [])
        parts.append("allow-only " + str(sites) + (" site" if sites == 1 else " sites"))
    return " · ".join(parts)


# --- end: profiles


# --- section: traffic


def device_display_name(device: Dict[str, Any]) -> str:
    """deviceDisplayName."""

    return (
        device.get("display_name")
        or device.get("name")
        or device.get("assigned_device_name")
        or device.get("ipv4_address")
        or "Unknown device"
    )


def query_count_text(count: Any) -> str:
    """queryCountText."""

    return js_number(count) + (" query" if count == 1 else " queries")


def group_names_text(count: int) -> str:
    """groupNamesText."""

    return str(count) + (" name" if count == 1 else " names")


def is_risky_group_kind(kind: Any) -> bool:
    """isRiskyGroupKind."""

    return kind in ("system", "cdn")


def _number(value: Any) -> float:
    """JavaScript's `Number(value) || 0` for a query count."""

    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value if value == value else 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return number if number == number else 0


def group_observations(domains: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """groupObservations: one entry per app or registrable domain, busiest first."""

    groups: Dict[str, Dict[str, Any]] = {}
    for observation in domains or []:
        group = observation.get("group") or {
            "key": "domain:" + str(observation.get("domain")),
            "name": observation.get("domain"),
        }
        key = group.get("key")
        if key not in groups:
            groups[key] = {
                "key": key,
                "name": group.get("name"),
                "kind": group.get("kind") or "",
                "names": [],
                "totalQueries": 0,
                "single": True,
            }
        entry = groups[key]
        if not is_risky_group_kind(entry["kind"]) and is_risky_group_kind(group.get("kind")):
            entry["kind"] = group.get("kind")
        entry["names"].append(observation)
        entry["totalQueries"] += _number(observation.get("query_count"))
        entry["single"] = len(entry["names"]) == 1
    return sorted(
        groups.values(),
        key=lambda item: (-item["totalQueries"], str(item["name"]).lower()),
    )


def domain_hint(observation: Dict[str, Any]) -> Dict[str, Any]:
    """domainHint: the seen-with guess and whether a web search is offered."""

    label = observation.get("label") or {}
    apps = observation.get("seen_with") or []
    return {
        "text": "Seen with " + " and ".join(apps) + " (guess)" if apps else "",
        "searchable": label.get("kind") in ("unknown", "cdn"),
    }


def group_blocked_text(group: Dict[str, Any]) -> str:
    """groupBlockedText."""

    blocked = sum(
        1 for observation in group["names"] if observation.get("globally_blocked") is True
    )
    if blocked == 0:
        return ""
    return ("All on global block list" if blocked == len(group["names"])
            else str(blocked) + " on global block list")


def group_categories_uniform(group: Dict[str, Any]) -> bool:
    """groupCategoriesUniform."""

    def category(observation: Dict[str, Any]) -> str:
        return (observation.get("label") or {}).get("category") or ""

    names = group["names"]
    return all(category(observation) == category(names[0]) for observation in names)


def row_label_parts(
    observation: Dict[str, Any], group_name: str = "", categories_uniform: bool = False
) -> Dict[str, Any]:
    """domainRowParts and rowLabelParts: what one name's label line shows."""

    label = observation.get("label") or {}
    label_name = label.get("name") or ""
    category = label.get("category") or ""
    repeated = bool(group_name) and label_name == group_name
    return {
        "labelName": label_name,
        "category": category,
        "kind": label.get("kind") or "",
        "showName": bool(label_name) and not repeated,
        "showCategory": bool(category) and not (repeated and categories_uniform),
    }


# --- end: traffic

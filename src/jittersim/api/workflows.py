"""Authenticated dashboard workflows separate from ordinary configuration routes."""

import json
import re
import shutil
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from jittersim.config.storage import StorageError
from jittersim.config.transfer import check_settings, keep_sections_missing_from
from jittersim.config.workflows import WorkflowStore, session_report, settings_snapshot
from jittersim.engine.compiler import (
    MAX_SERVICE_ASSIGNMENT_DOMAINS_PER_PLAN,
    MAX_SHAPED_MARKS_PER_PLAN,
)
from jittersim.engine.timeline import device_timeline
from jittersim.networking.helper import HelperClientError

WORKFLOW_WRITES = frozenset(
    (
        "/api/history/preview",
        "/api/history/restore",
        "/api/test-sessions/start",
        "/api/test-sessions/save",
        "/api/update-preferences",
        "/api/updates/install",
        "/api/updates/rollback",
        "/api/updates/retry",
        "/api/updates/check-online",
        "/api/updates/online-settings",
    )
)
ONLINE_JOB = re.compile(r"[0-9a-f]{16}\Z")
UPDATE_RETRY = re.compile(r"[0-9a-f]{64}\Z")


def online_unavailable():
    return response(
        {"state": "unavailable", "message": "Online checks need the Pi network helper and the "
         "release service."}, 503,
    )


def response(value, status=200):
    return status, "application/json", (json.dumps(value, allow_nan=False) + "\n").encode()


def machine_health():
    try:
        installed = version("jittersim")
    except PackageNotFoundError:
        installed = "development"
    result = {"version": installed, "checked_at": int(time.time())}
    try:
        result["disk_free_bytes"] = shutil.disk_usage(Path.cwd()).free
    except OSError:
        result["disk_free_bytes"] = None
    try:
        result["temperature_celsius"] = (
            int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
        )
    except (OSError, ValueError):
        result["temperature_celsius"] = None
    try:
        result["uptime_seconds"] = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        result["uptime_seconds"] = None
    return result


def read_workflow(server, path):
    store = WorkflowStore(server.repository)
    if path == "/api/update-preferences":
        try:
            return response(
                {
                    "check_staged_updates_on_open": (
                        server.repository.get_check_staged_updates_on_open()
                    )
                }
            )
        except StorageError:
            return response({"error": "dashboard update preference is unavailable"}, 503)
    if path.startswith("/api/device-workspace/"):
        device_id = path.removeprefix("/api/device-workspace/")
        device = server.repository.get_device(device_id)
        if device is None:
            return response({"error": "device no longer exists"}, 404)
        now = time.time()
        cache = getattr(server, "_workspace_timelines", {})
        key = (
            device_id,
            int(now // 30),
            server.repository.get_runtime_state().configuration_version,
            tuple(
                (item.device_id, item.enabled and item.used_bytes >= item.limit_bytes)
                for item in server.repository.list_data_limits()
            ),
            tuple(
                (item.group_id, item.enabled and item.used_bytes >= item.limit_bytes)
                for item in server.repository.list_group_data_limits()
            ),
        )
        if key not in cache:
            if len(cache) >= 16:
                cache.clear()
            cache[key] = device_timeline(server.repository, device, now=now)
            server._workspace_timelines = cache
        result = {"timeline": cache[key], "checked_at": now}
        group = next(
            (
                item
                for item in server.repository.list_device_groups()
                if device_id in item.device_ids
            ),
            None,
        )
        for route, field in (
            ("timers", "timers"),
            ("progressions", "progressions"),
            ("data-limits", "data_limits"),
            ("service-quotas", "service_quotas"),
            ("time-quotas", "time_quotas"),
            ("group-data-limits", "group_data_limits"),
        ):
            data = json.loads(server.response_for("/api/" + route)[2])
            result[field] = [
                item
                for item in data[field]
                if item.get("device_id") == device_id
                or group is not None
                and item.get("group_id") == group.id
            ]
        result["runs"] = [
            item
            for item in json.loads(server.response_for("/api/scenarios")[2])["runs"]
            if item.get("device_id") == device_id
            or group is not None
            and item.get("group_id") == group.id
        ]
        return response(result)
    if path == "/api/updates":
        if server.helper_client is None:
            return response(
                {
                    "phase": "unavailable",
                    "message": "Release controls require the Pi network helper.",
                }
            )
        try:
            return response(server.helper_client.release_status())
        except (HelperClientError, AttributeError):
            return response(
                {
                    "phase": "unavailable",
                    "message": "Install the release service on the Pi to enable update controls.",
                }
            )
    if path == "/api/updates/check-online" or path.startswith("/api/updates/check-online/"):
        job = path.removeprefix("/api/updates/check-online").lstrip("/") or None
        if job is not None and not ONLINE_JOB.fullmatch(job):
            return response({"error": "unknown check"}, 404)
        if server.helper_client is None:
            return online_unavailable()
        try:
            return response(server.helper_client.online_status(job))
        except (HelperClientError, AttributeError):
            return online_unavailable()
    if path == "/api/updates/online-settings":
        if server.helper_client is None:
            return online_unavailable()
        try:
            return response(server.helper_client.online_settings())
        except (HelperClientError, AttributeError):
            return online_unavailable()
    if path == "/api/history":
        return response({"versions": store.history(), "warning": store.history_warning()})
    if path == "/api/test-sessions":
        return response({"sessions": store.sessions()})
    if path.startswith("/api/test-sessions/"):
        identifier = path.removeprefix("/api/test-sessions/")
        try:
            record = store.session(identifier)
            return response({"session": record, "report": session_report(record)})
        except ValueError as error:
            return response({"error": str(error)}, 404)
    return None


def write_workflow(server, path, payload, parse_settings):
    if path not in WORKFLOW_WRITES:
        return None
    store = WorkflowStore(server.repository)
    try:
        if path == "/api/update-preferences":
            enabled = payload.get("check_staged_updates_on_open")
            if not isinstance(enabled, bool):
                raise ValueError("check_staged_updates_on_open must be true or false")
            server.repository.set_check_staged_updates_on_open(enabled)
            return response({"check_staged_updates_on_open": enabled, "saved": True})
        if path in ("/api/updates/install", "/api/updates/rollback"):
            if payload.get("confirm") is not True:
                raise ValueError("confirm the release operation first")
            if server.helper_client is None:
                return response({"error": "release controls require the Pi network helper"}, 503)
            rollback = path.endswith("rollback")
            release = payload.get("version", "")
            if not isinstance(release, str) or len(release) > 64:
                raise ValueError("release version is invalid")
            return response(
                server.helper_client.install_release(version=release, rollback=rollback), 202
            )
        if path == "/api/updates/retry":
            retry_id = payload.get("retry_id")
            if (set(payload) != {"retry_id"} or not isinstance(retry_id, str)
                    or UPDATE_RETRY.fullmatch(retry_id) is None):
                raise ValueError("choose the current failed update to retry")
            unavailable = {"error": "update retry is unavailable"}
            client = server.helper_client
            retry = getattr(client, "retry_update", None)
            if not callable(retry):
                return response(unavailable, 503)
            try:
                status = retry(retry_id=retry_id)
            except HelperClientError as error:
                if error.reason in {"helper-busy", "retry-refused", "update-retry-refused"}:
                    return response({"error": "the update cannot be retried right now"}, 409)
                return response(unavailable, 503)
            except AttributeError:
                return response(unavailable, 503)
            if (not isinstance(status, dict)
                    or not isinstance(status.get("request_id"), str)
                    or re.fullmatch(r"[0-9a-f]{32}", status["request_id"]) is None
                    or status.get("request_kind") not in ("install", "rollback")):
                return response(unavailable, 503)
            return response({"status": status}, 202)
        if path == "/api/updates/check-online":
            force = payload.get("force", False)
            if not isinstance(force, bool) or set(payload) - {"force"}:
                raise ValueError("force must be true or false")
            if server.helper_client is None:
                return online_unavailable()
            try:
                # Returns at once with a job. The root fetch unit does the work.
                return response(server.helper_client.online_check(force=force), 202)
            except (HelperClientError, AttributeError) as error:
                return response({"state": "unavailable", "message": str(error)}, 503)
        if path == "/api/updates/online-settings":
            changes = {name: payload[name] for name in ("daily_check", "install_automatically")
                       if name in payload}
            if (not changes or set(payload) - {"daily_check", "install_automatically", "confirm"}
                    or not all(isinstance(value, bool) for value in changes.values())):
                raise ValueError("daily_check and install_automatically must be true or false")
            if changes.get("install_automatically") is True and payload.get("confirm") is not True:
                raise ValueError("confirm that releases may install without a button press")
            if server.helper_client is None:
                return online_unavailable()
            try:
                return response(server.helper_client.online_settings(changes))
            except (HelperClientError, AttributeError) as error:
                return response({"state": "unavailable", "message": str(error)}, 503)
        if path == "/api/test-sessions/start":
            session = store.start_session(payload)
            server._record_activity("apply", "Test session started: " + session["name"])
            return response({"session": store.session(session["id"])})
        if path == "/api/test-sessions/save":
            return response({"session": store.edit_session(payload)})
        if path in ("/api/history/preview", "/api/history/restore"):
            identifier = payload.get("id")
            if not isinstance(identifier, int) or isinstance(identifier, bool):
                raise ValueError("choose a saved settings version")
            previous = store.history_item(identifier)
            if path.endswith("preview"):
                current = settings_snapshot(server.repository)
                changes = [key for key in current if current.get(key) != previous.get(key)]
                return response(
                    {
                        "settings": previous,
                        "changed_sections": changes,
                        "current_version": server.repository.get_runtime_state().configuration_version,
                        "message": "Restoring stops temporary runs. Usage for matching limits and activity are retained.",
                    }
                )
            expected = payload.get("current_version")
            if not isinstance(expected, int) or isinstance(expected, bool):
                raise ValueError("preview the settings before restoring")
            settings = keep_sections_missing_from(
                previous, parse_settings(previous), server.repository.saved_settings()
            )
            check_settings(
                settings,
                max_traffic_classes=MAX_SHAPED_MARKS_PER_PLAN,
                max_service_domains=MAX_SERVICE_ASSIGNMENT_DOMAINS_PER_PLAN,
            )
            if server.scheduler:
                server.scheduler.sample_usage(repository=server.repository)
            server.repository.replace_settings(
                settings,
                expected_version=expected,
                preserve_usage=True,
                observation_addresses=server.newly_protected_station_addresses(settings),
            )
            server._record_activity("reset", "Saved settings restored. Temporary runs stopped.")
            return server._apply_saved_change("restored", {"id": identifier})
    except (ValueError, TypeError, KeyError) as error:
        return response({"error": str(error)}, 400)
    except StorageError as error:
        return response({"error": str(error)}, 409)
    except HelperClientError:
        return response({"error": "the helper could not complete this operation"}, 502)
    return None

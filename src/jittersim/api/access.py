"""Authenticated access-rule operations used by the control server."""

import json
import sqlite3
import time
from dataclasses import replace

from jittersim.diagnostic_records import recorded
from jittersim.config.models import MacPolicy, SiteBlock, normalize_mac_address
from jittersim.config.storage import DeviceConflictError, RepositoryBusy, StorageError
from jittersim.engine.access import build_access_plan
from jittersim.networking.helper import HelperClientError

ACCESS_WRITES = frozenset(
    (
        "/api/access/apply",
        "/api/access/disable",
        "/api/access/device",
        "/api/site-blocks",
        "/api/site-blocks/delete",
        "/api/normal-access",
    )
)


# A database backup or slow commit holds the repository lock (review N3). Panic's
# bookkeeping waits for it at most this long per step and then goes on without it:
# the helper still stops everything and the answer says the stop was not saved.
# The steps are the version read before the route, the stop intent, the saved
# result (PANIC_RESULT_WAIT_SECONDS), the activity record, the state in the answer,
# the history checkpoint and the action record. Together they stay inside
# PANIC_DATABASE_ALLOWANCE_SECONDS (api/server.py).
PANIC_REPOSITORY_WAIT_SECONDS = 0.5
PANIC_RESULT_WAIT_SECONDS = 1.5
PANIC_REPOSITORY_STEPS = 6


def response(value, status=200):
    return status, "application/json", (json.dumps(value, allow_nan=False) + "\n").encode()


def _definition(plan):
    value = plan.payload()
    # The helper follows live leases even while the experiment is stopped.
    for client in value["clients"]:
        client.pop("address")
    return value


def read_access_api(server, path):
    if path != "/api/access":
        return None
    from jittersim.engine.access import access_from_payload

    plan = build_access_plan(server.repository.access_settings())
    result = {
        "available": False,
        "wifi_admission_available": False,
        "applied": False,
        "saved_policy": plan.payload(),
        "version": server.repository.get_runtime_state().configuration_version,
    }
    if server.helper_client is not None and hasattr(server.helper_client, "access_status"):
        try:
            result.update(server.helper_client.access_status())
            if result.get("policy") is not None:
                applied = access_from_payload(result["policy"])
                result["applied"] = _definition(plan) == _definition(applied) and not result.get(
                    "error"
                )
        except (HelperClientError, ValueError):
            return response(
                {
                    "error": (
                        "Access status could not be read. The last applied rules remain in place."
                    )
                },
                503,
            )
    runtime = server.repository.get_runtime_state()
    result["normal_access"] = bool(
        result.get("policy")
        and not result.get("enabled")
        and not result.get("error")
        and runtime.observed_state == "reset"
        and runtime.last_action == "reset"
    )
    return response(result)


@recorded("panic", "dashboard")
def normal_access(server):
    repository = server.repository
    if server.helper_client is None or not hasattr(server.helper_client, "normal_access"):
        return response(
            {"error": "A current networking helper is required to restore normal access."}, 503
        )
    ticket = None
    if server.scheduler is not None:
        ticket = server.scheduler.begin_recovery()
    persisted = True
    # A backup or slow commit holds the repository (review N3). The stop intent is
    # then saved after the helper call instead of before it: the scheduler fence of
    # this recovery already keeps automation from applying anything meanwhile.
    deferred = False

    def save_stop_intent():
        settings = repository.access_settings()
        repository.save_access_policies(
            settings.mac_policy, settings.website_policy,
            options=replace(settings.access_options, enabled=False),
        )

    def prepare():
        nonlocal persisted, deferred
        try:
            with server.bounded_repository(PANIC_REPOSITORY_WAIT_SECONDS):
                save_stop_intent()
                repository.update_runtime_state(
                    observed_state="resetting", last_action="reset", last_result=None, last_error=None
                )
        except RepositoryBusy:
            deferred = True
        except (StorageError, sqlite3.Error):
            persisted = False

    try:
        if ticket:
            server.scheduler.publish_recovery(ticket, prepare)
        else:
            prepare()
    except BaseException:
        if ticket:
            server.scheduler.abandon_recovery(ticket)
        raise
    error = None
    try:
        result = server.helper_client.normal_access()
    except HelperClientError:
        result = None
        error = "Normal access was not fully restored. Automation is paused. Retry the panic button."
    except BaseException:
        if ticket:
            server.scheduler.abandon_recovery(ticket)
        raise

    def publish(overlap_failed=False):
        nonlocal persisted, error
        if overlap_failed and error is None:
            error = "Another Panic cleanup failed during this recovery. Retry Panic before choosing Start."
        if not persisted and error is None:
            error = "Traffic restrictions are off but the stopped state could not be saved. Retry before restarting."
        state = None
        try:
            with server.bounded_repository(PANIC_RESULT_WAIT_SECONDS):
                if deferred:
                    save_stop_intent()
                state = repository.update_runtime_state(
                    observed_state="error" if error else "reset", last_action="reset",
                    last_result="failure" if error else "success", last_error=error,
                )
        except (StorageError, sqlite3.Error):
            # RepositoryBusy too: the stop is not saved, so recovery stays unconfirmed.
            persisted = False
        if not persisted and error is None:
            error = "Traffic restrictions are off but the stopped state could not be saved. Retry before restarting."
        return state, error is None

    state = server.scheduler.finish_recovery(ticket, publish, failed=error is not None or not persisted) if ticket else publish()[0]
    try:
        with server.bounded_repository(PANIC_REPOSITORY_WAIT_SECONDS):
            server._record_activity(
                "access",
                error or "Normal access restored. All profiles and household restrictions are off.",
            )
    except (StorageError, sqlite3.Error):
        pass
    if error:
        return response({"error": error}, 503)
    body = {"normal_access": True, "result": result}
    if state is not None:
        # The sidebar Panic button shows this like the answer to a Reset.
        try:
            with server.bounded_repository(PANIC_REPOSITORY_WAIT_SECONDS):
                body["state"] = server._runtime_state_with_running(state)
        except (StorageError, sqlite3.Error):
            pass
    return response(body)


def write_access_api(server, path, payload, *, control_intent=None):
    if path not in ACCESS_WRITES:
        return None
    if path == "/api/normal-access":
        return normal_access(server)
    generation = getattr(server.scheduler, "apply_generation", 0)
    control_revision = None
    if control_intent is not None:
        generation, control_revision = control_intent
        refused = server._apply_refused(generation)
    elif path in ("/api/site-blocks", "/api/site-blocks/delete"):
        refused = server._apply_refused(generation)
    else:
        refused, control_revision = server._capture_control_intent(generation)
    if refused is not None:
        return refused
    repository = server.repository
    try:
        version = payload.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError("reload access rules before saving")
        if version != repository.get_runtime_state().configuration_version:
            return response({"error": "Settings changed. Reload access rules before saving."}, 409)
        settings = repository.access_settings()
        mac = settings.mac_policy
        options = settings.access_options
        blocks = list(settings.site_blocks)
        if path in ("/api/site-blocks", "/api/site-blocks/delete"):
            if path.endswith("/delete"):
                if not any(rule.id == payload.get("id") for rule in blocks):
                    return response({"error": "site rule no longer exists"}, 404)
                blocks = [rule for rule in blocks if rule.id != payload["id"]]
            else:
                rule = SiteBlock(**payload["rule"])
                blocks = [existing for existing in blocks if existing.id != rule.id] + [rule]
            accepted, _ = server._publish_apply(generation, lambda: repository.save_access_policies(
                mac, settings.website_policy, blocks=blocks, expected_version=version
            ))
            if not accepted:
                return server._apply_refused(generation)
            repository.record_activity(
                kind="access", message="Site access rules changed.", now=time.time()
            )
            return response(
                {"saved": True, "version": repository.get_runtime_state().configuration_version}
            )
        if server.helper_client is None or not hasattr(server.helper_client, "apply_access"):
            return response(
                {"error": "A current networking helper is required to apply access rules."}, 503
            )
        if path == "/api/access/disable":
            if payload.get("confirmed") is not True:
                raise ValueError("confirm that saved household restrictions will be disabled")
            options = replace(options, enabled=False)
        if path == "/api/access/device":
            address = normalize_mac_address(payload.get("mac_address"))
            blocked = payload.get("blocked")
            if not isinstance(blocked, bool):
                raise ValueError("blocked must be true or false")
            if not settings.access_options.enabled or mac.mode == "disabled":
                # A quick action must never reactivate a dormant allow/block list.
                if not blocked:
                    return response({"error": "Device access is already unrestricted."}, 409)
                if not settings.access_options.enabled:
                    return response(
                        {
                            "error": (
                                "Enable and apply household access rules before blocking a device."
                            )
                        },
                        409,
                    )
                mac = MacPolicy()
            if mac.mode == "allowlist":
                allowed = set(mac.allowlist)
                allowed.discard(address) if blocked else allowed.add(address)
                mac = replace(mac, allowlist=tuple(sorted(allowed)))
            else:
                denied = set(mac.denylist)
                denied.add(address) if blocked else denied.discard(address)
                mac = MacPolicy("denylist", mac.allowlist, tuple(sorted(denied)))
            options = replace(options, enabled=True)
        if options != settings.access_options or mac != settings.mac_policy:
            accepted, _ = server._publish_apply(generation, lambda: repository.save_access_policies(
                mac, settings.website_policy, options=options, expected_version=version
            ))
            if not accepted:
                return server._apply_refused(generation)
        plan = build_access_plan(repository.access_settings())
        refused = server._apply_refused(generation)
        if refused is not None:
            return refused
        arguments = {"control_revision": control_revision} if control_revision is not None else {}
        result = server.helper_client.apply_access(plan, **arguments)
        refused = server._apply_refused(generation, undo=True)
        if refused is not None:
            return refused
        accepted, _ = server._publish_apply(generation, lambda: repository.record_activity(
            kind="access",
            message=("Household access rules applied." if plan.options.enabled
                     else "Household access rules disabled."),
            now=time.time(),
        ))
        if not accepted:
            return server._apply_refused(generation, undo=True)
        return response(
            {"result": result, "version": repository.get_runtime_state().configuration_version}
        )
    except DeviceConflictError as error:
        return response({"error": str(error)}, 409)
    except (KeyError, TypeError, ValueError) as error:
        return response({"error": str(error)}, 400)
    except HelperClientError as error:
        rejected = server._control_rejected(error)
        if rejected is not None:
            return rejected
        refused = server._apply_refused(generation, undo=True)
        if refused is not None:
            return refused
        return response(
            {"error": "Access update failed. Check access status before retrying."}, 503
        )

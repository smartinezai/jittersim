"""Independent household choices and external Panic retain their own intent."""

import os
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jittersim.api.server import ControlServer
import pytest

from jittersim.engine.service import compile_stored_plan
from jittersim.config.storage import ConfigRepository
from jittersim.engine.access import AccessPlan
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.panic_guard import PanicGuard
from tests.unit.test_panic_intent_generation import WaitingLock
from tests.unit.test_panic_restart_latch import helper


def provision(persistent, directory):
    persistent.provision_fresh(
        witnesses=(directory / "prior-install",), update_root=directory / "updates"
    )


def test_fresh_household_choice_survives_reconstruction_without_starting_profiles():
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        persistent = PanicGuard(directory / "control" / "panic-state.json", owner_uid=os.getuid())
        provision(persistent, directory)
        with helper(directory, panic_guard=persistent) as (_, client):
            client.normal_access()
            saved = AccessPlan()
            client.apply_access(replace(saved, options=replace(saved.options, enabled=True)))
        with helper(directory, panic_guard=persistent) as (service, client):
            assert client.gateway_status()["panic_stop_latched"] is True
            assert service._plan_count == 0
            assert service._access_manager.applied.options.enabled is True


def test_start_waiting_before_external_helper_panic_cannot_capture_a_new_intent_afterward():
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        database = directory / "settings.sqlite"
        with helper(directory) as (service, client), ConfigRepository(database) as repository:
            scheduler = RuntimeScheduler(database_path=database, helper_client=client)
            server = ControlServer(
                ("127.0.0.1", 0), repository, helper_client=client, scheduler=scheduler
            )
            waiting = threading.Event()
            result = []
            original = scheduler._apply_lock
            original.acquire()
            worker = None
            try:
                with (
                    patch.object(scheduler, "_apply_lock", WaitingLock(original, waiting)),
                    patch.object(scheduler, "start"),
                ):
                    worker = threading.Thread(
                        target=lambda: result.append(server.write_response_for("/api/apply", {}))
                    )
                    worker.start()
                    assert waiting.wait(2)
                    assert client.normal_access()["ok"] is True
                    original.release()
                    worker.join(3)
                    assert not worker.is_alive()
                    assert result[0][0] == 409
                    assert service._plan_count == 0
                    assert not scheduler.is_active
            finally:
                if worker is not None and worker.is_alive():
                    original.release()
                    worker.join(3)
                server.server_close()


@pytest.mark.parametrize("path", ["/api/access/apply", "/api/observations/add-global-block", "/api/observations/remove-global-block"])
def test_queued_household_action_is_canceled_and_profile_start_cannot_restore_it(path):
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        persistent = PanicGuard(directory / "control" / "panic-state.json", owner_uid=os.getuid())
        provision(persistent, directory)
        database = directory / "settings.sqlite"
        with (
            helper(directory, panic_guard=persistent) as (service, client),
            ConfigRepository(database) as repository,
        ):
            settings = repository.access_settings()
            repository.save_access_policies(
                settings.mac_policy,
                settings.website_policy,
                options=replace(settings.access_options, enabled=True),
            )
            scheduler = RuntimeScheduler(database_path=database, helper_client=client)
            server = ControlServer(
                ("127.0.0.1", 0), repository, helper_client=client, scheduler=scheduler
            )
            waiting = threading.Event()
            result = []
            original = scheduler._apply_lock
            original.acquire()
            worker = None
            try:
                with (
                    patch.object(scheduler, "_apply_lock", WaitingLock(original, waiting)),
                    patch.object(scheduler, "start"),
                ):
                    payload = {"version": repository.get_runtime_state().configuration_version, "domain": "video.example.com"}
                    worker = threading.Thread(
                        target=lambda: result.append(
                            server.write_response_for(path, payload)
                        )
                    )
                    worker.start()
                    assert waiting.wait(2)
                    assert client.normal_access()["ok"] is True
                    original.release()
                    worker.join(3)
                    assert not worker.is_alive()
                    assert result[0][0] == 409
                    assert not service._access_manager.applied.options.enabled
                    if path != "/api/access/apply":
                        return
                    # A rejected external write requires a new dashboard Panic.
                    assert server.write_response_for("/api/apply", {})[0] == 503
                    # Root Start carries a fresh revision independently. The saved
                    # household definition remains enabled but cannot reactivate it.
                    plan = compile_stored_plan(repository=repository, connected_snapshot={})
                    client.apply(plan=plan, explicit_apply=True)
                    assert not service._access_manager.applied.options.enabled
                    payload["version"] = repository.get_runtime_state().configuration_version
                    client.apply_access(plan.access)
                    assert service._access_manager.applied.options.enabled
            finally:
                if worker is not None and worker.is_alive():
                    original.release()
                    worker.join(3)
                server.server_close()
        with helper(directory, panic_guard=persistent) as (service, _):
            assert service._access_manager.applied.options.enabled


@pytest.mark.parametrize("failure", ["guard", "gateway"])
def test_failed_household_publication_undoes_mutation_and_blocks_old_intent(failure):
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        persistent = PanicGuard(directory / "control" / "panic-state.json", owner_uid=os.getuid())
        provision(persistent, directory)
        with helper(directory, panic_guard=persistent) as (service, client):
            assert client.normal_access()["ok"]
            before = client.control_revision()
            saved = AccessPlan()
            enabled = replace(saved, options=replace(saved.options, enabled=True))
            target = persistent if failure == "guard" else service
            method = "write" if failure == "guard" else "_write_gateway_state"
            side_effect = OSError("full") if failure == "guard" else None
            with patch.object(target, method, side_effect=side_effect, return_value=False):
                from jittersim.networking.helper import HelperClientError

                with pytest.raises(HelperClientError, match="recovery state could not be saved"):
                    client.apply_access(enabled, control_revision=before)
            status = client.gateway_status()
            assert status["panic_stop_latched"] and status["household_stop_latched"]
            assert status["panic_recovery_failed"] and status["control_revision_required"]
            assert status["control_revision"] != before
            assert not service._access_manager.applied.options.enabled
            assert service._active_plan is None
            assert client.normal_access()["ok"]
            client.apply_access(enabled)
            assert not persistent.read()["household_stop_latched"]
            assert persistent.read()["panic_stop_latched"]

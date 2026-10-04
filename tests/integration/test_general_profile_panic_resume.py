"""Deliberate general-profile starts retain their original emergency intent."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.models import Device, Profile
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking.helper import HelperClientError
from jittersim.panic_guard import PanicGuard
from tests.unit.test_panic_restart_latch import helper as serving_helper
from tests.integration import test_general_profile as general_cases

control = general_cases.control
write = general_cases.write


def test_apply_to_all_is_a_deliberate_start_after_panic(control):
    repo, helper, _, server = control
    assert write(server, "/api/reset", {})[0] == 200
    original = helper.apply

    def latched_apply(*, plan, explicit_apply=False):
        if not explicit_apply:
            raise HelperClientError("Panic stopped profiles")
        return original(plan=plan, explicit_apply=explicit_apply)

    with patch.object(helper, "apply", side_effect=latched_apply):
        status, result = write(server, "/api/general-profile", {"profile_id": "slow"})
    assert status == 200 and result["general_profile"]["active"]
    assert repo.get_general_profile().id == "slow"


@pytest.mark.parametrize("stage", ["capability", "usage"])
def test_panic_during_preparation_refuses_the_selection(control, stage):
    repo, helper, scheduler, server = control
    target, method = (
        (helper, "check_general_profile_support")
        if stage == "capability"
        else (scheduler, "sample_before_control_change")
    )
    with patch.object(target, method, side_effect=lambda **unused: scheduler.halt()):
        status, _ = write(server, "/api/general-profile", {"profile_id": "slow"})
    assert status == 409
    assert repo.get_general_profile() is None and helper.plans == []


def test_external_helper_panic_before_save_refuses_the_selection(control):
    repo, helper, _, server = control
    with patch.object(helper, "control_revision", side_effect=["a" * 32, "b" * 32], create=True):
        status, _ = write(server, "/api/general-profile", {"profile_id": "slow"})
    assert status == 409
    assert repo.get_general_profile() is None and helper.plans == []


def test_an_older_helper_refuses_before_saving(control):
    repo, helper, _, server = control
    with patch.object(
        helper,
        "control_revision",
        side_effect=HelperClientError("Update the Pi helper"),
        create=True,
    ):
        status, _ = write(server, "/api/general-profile", {"profile_id": "slow"})
    assert status == 503
    assert repo.get_general_profile() is None and helper.plans == []


def test_stop_is_never_an_explicit_start(control):
    repo, helper, _, server = control
    assert write(server, "/api/general-profile", {"profile_id": "slow"})[0] == 200
    with patch.object(helper, "apply", wraps=helper.apply) as apply:
        assert write(server, "/api/general-profile/stop", {})[0] == 200
    assert apply.call_args.kwargs.get("explicit_apply", False) is False
    assert repo.get_general_profile() is None


def test_real_helper_resume_and_stop_preserve_persistent_panic():
    with tempfile.TemporaryDirectory(prefix="js-general-", dir="/tmp") as folder:
        directory = Path(folder)
        guard = PanicGuard(directory / "control/panic-state.json", owner_uid=os.getuid())
        guard.provision_fresh(
            witnesses=(directory / "prior-install",), update_root=directory / "updates"
        )
        with (
            serving_helper(directory, panic_guard=guard) as (service, client),
            ConfigRepository(directory / "settings.db") as repo,
        ):
            repo.save_profile(Profile("normal", "Normal"))
            repo.save_device(
                Device("phone", "Phone", "192.168.50.10", "normal", "02:00:00:00:00:10")
            )
            scheduler = RuntimeScheduler(
                database_path=directory / "settings.db", helper_client=client
            )
            server = ControlServer(
                ("127.0.0.1", 0), repo, helper_client=client, scheduler=scheduler
            )
            try:
                with patch.object(scheduler, "start"):
                    assert write(server, "/api/normal-access", {})[0] == 200
                    assert client.gateway_status()["panic_stop_latched"] is True
                    assert write(server, "/api/general-profile", {"profile_id": "normal"})[0] == 200
                    assert client.gateway_status()["panic_stop_latched"] is False
                    assert write(server, "/api/normal-access", {})[0] == 200
                    count = service._plan_count
                    # Stop saves the removal but never starts the retained device profile.
                    assert write(server, "/api/general-profile/stop", {})[0] == 502
                    assert service._plan_count == count
                    assert client.gateway_status()["panic_stop_latched"] is True
                    assert guard.read()["panic_stop_latched"] is True
                    assert repo.get_general_profile() is None
                    assert repo.get_device("phone").profile_id == "normal"
            finally:
                server.server_close()
                scheduler.stop()


def test_real_helper_panic_at_save_refuses_original_apply_intent():
    with tempfile.TemporaryDirectory(prefix="js-general-", dir="/tmp") as folder:
        directory = Path(folder)
        guard = PanicGuard(directory / "control/panic-state.json", owner_uid=os.getuid())
        guard.provision_fresh(
            witnesses=(directory / "prior-install",), update_root=directory / "updates"
        )
        with (
            serving_helper(directory, panic_guard=guard) as (service, client),
            ConfigRepository(directory / "settings.db") as repo,
        ):
            repo.save_profile(Profile("normal", "Normal"))
            scheduler = RuntimeScheduler(
                database_path=directory / "settings.db", helper_client=client
            )
            server = ControlServer(
                ("127.0.0.1", 0), repo, helper_client=client, scheduler=scheduler
            )
            original_revision = client.control_revision()
            validate = server._validate_saved_plan

            def panic_during_save():
                validate()
                assert client.normal_access()["ok"] is True

            try:
                with (
                    patch.object(scheduler, "start"),
                    patch.object(server, "_validate_saved_plan", side_effect=panic_during_save),
                    patch.object(client, "apply", wraps=client.apply) as apply,
                ):
                    status, result = write(server, "/api/general-profile", {"profile_id": "normal"})
                assert status == 409 and not result["general_profile"]["active"]
                assert repo.get_general_profile().id == "normal"
                assert apply.call_args.kwargs["control_revision"] == original_revision
                assert apply.call_args.kwargs["explicit_apply"] is True
                assert (
                    service._plan_count == 0 and not service._access_manager.applied.options.enabled
                )
                assert client.gateway_status()["panic_stop_latched"] is True
                assert guard.read()["panic_stop_latched"] is True
                assert not scheduler.can_apply(scheduler.apply_generation)
            finally:
                server.server_close()
                scheduler.stop()

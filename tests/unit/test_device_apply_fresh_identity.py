"""Manual device Apply requires a successful current client scan before any save."""

import json

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository
from jittersim.engine.eligibility import station_snapshot
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking.helper import HelperClientError
from tests.unit.test_api import PlanRecordingHelper

CLIENT = {"ipv4_address": "192.168.50.178", "mac_address": "dc:1e:d5:3e:97:4c"}
DEVICE_ID = "client-dc1ed53e974c"


class ScanHelper(PlanRecordingHelper):
    scan_failed = False

    def connected_device_snapshot(self):
        if self.scan_failed:
            raise HelperClientError("client scan unavailable", reason="helper-busy")
        return super().connected_device_snapshot()

    def check_general_profile_support(self):
        return None


@pytest.fixture
def dashboard(tmp_path):
    repository = ConfigRepository(tmp_path / "manual-apply.db")
    repository.save_profile(Profile(id="low", name="Low", upload=Impairment(delay_ms=50)))
    repository.save_profile(Profile(id="slow", name="Slow", upload=Impairment(delay_ms=3000)))
    helper = ScanHelper()
    server = ControlServer(("127.0.0.1", 0), repository, helper_client=helper)
    try:
        yield server, repository, helper
    finally:
        server.server_close()
        repository.close()


def write(server, path, payload):
    status, _, body = server.write_response_for(path, payload)
    return status, json.loads(body)


def apply_device(server):
    return write(server, "/api/connected-devices/apply-profile", {**CLIENT, "profile_id": "slow"})


def test_selected_client_verifier_refuses_matching_stale_identity(dashboard):
    server, _, helper = dashboard
    snapshot = station_snapshot(helper)
    helper.scan_failed = True
    stale = station_snapshot(helper)
    assert stale["stale"] is True
    assert stale["devices"] == snapshot["devices"]
    assert stale["association_status_available"] is True
    device = Device(id=DEVICE_ID, name="Client", profile_id="slow", **CLIENT)

    with pytest.raises(ValueError, match="Refresh the list"):
        server._verify_selected_client(device, stale)


@pytest.mark.parametrize("running", ["reset", "saved", "everyone"])
@pytest.mark.parametrize("evidence", ["last_good", "helper_cache", "unavailable"])
def test_failed_scan_refuses_apply_without_changing_saved_or_running_state(
    dashboard, running, evidence
):
    server, repository, helper = dashboard
    repository.save_device(Device(id=DEVICE_ID, name="Existing", profile_id="low", **CLIENT))
    if running == "everyone":
        assert write(server, "/api/general-profile", {"profile_id": "low"})[0] == 200
    else:
        assert write(server, "/api/reset" if running == "reset" else "/api/apply", {})[0] == 200
    good = station_snapshot(helper)
    if evidence == "helper_cache":
        helper.recent_connected_devices = lambda max_age: good
    elif evidence == "unavailable":
        del helper._last_good_station_snapshot
    helper.scan_failed = True
    before_database = tuple(repository._connection.iterdump())
    before_runtime = repository.get_runtime_state()
    before_plan = helper.applied_plan
    before_access = helper.access_plan
    before_applies = list(helper.plans)

    status, result = apply_device(server)

    assert status == 409, result
    assert "Refresh the list" in result["error"]
    assert tuple(repository._connection.iterdump()) == before_database
    assert repository.get_runtime_state() == before_runtime
    assert helper.applied_plan is before_plan
    assert helper.access_plan is before_access
    assert helper.plans == before_applies


def test_matching_stale_scan_cannot_create_or_start_a_new_device(dashboard):
    server, repository, helper = dashboard
    assert write(server, "/api/reset", {})[0] == 200
    station_snapshot(helper)
    helper.scan_failed = True
    before = tuple(repository._connection.iterdump())

    status, result = apply_device(server)

    assert status == 409, result
    assert repository.get_device(DEVICE_ID) is None
    assert tuple(repository._connection.iterdump()) == before
    assert helper.plans == []


def test_scheduler_keeps_its_existing_device_plan_during_the_same_scan_failure(dashboard):
    _, repository, helper = dashboard
    repository.save_device(Device(id=DEVICE_ID, name="Existing", profile_id="low", **CLIENT))
    # Protection requires the scheduler to check live identity when preparing its plan.
    repository.save_unaffected_device(UnaffectedDevice("02:00:00:00:00:01", "Protected"))
    scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
    good = scheduler.prepare_plan(repository=repository)
    helper.scan_failed = True

    cached = scheduler.prepare_plan(repository=repository)

    assert station_snapshot(helper)["stale"] is True
    assert [rule.device_id for rule in cached.plan.mark_rules] == [DEVICE_ID]
    assert cached.plan == good.plan


@pytest.mark.parametrize("explicit_fresh", [False, True])
def test_successful_current_scan_still_saves_and_starts_the_selected_device(
    dashboard, explicit_fresh
):
    server, repository, helper = dashboard
    assert write(server, "/api/reset", {})[0] == 200
    if explicit_fresh:
        scan = helper.connected_device_snapshot
        helper.connected_device_snapshot = lambda: {**scan(), "stale": False}

    status, result = apply_device(server)

    assert status == 200, result
    assert result["live_applied"] is True
    assert repository.get_device(DEVICE_ID).profile_id == "slow"
    assert [rule.device_id for rule in helper.applied_plan.mark_rules] == [DEVICE_ID]
    assert repository.get_device_start_scope() == {DEVICE_ID}

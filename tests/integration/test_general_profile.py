"""Exercise general profile controls without changing saved device rules."""

import json
import sqlite3
import threading
from dataclasses import replace
from ipaddress import IPv4Address
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import (
    DataLimit,
    Device,
    Impairment,
    MacPolicy,
    Profile,
    Randomness,
    Timer,
)
from jittersim.config.storage import ConfigRepository
from jittersim.engine.codec import PlanCodecError, plan_from_payload, plan_to_payload
from jittersim.engine.plan import GENERAL_QUEUE_ID
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.service import compile_stored_plan
from jittersim.networking.browser_proxy import ProxyError, destination_allowed, select_mark
from jittersim.networking.helper import HelperClientError

PROTECTED = "02:00:00:00:00:01"
AFFECTED = "02:00:00:00:00:02"


class GeneralHelper:
    def __init__(self):
        self.plans = []
        self.supported = True
        self.fail_apply = False
        self.counters = {"devices": {}, "services": {}}
        self.resets = 0

    def check_general_profile_support(self):
        if not self.supported:
            raise HelperClientError("Update the Pi helper before applying a general profile")

    def ping(self):
        return {"ok": True}

    def gateway_status(self):
        return {"outage_stop_latched": False, "state_error": False}

    def set_gateway_persistence(self, *, enabled):
        return {"ok": True, "mode_enabled": enabled}

    def apply(self, *, plan, explicit_apply=False):
        if self.fail_apply:
            raise HelperClientError("apply failed")
        self.plans.append(plan)
        self.counters = {"devices": {}, "services": {}}
        return {"commands_run": 1}

    def reset(self):
        self.resets += 1
        return {"commands_run": 1}

    def traffic_counters(self):
        return self.counters

    def connected_device_snapshot(self):
        return {"devices": [], "association_status_available": False}


@pytest.fixture
def control(tmp_path):
    repo = ConfigRepository(tmp_path / "config.db")
    repo.save_profile(Profile("normal", "Normal"))
    repo.save_profile(
        Profile(
            "slow",
            "Slow",
            upload=Impairment(bandwidth_kbit=1000),
            download=Impairment(delay_ms=300, bandwidth_kbit=2000),
        )
    )
    helper = GeneralHelper()
    scheduler = RuntimeScheduler(database_path=tmp_path / "config.db", helper_client=helper)
    scheduler.start = lambda: None
    server = ControlServer(
        ("127.0.0.1", 0),
        repo,
        admin_token="fixture-token",
        helper_client=helper,
        scheduler=scheduler,
    )
    yield repo, helper, scheduler, server
    scheduler.stop()
    server.server_close()
    repo.close()


def write(server, path, payload):
    status, _, body = server.write_response_for(path, payload)
    return status, json.loads(body)


def test_general_mode_works_without_assignments_and_survives_reopening(control, tmp_path):
    repo, helper, scheduler, server = control
    repo.save_unaffected_device(UnaffectedDevice(PROTECTED))
    status, data = write(server, "/api/general-profile", {"profile_id": "slow"})
    assert status == 200 and data["general_profile"]["active"]
    plan = helper.plans[-1]
    assert not plan.mark_rules and not repo.list_devices()
    assert plan.protected_mac_addresses == (PROTECTED,)
    assert len(plan.queues) == 2
    assert plan_from_payload(plan_to_payload(plan)) == plan
    assert scheduler.applied_queue_bandwidth_limits()[GENERAL_QUEUE_ID]["upload_kbit"] == 1000
    backup = tmp_path / "backup.db"
    backup.write_bytes(repo.backup_bytes())
    with ConfigRepository(backup) as reopened:
        assert reopened.get_general_profile().id == "slow"
        assert not reopened.list_devices()
    with sqlite3.connect(backup) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("DELETE FROM profiles WHERE id = 'slow'")


def test_general_mode_overrides_timers_and_quota_without_rewriting_assignments(control):
    repo, helper, scheduler, server = control
    repo.save_device(Device("alice", "Alice", "192.168.50.10", "normal", AFFECTED))
    repo.save_timer(Timer("timer", "alice", "normal", 1, 10000))
    repo.save_data_limit(DataLimit("alice", "normal", 100, used_bytes=100))
    before = repo.list_devices()
    assert write(server, "/api/general-profile", {"profile_id": "slow"})[0] == 200
    assert scheduler.build_plan(repository=repo, now=2).general_profile.profile_id == "slow"
    assert repo.list_devices() == before
    assert write(server, "/api/general-profile/stop", {})[0] == 200
    assert repo.get_general_profile() is None
    assert helper.plans[-1].general_profile is None
    assert helper.plans[-1].queues[0].impairment.bandwidth_kbit is None
    assert repo.list_devices() == before


def test_general_mode_does_not_depend_on_the_individual_device_limit(control):
    repo, helper, _, server = control
    for number in range(20):
        repo.save_device(
            Device(f"client-{number}", f"Client {number}", f"192.168.50.{number + 100}", "normal")
        )
    assert write(server, "/api/general-profile", {"profile_id": "slow"})[0] == 200
    assert len(helper.plans[-1].queues) == 2
    assert len(repo.list_devices()) == 20
    status, _ = write(server, "/api/general-profile/stop", {})
    assert status == 409
    assert repo.get_general_profile().id == "slow"
    assert helper.plans[-1].general_profile is not None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"profile_id": None},
        {"profile_id": True},
        {"profile_id": "missing"},
        {"profile_id": "slow", "device_id": "alice"},
    ],
)
def test_invalid_selection_keeps_the_saved_mode(control, payload):
    repo, helper, _, server = control
    assert write(server, "/api/general-profile", {"profile_id": "normal"})[0] == 200
    applied = len(helper.plans)
    assert write(server, "/api/general-profile", payload)[0] == 409
    assert repo.get_general_profile().id == "normal"
    assert len(helper.plans) == applied


def test_old_helper_is_rejected_before_saving_and_apply_failure_is_not_active(control):
    repo, helper, _, server = control
    helper.supported = False
    assert write(server, "/api/general-profile", {"profile_id": "slow"})[0] == 503
    assert repo.get_general_profile() is None
    helper.supported = True
    helper.fail_apply = True
    status, response = write(server, "/api/general-profile", {"profile_id": "slow"})
    assert status == 502 and "Selection saved" in response["error"]
    assert not response["general_profile"]["active"]
    assert response["general_profile"]["state"]["observed_state"] == "error"


def test_reset_stays_paused_after_recovery_then_explicit_apply_resumes(control):
    repo, helper, scheduler, server = control
    assert write(server, "/api/general-profile", {"profile_id": "slow"})[0] == 200
    assert write(server, "/api/reset", {})[0] == 200
    applied = len(helper.plans)
    server.recover_saved_network_state()
    assert not scheduler.tick(repository=repo, now=100)
    assert len(helper.plans) == applied
    assert repo.get_general_profile().id == "slow"
    assert write(server, "/api/apply", {})[0] == 200
    assert helper.plans[-1].general_profile.profile_id == "slow"


def test_general_mode_does_not_charge_quota_and_rebases_on_exit(control):
    repo, helper, scheduler, server = control
    repo.save_device(Device("alice", "Alice", "192.168.50.10", "normal", AFFECTED))
    repo.save_data_limit(DataLimit("alice", "normal", 10000))
    assert write(server, "/api/apply", {})[0] == 200
    helper.counters = {"devices": {"alice": 40}, "services": {}}
    assert write(server, "/api/general-profile", {"profile_id": "slow"})[0] == 200
    assert repo.get_data_limit("alice").used_bytes == 40
    helper.counters = {"devices": {"alice": 1000, GENERAL_QUEUE_ID: 5000}, "services": {}}
    scheduler._last_quota_sample_at = 0
    scheduler._sample_quota_usage_if_needed(repository=repo, now=100)
    assert repo.get_data_limit("alice").used_bytes == 40
    assert write(server, "/api/general-profile/stop", {})[0] == 200
    helper.counters = {"devices": {"alice": 10}, "services": {}}
    scheduler._last_quota_sample_at = 0
    scheduler._sample_quota_usage_if_needed(repository=repo, now=200)
    assert repo.get_data_limit("alice").used_bytes == 50


def test_random_profile_updates_and_outages_remain_general(control):
    repo, helper, scheduler, server = control
    profile = replace(
        repo.get_profile("slow"),
        random_seed=42,
        reroll_interval_seconds=10,
        upload=Impairment(delay_ms=200, delay_randomness=Randomness(50)),
        outage_probability=100,
        outage_interval_seconds=10,
        outage_duration_seconds=5,
    )
    repo.save_profile(profile)
    with patch("jittersim.engine.runtime.time.time", return_value=0):
        assert write(server, "/api/general-profile", {"profile_id": "slow"})[0] == 200
    assert scheduler.tick(repository=repo, now=0) is False
    assert scheduler.tick(repository=repo, now=11)
    assert helper.plans[-1].general_profile.outage_active
    assert scheduler.tick(repository=repo, now=17)
    assert not helper.plans[-1].general_profile.outage_active
    repo.save_profile(replace(profile, upload=Impairment(delay_ms=123)))
    assert scheduler.tick(repository=repo, now=18)
    assert helper.plans[-1].queues[0].impairment.delay_ms == 123
    assert not repo.list_devices()


def test_proxy_general_profile_covers_unassigned_clients_and_bypasses_protected(control):
    repo, _, _, _ = control
    repo.save_unaffected_device(UnaffectedDevice(PROTECTED))
    repo.save_general_profile("slow")
    plan = compile_stored_plan(repository=repo)
    address = IPv4Address("192.168.50.99")
    assert select_mark(plan, client_ip=address, host="example.com", client_mac=AFFECTED) == (
        1,
        "general-profile",
    )
    assert select_mark(plan, client_ip=address, host="example.com", client_mac=PROTECTED) == (
        0,
        "unaffected",
    )
    outage = replace(plan, general_profile=replace(plan.general_profile, outage_active=True))
    assert select_mark(outage, client_ip=address, host="example.com", client_mac=PROTECTED)[0] == 0
    with pytest.raises(ProxyError):
        select_mark(outage, client_ip=address, host="example.com", client_mac=AFFECTED)
    allowed = replace(plan, general_profile=replace(plan.general_profile, allowed_addresses=()))
    assert not destination_allowed(allowed, address, "8.8.8.8", AFFECTED)
    assert destination_allowed(allowed, address, "8.8.8.8", PROTECTED)
    blocked = replace(allowed, global_blocked_addresses=(IPv4Address("8.8.8.8"),))
    assert not destination_allowed(blocked, address, "8.8.8.8", PROTECTED)
    with pytest.raises(ProxyError):
        select_mark(plan, client_ip=address, host="example.com")
    no_protection = replace(
        plan,
        protected_mac_addresses=(),
        mac_policy=MacPolicy(mode="denylist", denylist=(AFFECTED,)),
    )
    assert select_mark(
        no_protection, client_ip=address, host="example.com", client_mac=AFFECTED
    ) == (1, "general-profile")
    with pytest.raises(ProxyError):
        select_mark(
            replace(no_protection, access=None),
            client_ip=address, host="example.com", client_mac=AFFECTED,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("outage_active", 1),
        ("profile_id", "bad;name"),
        ("allowed_addresses", ["::1"]),
        ("allowed_addresses", [True]),
        ("allowed_addresses", ["8.8.8.8"] * 4097),
    ],
)
def test_general_wire_format_rejects_unsafe_settings(control, field, value):
    repo, _, _, _ = control
    repo.save_general_profile("slow")
    payload = plan_to_payload(compile_stored_plan(repository=repo))
    payload["general_profile"][field] = value
    with pytest.raises(PlanCodecError):
        plan_from_payload(payload)


def test_general_wire_format_cannot_mix_individual_rules_or_silently_fall_back(control):
    repo, _, _, _ = control
    repo.save_general_profile("slow")
    payload = plan_to_payload(compile_stored_plan(repository=repo))
    payload["mark_rules"] = [{"device_id": "alice", "ipv4_address": "192.168.50.10", "mark": 1}]
    with pytest.raises(PlanCodecError):
        plan_from_payload(payload)
    payload["mark_rules"] = []
    del payload["general_profile"]
    with pytest.raises(PlanCodecError):
        plan_from_payload(payload)


def test_general_actions_require_authentication(control):
    _, _, _, server = control
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        for path, payload in (
            ("/api/general-profile", None),
            ("/api/general-profile", {"profile_id": "slow"}),
            ("/api/general-profile/stop", {}),
        ):
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}{path}",
                data=None if payload is None else json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with pytest.raises(HTTPError) as error:
                urlopen(request, timeout=3)
            assert error.value.code == 401
    finally:
        server.shutdown()
        worker.join(timeout=3)


def test_fresh_database_has_no_general_profile_after_repeated_open(tmp_path):
    path = tmp_path / "settings.db"
    for _ in range(2):
        with ConfigRepository(path) as repo:
            assert repo.get_general_profile() is None


def test_repeated_stop_does_not_resume_rules_after_reset(control):
    repo, helper, _, server = control
    repo.save_device(Device("alice", "Alice", "192.168.50.10", "slow"))
    assert write(server, "/api/reset", {})[0] == 200
    assert write(server, "/api/general-profile/stop", {})[0] == 200
    assert not helper.plans
    assert repo.get_runtime_state().observed_state == "reset"


def test_stop_waits_for_a_background_apply_and_does_not_restore_general_mode(control):
    repo, helper, scheduler, server = control
    assert write(server, "/api/general-profile", {"profile_id": "slow"})[0] == 200
    repo.save_profile(replace(repo.get_profile("slow"), upload=Impairment(delay_ms=123)))
    entered = threading.Event()
    release = threading.Event()
    original_apply = helper.apply
    results = []

    def blocking_apply(*, plan):
        if plan.general_profile is not None:
            entered.set()
            assert release.wait(3)
        return original_apply(plan=plan)

    helper.apply = blocking_apply

    applying = threading.Thread(target=lambda: results.append(scheduler.tick()))
    applying.start()
    assert entered.wait(3)
    timer = threading.Timer(0.1, release.set)
    timer.start()
    try:
        assert write(server, "/api/general-profile/stop", {})[0] == 200
    finally:
        release.set()
        applying.join(3)
        timer.join(3)
    assert not applying.is_alive()
    assert results == [True]
    assert repo.get_general_profile() is None
    assert helper.plans[0].general_profile is not None
    assert helper.plans[-1].general_profile is None
    assert not scheduler.tick(repository=repo, now=200)

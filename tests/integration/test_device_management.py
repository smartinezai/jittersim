"""Exercise saved identities, profile protection and atomic dashboard actions."""

import json
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime
from ipaddress import IPv4Address
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from jittersim.api.server import ControlServer, _connected_device_from_payload
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import (
    DataLimit,
    Device,
    DeviceGroup,
    GroupDataLimit,
    Impairment,
    MacPolicy,
    Profile,
    Progression,
    Randomness,
    Schedule,
    ServiceAssignment,
    ServiceQuota,
    TimeQuota,
    Timer,
    WebsitePolicy,
    time_quota_counter_id,
)
from jittersim.config.storage import ConfigRepository, StorageError
from jittersim.engine.codec import PlanCodecError, plan_from_payload, plan_to_payload
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.service import compile_stored_plan
from jittersim.networking.helper import HelperClientError
from jittersim.networking.renderer import render_command_plan
from jittersim.networking.topology import Topology

ALICE = "02:00:00:00:00:01"
BOB = "02:00:00:00:00:02"


class ClientsHelper:
    def __init__(self):
        self.clients = [
            {
                "mac_address": ALICE,
                "ipv4_address": "192.168.50.10",
                "association_verified": True,
                "state": "connected",
            },
            {
                "mac_address": BOB,
                "ipv4_address": "192.168.50.11",
                "association_verified": True,
                "state": "connected",
            },
        ]
        self.counters = {"devices": {}, "services": {}}
        self.plans = []
        self.available = True
        self.fail_apply = False

    def connected_device_snapshot(self):
        return {"devices": self.clients, "association_status_available": self.available}

    def apply(self, *, plan, explicit_apply=False):
        if self.fail_apply:
            raise HelperClientError("apply failed")
        self.plans.append(plan)
        self.counters = {
            "devices": {rule.device_id: 0 for rule in plan.mark_rules},
            "services": {rule.quota_id: 0 for rule in plan.service_quota_rules},
        }
        return {"commands_run": 1}

    def traffic_counters(self):
        return self.counters


@pytest.fixture
def control(tmp_path):
    repository = ConfigRepository(tmp_path / "config.db")
    repository.save_profile(Profile("normal", "Normal"))
    repository.save_profile(
        Profile(
            "slow", "Slow", upload=Impairment(delay_ms=400), download=Impairment(loss_percent=100)
        )
    )
    repository.save_device(Device("alice", "Alice", "192.168.50.10", "slow", ALICE))
    repository.save_device(Device("bob", "Bob", "192.168.50.11", "slow", BOB))
    helper = ClientsHelper()
    server = ControlServer(
        ("127.0.0.1", 0), repository, helper_client=helper, admin_token="fixture-token"
    )
    yield repository, helper, server
    server.server_close()
    repository.close()


def write(server, path, payload):
    status, _, body = server.write_response_for(path, payload)
    return status, json.loads(body)


def test_protection_is_normalized_persistent_and_in_configuration_backup(control, tmp_path):
    repo, helper, server = control
    status, result = write(
        server,
        "/api/unaffected-devices",
        {"mac_address": ALICE.upper().replace(":", "-"), "display_name": " Alice "},
    )
    assert status == 200 and result["live_applied"]
    assert [rule.device_id for rule in helper.plans[-1].mark_rules] == ["bob"]
    assert helper.plans[-1].protected_mac_addresses == (ALICE,)
    backup = tmp_path / "backup.db"
    backup.write_bytes(repo.backup_bytes())
    with sqlite3.connect(backup) as db:
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name='random_website_analytics'"
            ).fetchone()
            is None
        )
    with ConfigRepository(backup) as restored:
        assert restored.list_unaffected_devices()[0].display_name == "Alice"
    # A missing live client does not remove its saved protection.
    helper.clients = []
    assert (
        json.loads(server.response_for("/api/unaffected-devices")[2])["devices"][0]["mac_address"]
        == ALICE
    )


def test_protection_limit_and_transaction_failure_preserve_previous_records(tmp_path):
    with ConfigRepository(tmp_path / "limit.db") as repo:
        for value in range(256):
            repo.save_unaffected_device(
                UnaffectedDevice(f"02:00:00:00:{value // 256:02x}:{value % 256:02x}")
            )
        with pytest.raises(StorageError, match="256"):
            repo.save_unaffected_device(UnaffectedDevice("02:00:00:01:00:00"))
        repo.save_unaffected_device(UnaffectedDevice("02:00:00:00:00:01", "Updated"))
        with pytest.raises(RuntimeError):
            with repo.transaction():
                repo.delete_unaffected_device("02:00:00:00:00:01")
                raise RuntimeError("later validation failed")
        assert len(repo.list_unaffected_devices()) == 256
        assert repo.list_unaffected_devices()[1].display_name == "Updated"


def test_concurrent_protection_writes_cannot_exceed_the_limit(tmp_path):
    path = tmp_path / "concurrent.db"
    with ConfigRepository(path) as repo:
        with repo.transaction():
            for value in range(255):
                repo.save_unaffected_device(UnaffectedDevice(f"02:00:00:00:00:{value:02x}"))
    barrier = threading.Barrier(2)
    outcomes = []

    def save(mac):
        with ConfigRepository(path) as repo:
            barrier.wait(timeout=3)
            try:
                repo.save_unaffected_device(UnaffectedDevice(mac))
                outcomes.append("saved")
            except StorageError:
                outcomes.append("full")

    threads = [
        threading.Thread(target=save, args=(mac,))
        for mac in ("02:00:00:01:00:01", "02:00:00:01:00:02")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert sorted(outcomes) == ["full", "saved"]
    with ConfigRepository(path) as repo:
        assert len(repo.list_unaffected_devices()) == 256


def test_mac_identity_keeps_groups_after_ip_change_and_rejects_ip_reuse(control):
    repo, helper, server = control
    repo.save_device_group(DeviceGroup("family", "Family", ("alice",)))
    helper.clients[0]["ipv4_address"] = "192.168.50.99"
    status, result = write(
        server,
        "/api/connected-devices/apply-profile",
        {**helper.clients[0], "profile_id": "normal"},
    )
    assert status == 200 and result["device"]["id"] == "alice"
    assert repo.get_device_group("family").device_ids == ("alice",)
    assert str(repo.get_device("alice").ipv4_address) == "192.168.50.99"
    helper.clients[1]["ipv4_address"] = "192.168.50.99"
    status, result = write(
        server,
        "/api/connected-devices/apply-profile",
        {**helper.clients[1], "profile_id": "normal"},
    )
    assert status == 409 and "different saved device" in result["error"]
    assert repo.get_device("alice").mac_address == ALICE
    assert str(repo.get_device("bob").ipv4_address) == "192.168.50.11"
    listed = json.loads(server.response_for("/api/connected-devices")[2])["devices"]
    assert listed[1]["assigned_device_id"] == "bob"
    assert listed[1]["group_id"] is None


def test_profile_assignment_reports_the_active_group_and_timer_override(control):
    repo, helper, server = control
    repo.save_device_group(DeviceGroup("family", "Family", ("alice",), "slow"))
    status, result = write(
        server,
        "/api/connected-devices/apply-profile",
        {**helper.clients[0], "profile_id": "normal"},
    )
    assert status == 200 and "Group Family currently uses slow" in result["message"]
    now = 110
    repo.save_timer(Timer("temporary", "alice", "slow", 100, 200))
    with patch("jittersim.api.server.time.time", return_value=now):
        status, result = write(
            server,
            "/api/connected-devices/apply-profile",
            {**helper.clients[0], "profile_id": "normal"},
        )
    assert status == 200 and "Timer temporary currently uses slow" in result["message"]


def test_editing_a_profile_updates_an_existing_runtime_choice_immediately(control):
    repo, helper, _ = control
    scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
    scheduler._build_plan(repository=repo, now=100)
    repo.save_profile(replace(repo.get_profile("slow"), download=Impairment(delay_ms=700)))
    plan = scheduler.build_plan(repository=repo, now=101)
    assert all(
        queue.impairment.delay_ms == 700
        for queue in plan.queues
        if queue.direction.value == "download"
    )


def test_reused_ip_does_not_display_or_take_another_identity(control):
    repo, helper, server = control
    new_mac = "02:00:00:00:00:03"
    helper.clients = [{**helper.clients[0], "mac_address": new_mac}]
    client = json.loads(server.response_for("/api/connected-devices")[2])["devices"][0]
    assert client["assigned_device_id"] is None
    with pytest.raises(ValueError, match="different saved device"):
        _connected_device_from_payload({**client, "profile_id": "normal"}, repo)


def test_generated_client_id_cannot_replace_an_unrelated_saved_record(control):
    repo, helper, server = control
    mac = "02:00:00:00:00:03"
    occupied_id = "client-" + mac.replace(":", "")
    repo.save_device(Device(occupied_id, "Keep this name", "192.168.50.80", "normal"))
    helper.clients.append(
        {**helper.clients[0], "mac_address": mac, "ipv4_address": "192.168.50.81"}
    )
    status, result = write(
        server, "/api/connected-devices/apply-profile", {**helper.clients[-1], "profile_id": "slow"}
    )
    assert status == 409 and "conflicts" in result["error"]
    assert repo.get_device(occupied_id).name == "Keep this name"
    assert str(repo.get_device(occupied_id).ipv4_address) == "192.168.50.80"


def test_legacy_identity_binds_only_after_verified_selection(control):
    repo, helper, server = control
    repo.save_device(replace(repo.get_device("alice"), mac_address=None))
    helper.available = False
    status, _ = write(
        server,
        "/api/connected-devices/apply-profile",
        {**helper.clients[0], "profile_id": "normal"},
    )
    assert status == 409 and repo.get_device("alice").mac_address is None
    helper.available = True
    status, _ = write(
        server,
        "/api/connected-devices/apply-profile",
        {**helper.clients[0], "profile_id": "normal"},
    )
    assert status == 200 and repo.get_device("alice").mac_address == ALICE


def test_unassigned_clients_join_groups_atomically_without_mac_typing(control):
    repo, helper, server = control
    helper.clients[0].update(mac_address="02:00:00:00:00:03", ipv4_address="192.168.50.30")
    repo.save_device_group(DeviceGroup("family", "Family", ("bob",), "normal"))
    status, result = write(
        server, "/api/connected-devices/group", {**helper.clients[0], "group_id": "family"}
    )
    assert status == 200 and result["live_applied"]
    saved = repo.get_device_by_mac_address("02:00:00:00:00:03")
    assert saved.profile_id == "normal"
    assert set(repo.get_device_group("family").device_ids) == {"bob", saved.id}
    # A membership conflict cannot change the client's IP or leave a partial group.
    helper.clients[0]["ipv4_address"] = "192.168.50.99"
    version = repo.get_runtime_state().configuration_version
    status, result = write(
        server,
        "/api/groups",
        {
            "id": "other",
            "name": "Other",
            "profile_id": "slow",
            "device_ids": [],
            "connected_clients": [helper.clients[0]],
        },
    )
    assert status == 409 and "already belongs" in result["error"]
    assert repo.get_device_group("other") is None
    assert str(repo.get_device_by_mac_address("02:00:00:00:00:03").ipv4_address) == "192.168.50.30"
    assert repo.get_runtime_state().configuration_version == version


def test_unsaved_client_needs_group_profile_and_expired_selection_rolls_back(control):
    repo, helper, server = control
    helper.clients[0].update(mac_address="02:00:00:00:00:03", ipv4_address="192.168.50.30")
    payload = {
        "id": "family",
        "name": "Family",
        "device_ids": ["bob"],
        "connected_clients": [helper.clients[0]],
    }
    status, result = write(server, "/api/groups", payload)
    assert status == 409 and "Choose a group profile" in result["error"]
    assert repo.get_device_by_mac_address("02:00:00:00:00:03") is None
    helper.clients = [helper.clients[1]]
    status, result = write(server, "/api/groups", {**payload, "profile_id": "normal"})
    assert status == 409 and "no longer verified" in result["error"]
    assert repo.get_device_group("family") is None


def test_budget_rejection_preserves_protection_and_connected_assignments(control):
    repo, helper, server = control
    repo.save_unaffected_device(UnaffectedDevice(ALICE))
    for index in range(14):
        repo.save_device(
            Device(
                f"extra-{index}", "Extra", f"192.168.50.{30 + index}", "normal",
                f"02:00:00:00:01:{index:02x}",
            )
        )
    status, result = write(server, "/api/unaffected-devices/delete", {"mac_address": ALICE})
    assert status == 409 and "15 affected" in result["error"]
    assert repo.list_unaffected_devices()[0].mac_address == ALICE
    helper.clients.append(
        {**helper.clients[1], "ipv4_address": "192.168.50.80", "mac_address": "02:00:00:00:00:03"}
    )
    status, _ = write(
        server,
        "/api/connected-devices/apply-profile",
        {**helper.clients[-1], "profile_id": "normal"},
    )
    assert (
        status == 409 and repo.get_device_by_mac_address(helper.clients[-1]["mac_address"]) is None
    )


def test_failed_network_apply_reports_saved_but_unconfirmed_protection(control):
    repo, helper, server = control
    helper.fail_apply = True
    status, result = write(server, "/api/unaffected-devices", {"mac_address": ALICE})
    assert status == 502 and result["live_applied"] is False
    assert "not confirmed" in result["error"]
    assert repo.list_unaffected_devices()[0].mac_address == ALICE
    assert repo.get_runtime_state().observed_state == "error"


@pytest.mark.parametrize(
    "source",
    [
        "direct",
        "group",
        "schedule",
        "timer",
        "progression",
        "device-quota",
        "group-quota",
        "random",
    ],
)
def test_protection_precedes_every_runtime_profile_source(control, source):
    repo, helper, _ = control
    repo.save_unaffected_device(UnaffectedDevice(ALICE))
    now = 110
    if source == "group":
        repo.save_device_group(DeviceGroup("family", "Family", ("alice", "bob"), "normal"))
    elif source == "schedule":
        repo.save_schedule(
            Schedule(
                "all-day", "alice", "normal", (datetime.fromtimestamp(now).weekday(),), 0, 1439
            )
        )
    elif source == "timer":
        repo.save_timer(Timer("temporary", "alice", "normal", 100, 200))
    elif source == "progression":
        repo.save_progression(Progression("progress", "alice", "normal", "slow", 100, 100))
    elif source == "device-quota":
        repo.save_data_limit(DataLimit("alice", "normal", 10, used_bytes=10))
    elif source == "group-quota":
        repo.save_device_group(DeviceGroup("family", "Family", ("alice", "bob")))
        repo.save_group_data_limit(GroupDataLimit("family", "normal", 10, used_bytes=10))
    elif source == "random":
        repo.save_profile(
            replace(
                repo.get_profile("slow"),
                reroll_interval_seconds=1,
                outage_probability=100,
                outage_interval_seconds=1,
                outage_duration_seconds=100,
                website_mode="allow_only",
                website_domains=("example.com",),
                website_randomness=Randomness(enabled_probability=100),
            )
        )
    scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
    with patch(
        "jittersim.engine.website.resolve_domains", return_value=(IPv4Address("203.0.113.10"),)
    ):
        scheduler._build_plan(repository=repo, now=100)
        outcomes = []
        plan, _ = scheduler._build_plan(repository=repo, now=now, random_website_outcomes=outcomes)
    assert [rule.device_id for rule in plan.mark_rules] == ["bob"]
    assert all(item.device_id != "alice" for item in outcomes)
    assert all(item.device_ip != IPv4Address("192.168.50.10") for item in plan.website_filters)
    assert IPv4Address("192.168.50.10") not in plan.blocked_device_addresses
    assert "alice" not in scheduler._rolls


def test_service_and_timed_fallbacks_are_skipped_without_losing_global_rules(control):
    repo, helper, _ = control
    repo.save_unaffected_device(UnaffectedDevice(ALICE))
    repo.save_service_assignment(
        ServiceAssignment("browser", "alice", "Video", ("example.com",), "slow")
    )
    repo.save_service_quota(
        ServiceQuota("video", "alice", "Video", ("example.com",), "slow", 10, used_bytes=10)
    )
    repo.save_time_quota(
        TimeQuota(
            "time",
            "alice",
            "Video",
            ("example.com",),
            "slow",
            60,
            used_seconds=60,
            started_at=100,
            expires_at=200,
        )
    )
    repo.save_mac_policy(MacPolicy(mode="denylist", denylist=("02:00:00:00:00:99",)))
    repo.save_website_policy(WebsitePolicy(blocked_domains=("example.com",)))
    scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
    with patch(
        "jittersim.engine.website.resolve_domains", return_value=(IPv4Address("203.0.113.10"),)
    ):
        plans = [
            compile_stored_plan(repository=repo),
            scheduler.build_plan(repository=repo, now=110),
        ]
    for plan in plans:
        assert not plan.service_assignment_rules and not plan.service_quota_rules
        assert plan.global_blocked_addresses and plan.mac_policy.denylist
        payload = plan_to_payload(plan)
        assert plan_from_payload(payload) == plan
        script = render_command_plan(plan=plan, topology=Topology("eth0", "wlan0"))[0].stdin
        assert (
            script.index("block global website")
            < script.index("unaffected by profiles")
            < script.index("usage-device bob")
        )
        assert "ether daddr { " + ALICE + " } meta mark set 0 return" in script
    payload["protected_mac_addresses"] = ["02:00:00:00:00:01; reboot"]
    with pytest.raises(PlanCodecError):
        plan_from_payload(payload)


def test_identity_lookup_failure_removes_stale_rules_but_keeps_mac_protection(control):
    repo, helper, server = control
    repo.save_unaffected_device(UnaffectedDevice(ALICE))
    helper.available = False
    scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
    plan = scheduler.build_plan(repository=repo)
    assert not plan.mark_rules and not plan.queues
    assert plan.protected_mac_addresses == (ALICE,)
    assert plan.unverified_device_ids == ("bob",)
    status, result = write(server, "/api/apply", {})
    assert status == 200 and result["unverified_device_ids"] == ["bob"]
    assert "bob" in result["warning"]
    helper.available = True
    helper.clients[1]["ipv4_address"] = "192.168.50.88"
    plan = scheduler.build_plan(repository=repo)
    assert str(plan.mark_rules[0].ipv4_address) == "192.168.50.88"


def test_global_website_allowlist_still_applies_to_unaffected_clients(control):
    from jittersim.networking.browser_proxy import destination_allowed

    repo, helper, _ = control
    repo.save_unaffected_device(UnaffectedDevice(ALICE))
    repo.save_website_policy(WebsitePolicy(mode="allow_only", allowed_domains=("example.com",)))
    allowed = IPv4Address("203.0.113.10")
    with (
        patch(
            "jittersim.engine.website.resolve_domains",
            side_effect=lambda **kwargs: (allowed,) if kwargs["domains"] else (),
        ),
        patch(
            "jittersim.engine.runtime.resolve_domains",
            side_effect=lambda **kwargs: (allowed,) if kwargs["domains"] else (),
        ),
        patch(
            "jittersim.engine.service.resolve_domains",
            side_effect=lambda **kwargs: (allowed,) if kwargs["domains"] else (),
        ),
    ):
        plans = [
            compile_stored_plan(repository=repo),
            RuntimeScheduler(database_path=":memory:", helper_client=helper).build_plan(
                repository=repo
            ),
        ]
    for plan in plans:
        assert plan.global_allowed_addresses == (allowed,)
        assert destination_allowed(plan, IPv4Address("192.168.50.10"), str(allowed), ALICE)
        assert not destination_allowed(plan, IPv4Address("192.168.50.10"), "203.0.113.20", ALICE)
        script = render_command_plan(plan=plan, topology=Topology("eth0", "wlan0"))[0].stdin
        assert script.index("enforce global website allowlist") < script.index(
            "unaffected by profiles"
        )
        assert plan_from_payload(plan_to_payload(plan)) == plan


def test_progression_website_blocks_do_not_turn_into_a_total_outage(control):
    from jittersim.networking.browser_proxy import destination_allowed

    repo, helper, _ = control
    repo.save_progression(
        Progression(
            "websites",
            "alice",
            "normal",
            "slow",
            100,
            100,
            website_domains=("example.com", "other.example"),
        )
    )
    scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
    with patch(
        "jittersim.engine.website.resolve_domains",
        side_effect=lambda **kwargs: (IPv4Address("203.0.113.10"),) if kwargs["domains"] else (),
    ):
        plan = scheduler.build_plan(repository=repo, now=150)
    website_filter = plan.website_filters[0]
    assert website_filter.allowed_addresses is None
    assert not destination_allowed(plan, website_filter.device_ip, "203.0.113.10")
    assert destination_allowed(plan, website_filter.device_ip, "203.0.113.20")
    script = render_command_plan(plan=plan, topology=Topology("eth0", "wlan0"))[0].stdin
    assert "allowlist has no resolved addresses" not in script
    assert plan_from_payload(plan_to_payload(plan)) == plan


def test_quota_usage_excludes_protection_and_does_not_charge_the_exempt_interval(control):
    repo, helper, server = control
    repo.save_data_limit(DataLimit("alice", "slow", 1000, used_bytes=40, counter_bytes=10))
    repo.save_device_group(DeviceGroup("family", "Family", ("alice", "bob")))
    repo.save_group_data_limit(
        GroupDataLimit("family", "slow", 1000, used_bytes=40, counter_bytes=20)
    )
    repo.save_service_quota(
        ServiceQuota(
            "video",
            "alice",
            "Video",
            ("example.com",),
            "slow",
            1000,
            used_bytes=40,
            counter_bytes=10,
        )
    )
    repo.save_time_quota(
        TimeQuota(
            "time",
            "alice",
            "Video",
            ("example.com",),
            "slow",
            120,
            used_seconds=10,
            counter_bytes=10,
            last_sample_at=100,
            started_at=100,
            expires_at=500,
        )
    )
    scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
    server.scheduler = scheduler
    helper.counters = {
        "devices": {"alice": 10, "bob": 10},
        "services": {"video": 10, time_quota_counter_id("time"): 10},
    }
    # Sample synthetic times directly without starting a background worker.
    with patch.object(scheduler, "start"), patch("jittersim.api.server.time.time", return_value=110):
        status, _ = write(server, "/api/unaffected-devices", {"mac_address": ALICE})
    assert status == 200 and repo.get_data_limit("alice").used_bytes == 40
    helper.counters = {
        "devices": {"alice": 900, "bob": 30},
        "services": {"video": 900, time_quota_counter_id("time"): 900},
    }
    scheduler._sample_quota_usage_if_needed(repository=repo, now=180)
    assert repo.get_data_limit("alice").used_bytes == 40
    assert repo.get_service_quota("video").used_bytes == 40
    assert repo.get_time_quota("time").used_seconds == 10
    assert repo.get_group_data_limit("family").used_bytes == 70

    assert server._group_quota_counter("family") == 30
    with (
        patch.object(scheduler, "start"),
        patch("jittersim.api.server.time.time", return_value=200),
        patch(
            "jittersim.engine.runtime.resolve_domains", return_value=(IPv4Address("203.0.113.10"),)
        ),
    ):
        status, _ = write(server, "/api/unaffected-devices/delete", {"mac_address": ALICE})
    assert status == 200
    helper.counters["services"][time_quota_counter_id("time")] = 10
    scheduler._sample_quota_usage_if_needed(repository=repo, now=205)
    assert repo.get_time_quota("time").used_seconds == 15
    assert repo.get_data_limit("alice").used_bytes == 40
    assert repo.get_group_data_limit("family").used_bytes == 70


def test_protected_quota_resets_still_run_and_offline_members_do_not_freeze_group_usage(control):
    repo, helper, _ = control
    repo.save_unaffected_device(UnaffectedDevice(ALICE))
    repo.save_data_limit(
        DataLimit(
            "alice",
            "slow",
            100,
            used_bytes=90,
            reset_mode="interval",
            reset_interval_seconds=60,
            last_reset_at=100,
        )
    )
    repo.save_service_quota(
        ServiceQuota(
            "video",
            "alice",
            "Video",
            ("example.com",),
            "slow",
            100,
            used_bytes=90,
            reset_mode="interval",
            reset_interval_seconds=60,
            last_reset_at=100,
        )
    )
    repo.save_time_quota(
        TimeQuota(
            "time",
            "alice",
            "Video",
            ("example.com",),
            "slow",
            60,
            used_seconds=50,
            reset_mode="interval",
            reset_interval_seconds=60,
            last_reset_at=100,
            last_sample_at=100,
        )
    )
    repo.save_device(Device("offline", "Offline", "192.168.50.90", "slow", "02:00:00:00:00:09"))
    repo.save_device_group(DeviceGroup("family", "Family", ("alice", "bob", "offline")))
    repo.save_group_data_limit(GroupDataLimit("family", "slow", 100))
    helper.counters = {"devices": {"bob": 20}, "services": {}}
    scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
    scheduler._current_payload = plan_to_payload(scheduler.build_plan(repository=repo, now=161))
    scheduler._sample_quota_usage_if_needed(repository=repo, now=161)
    assert repo.get_data_limit("alice").used_bytes == 0
    assert repo.get_service_quota("video").used_bytes == 0
    assert repo.get_time_quota("time").used_seconds == 0
    assert repo.get_time_quota("time").last_sample_at == 161
    assert repo.get_group_data_limit("family").used_bytes == 20
    assert repo.get_data_limit("alice").last_reset_at == 161


def test_protection_changes_preserve_another_clients_byte_progression(control):
    repo, helper, server = control
    repo.save_progression(
        Progression(
            "streaming",
            "bob",
            "normal",
            "slow",
            100,
            1000,
            mode="bytes",
            traffic_target_bytes=1000,
        )
    )
    scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
    server.scheduler = scheduler
    scheduler._current_payload = plan_to_payload(scheduler.build_plan(repository=repo, now=100))
    helper.counters = {"devices": {"alice": 0, "bob": 500}, "services": {}}
    scheduler._sample_quota_usage_if_needed(repository=repo, now=106)
    assert scheduler.device_traffic_bytes(device_id="bob") == 500
    with (
        patch("jittersim.api.server.time.time", return_value=110),
        patch.object(scheduler, "start"),
    ):
        status, _ = write(server, "/api/unaffected-devices", {"mac_address": ALICE})
    assert status == 200
    assert scheduler.device_traffic_bytes(device_id="bob") == 500
    helper.counters["devices"]["bob"] = 250
    scheduler._sample_quota_usage_if_needed(repository=repo, now=116)
    assert scheduler.device_traffic_bytes(device_id="bob") == 750
    plan = scheduler.build_plan(repository=repo, now=116)
    bob_upload = next(
        queue
        for queue in plan.queues
        if queue.device_id == "bob" and queue.direction.value == "upload"
    )
    assert bob_upload.impairment.delay_ms == 300


def test_protected_http_actions_require_authentication(control):
    repo, _, server = control
    for path, data in [
        ("/api/unaffected-devices", None),
        ("/api/unaffected-devices", {"mac_address": ALICE}),
        ("/api/connected-devices/group", {"group_id": "missing"}),
    ]:
        responses = []

        def client():
            request = Request(
                f"http://127.0.0.1:{server.server_port}" + path,
                data=None if data is None else json.dumps(data).encode(),
                headers={"Content-Type": "application/json"},
            )
            try:
                with urlopen(request, timeout=3) as response:
                    responses.append(response.status)
            except HTTPError as error:
                responses.append(error.code)

        thread = threading.Thread(target=client)
        thread.start()
        server.handle_request()
        thread.join(3)
        assert responses == [401]
    assert not repo.list_unaffected_devices()

"""Keep protected ad choices separate from changing profile sessions."""

from unittest.mock import patch

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import DataLimit, Device, Impairment, Profile, Randomness
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.compiler import compile_plan
from jittersim.networking.adblocking import AdRouting, AdStore
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperService
from tests.adblocking_support import provision_guard
from tests.helper_support import serving
from tests.unit.test_applier import RootQueueExecutor
from tests.unit.test_runtime import ClientListingHelper


@pytest.mark.parametrize("general", [False, True])
def test_ad_edits_keep_fresh_sessions_usage_and_protected_history_private(tmp_path, general):
    store = AdStore(
        AdRouting("ads0", FakeExecutor()), path=tmp_path / "policy",
        lock_path=tmp_path / "lock", active_path=tmp_path / "active",
        status_path=tmp_path / "status", intent_path=tmp_path / "intent",
        panic_guard=provision_guard(tmp_path),
    )

    class Helper(ClientListingHelper):
        def configure_ad_blocking(self, macs, allowances, **kwargs):
            return store.configure(macs, allowances, **kwargs)

        def ad_blocking_status(self):
            return store.status()

        def apply(self, *, plan, explicit_apply=False):
            if explicit_apply:
                store.resume()
            self.plans.append(plan)
            return {"commands_run": 1, "counters_preserved": True}

    with ConfigRepository(tmp_path / "config.db") as repo:
        repo.save_profile(Profile("fresh", "Fresh", random_mode="fresh",
            download=Impairment(delay_ms=100, delay_randomness=Randomness(variation_percent=50)),
            reroll_interval_seconds=100))
        for index, name in enumerate(("alice", "bob", "carol"), 1):
            mac = f"02:00:00:00:00:0{index}"
            repo.save_device(Device(name, name.title(), f"192.168.50.{index + 9}", "fresh", mac))
            if name != "carol":
                repo.save_unaffected_device(UnaffectedDevice(mac, name.title(), 1))
        if general:
            repo.save_general_profile("fresh")
        else:
            repo.save_data_limit(DataLimit("carol", "fresh", 10**9, last_reset_at=100))
        helper = Helper()
        helper.clients = [{"mac_address": device.mac_address,
            "ipv4_address": str(device.ipv4_address), "association_verified": True}
            for device in repo.list_devices()]
        helper.counters = {"devices": {"carol": 0}, "services": {}}
        scheduler = RuntimeScheduler(database_path=tmp_path / "config.db", helper_client=helper)
        scheduler.start = lambda: None
        scheduler.activate(applied_payload={})
        server = ControlServer(("127.0.0.1", 0), repo, helper_client=helper,
            scheduler=scheduler, admin_token="fixture-token")
        try:
            with patch("jittersim.engine.runtime.secrets.randbits", return_value=42) as draw:
                assert scheduler.tick(repository=repo, now=100)
                original = {key: (roll.session_id, roll.seed, roll.roll_number)
                            for key, roll in scheduler._rolls.items()}
                original_events = repo.list_pattern_events(now=100)
                plans_before = len(helper.plans)
                assert "alice" not in original and "bob" not in original
                assert set(original) == ({"@general"} if general else {"carol"})
                assert draw.call_count == 1
                for payload in (
                    {"mac_address": "02:00:00:00:00:01", "enabled": True},
                    {"allowed_domains": ["useful.example.net"]},
                ):
                    assert server.write_response_for("/api/ad-blocking", payload)[0] == 200
                helper.counters["devices"]["carol"] = 1200
                assert not scheduler.tick(repository=repo, now=106)
                assert len(helper.plans) == plans_before
                assert {key: (roll.session_id, roll.seed, roll.roll_number)
                        for key, roll in scheduler._rolls.items()} == original
                assert draw.call_count == 1
                if not general:
                    assert repo.get_data_limit("carol").used_bytes == 1200
                    assert scheduler.device_traffic_bytes(device_id="carol") == 1200
                assert repo.list_pattern_events(now=106) == original_events
                assert repo.list_ad_blocking_devices() == ("02:00:00:00:00:01",)
                assert repo.list_domain_observations() == ()

                store.pause()
                stopped_permission = store.intent_path.read_bytes()
                assert server.write_response_for("/api/ad-blocking", {
                    "allowed_domains": ["another.example.net"],
                })[0] == 200
                scheduler.tick(repository=repo, now=203)
                assert all(roll.roll_number == 1 for roll in scheduler._rolls.values())
                assert store.intent_path.read_bytes() == stopped_permission
                assert store.read()["running"] is False
                assert not {"alice", "bob"} & {event["scope"] for event in repo.list_pattern_events(now=203)}
        finally:
            server.server_close()
            scheduler.stop()


def test_live_pattern_apply_preserves_counter_generation_and_never_resumes_paused_ads(tmp_path):
    guard = provision_guard(tmp_path)
    store = AdStore(
        AdRouting("ads0", FakeExecutor()), path=tmp_path / "policy",
        lock_path=tmp_path / "lock", active_path=tmp_path / "active",
        status_path=tmp_path / "status", intent_path=tmp_path / "intent", panic_guard=guard,
    )
    services = []

    def service(applier):
        result = HelperService(applier, ad_blocking_store=store, panic_guard=guard,
            gateway_state_path=tmp_path / "gateway", active_plan_path=tmp_path / "plan")
        services.append(result)
        return result

    def plan(delay):
        return compile_plan(
            devices=(Device("carol", "Carol", "192.168.50.12", "slow"),),
            profiles={"slow": Profile("slow", "Slow", download=Impairment(delay_ms=delay))},
            protected_mac_addresses=("02:00:00:00:00:01",),
        )

    with serving(service, executor=RootQueueExecutor()) as (client, _):
        client.configure_ad_blocking(["02:00:00:00:00:01"], [], resume=True)
        assert client.apply(plan=plan(100), explicit_apply=True)["counters_preserved"] is False
        assert services[0]._plan_count == 1
        store.pause()
        stopped_permission = store.intent_path.read_bytes()
        assert client.apply(plan=plan(200))["counters_preserved"] is True
        assert services[0]._plan_count == 1
        assert store.intent_path.read_bytes() == stopped_permission
        assert store.read()["running"] is False
        assert client.apply(plan=plan(300), explicit_apply=True)["counters_preserved"] is True
        assert services[0]._plan_count == 1
        assert store.read()["running"] is True

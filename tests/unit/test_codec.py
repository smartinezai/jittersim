import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, MacPolicy, Profile
from jittersim.engine.codec import PlanCodecError, plan_from_payload, plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import ServiceQuotaRule, WebsiteFilter


class PlanCodecTests(unittest.TestCase):
    def setUp(self) -> None:
        profile = Profile(
            id="slow",
            name="Slow",
            upload=Impairment(delay_ms=300),
            download=Impairment(loss_percent=10),
        )
        self.plan = compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "slow"),),
            profiles={"slow": profile},
        )

    def test_round_trips_a_compiled_plan(self) -> None:
        payload = plan_to_payload(self.plan)

        self.assertEqual(plan_from_payload(payload), self.plan)

    def test_round_trips_network_access_rules(self) -> None:
        plan = compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "slow"),),
            profiles={"slow": Profile(id="slow", name="Slow")},
            mac_policy=MacPolicy(mode="denylist", denylist=("aa:bb:cc:dd:ee:ff",)),
            global_blocked_addresses=(IPv4Address("203.0.113.10"),),
            website_filters=(
                WebsiteFilter(
                    device_ip=IPv4Address("192.168.50.10"),
                    allowed_addresses=(IPv4Address("203.0.113.20"),),
                    blocked_addresses=(IPv4Address("203.0.113.30"),),
                ),
            ),
        )

        self.assertEqual(plan_from_payload(plan_to_payload(plan)), plan)

    def test_round_trips_a_website_filter_without_an_allowlist(self) -> None:
        plan = compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "slow"),),
            profiles={"slow": Profile(id="slow", name="Slow")},
            website_filters=(
                WebsiteFilter(
                    device_ip=IPv4Address("192.168.50.10"),
                    allowed_addresses=None,
                    blocked_addresses=(IPv4Address("203.0.113.30"),),
                ),
            ),
        )

        decoded = plan_from_payload(plan_to_payload(plan))

        self.assertEqual(decoded, plan)
        self.assertIsNone(decoded.website_filters[0].allowed_addresses)

    def test_round_trips_service_quota_rules(self) -> None:
        plan = compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "slow"),),
            profiles={"slow": Profile(id="slow", name="Slow")},
            service_quota_rules=(
                ServiceQuotaRule(
                    quota_id="alice-video",
                    device_id="alice",
                    device_ip=IPv4Address("192.168.50.10"),
                    destination_addresses=(IPv4Address("203.0.113.10"),),
                    mark=0,
                    upload_impairment=Impairment(delay_ms=200),
                    download_impairment=Impairment(loss_percent=5),
                    active=True,
                ),
            ),
        )

        self.assertEqual(plan_from_payload(plan_to_payload(plan)), plan)

    def test_rejects_an_unsafe_service_quota_id(self) -> None:
        payload = plan_to_payload(self.plan)
        payload["service_quota_rules"] = [
            {
                "quota_id": "bad\"; drop table inet jittersim; #",
                "device_id": "alice",
                "device_ip": "192.168.50.10",
                "destination_addresses": ["203.0.113.10"],
                "mark": 2,
                "upload_impairment": {"delay_ms": 1},
                "download_impairment": {"delay_ms": 1},
                "active": True,
            }
        ]

        with self.assertRaisesRegex(PlanCodecError, "quota_id is invalid"):
            plan_from_payload(payload)

    def test_rejects_a_service_quota_mark_beyond_the_prio_bands(self) -> None:
        payload = plan_to_payload(self.plan)
        payload["service_quota_rules"] = [
            {
                "quota_id": "video",
                "device_id": "alice",
                "device_ip": "192.168.50.10",
                "destination_addresses": ["203.0.113.10"],
                "mark": 16,
                "upload_impairment": {"delay_ms": 1},
                "download_impairment": {"delay_ms": 1},
                "active": True,
            }
        ]

        with self.assertRaisesRegex(PlanCodecError, "mark is outside the safe range"):
            plan_from_payload(payload)

    def test_rejects_a_non_private_mark_address(self) -> None:
        payload = plan_to_payload(self.plan)
        payload["mark_rules"][0]["ipv4_address"] = "8.8.8.8"

        with self.assertRaisesRegex(PlanCodecError, "mark_rules\[0\] is invalid"):
            plan_from_payload(payload)

    def test_rejects_a_queue_with_the_wrong_mark(self) -> None:
        payload = plan_to_payload(self.plan)
        payload["queues"][0]["mark"] = 2

        with self.assertRaisesRegex(PlanCodecError, "wrong mark"):
            plan_from_payload(payload)

    def test_rejects_missing_directional_queue(self) -> None:
        payload = plan_to_payload(self.plan)
        payload["queues"] = payload["queues"][:1]

        with self.assertRaisesRegex(PlanCodecError, "two queues per device"):
            plan_from_payload(payload)


if __name__ == "__main__":
    unittest.main()

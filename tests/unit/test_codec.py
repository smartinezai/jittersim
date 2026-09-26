import unittest
from copy import deepcopy
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, MacPolicy, Profile
from jittersim.engine.codec import PlanCodecError, plan_from_payload, plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import (
    EVERYONE_ELSE_QUEUE_ID,
    EveryoneElseRule,
    ServiceAssignmentRule,
    ServiceQuotaRule,
    WebsiteFilter,
)


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
        original = deepcopy(payload)

        self.assertEqual(plan_from_payload(payload), self.plan)
        self.assertEqual(payload, original)

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

    def test_round_trips_burst_loss(self) -> None:
        plan = compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "bursty"),),
            profiles={
                "bursty": Profile(
                    id="bursty",
                    name="Bursty",
                    upload=Impairment(loss_percent=10, loss_burst_packets=4),
                )
            },
        )

        decoded = plan_from_payload(plan_to_payload(plan))

        self.assertEqual(decoded, plan)
        self.assertEqual(decoded.queues[0].impairment.loss_burst_packets, 4)

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

    def test_browser_service_rule_cannot_disagree_with_its_queue(self) -> None:
        plan = compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "slow"),),
            profiles={"slow": Profile(id="slow", name="Slow")},
            service_assignment_rules=(
                ServiceAssignmentRule(
                    assignment_id="video",
                    device_id="alice",
                    device_ip=IPv4Address("192.168.50.10"),
                    domains=("youtube.com",),
                    mark=0,
                    upload_impairment=Impairment(delay_ms=200),
                    download_impairment=Impairment(loss_percent=5),
                ),
            ),
        )
        payload = plan_to_payload(plan)
        self.assertEqual(plan_from_payload(payload), plan)
        payload["service_assignment_rules"][0]["upload_impairment"]["delay_ms"] = 400
        with self.assertRaisesRegex(PlanCodecError, "does not match"):
            plan_from_payload(payload)

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


class EveryoneElseCodecTests(unittest.TestCase):
    def plan(self):
        return compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "normal"),),
            profiles={"normal": Profile(id="normal", name="Normal")},
            everyone_else=EveryoneElseRule(
                mark=0,
                upload_impairment=Impairment(delay_ms=100),
                download_impairment=Impairment(loss_percent=5),
                unaffected_macs=("aa:aa:aa:aa:aa:aa",),
                blocked=True,
            ),
        )

    def test_round_trips_the_everyone_else_rule(self) -> None:
        plan = self.plan()

        self.assertEqual(plan_from_payload(plan_to_payload(plan)), plan)

    def test_a_payload_without_everyone_else_has_none(self) -> None:
        payload = plan_to_payload(compile_plan(devices=(), profiles={}))
        del payload["everyone_else"]

        self.assertIsNone(plan_from_payload(payload).everyone_else)

    def test_rejects_an_unsafe_everyone_else_rule(self) -> None:
        changes = {
            "a device's mark": lambda payload: payload["everyone_else"].update(mark=1),
            "past the kernel limit": lambda payload: payload["everyone_else"].update(mark=16),
            "a bad address": lambda payload: payload["everyone_else"].update(
                unaffected_macs=["nope"]
            ),
            "blocked as text": lambda payload: payload["everyone_else"].update(blocked="yes"),
            "a queue that differs": lambda payload: payload["queues"][2]["impairment"].update(
                delay_ms=5
            ),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                payload = plan_to_payload(self.plan())
                change(payload)

                with self.assertRaises(PlanCodecError):
                    plan_from_payload(payload)

    def test_rejects_a_rule_that_shares_a_device_mark(self) -> None:
        # Device marks don't have to be 1..N, so a rule can't assume they are.
        def move_alice_to_mark_15(payload):
            payload["mark_rules"][0]["mark"] = 15
            for queue in payload["queues"]:
                if queue["device_id"] == "alice":
                    queue["mark"] = 15

        assignment = ServiceAssignmentRule(
            assignment_id="yt",
            device_id="alice",
            device_ip=IPv4Address("192.168.50.10"),
            domains=("youtube.com",),
            mark=0,
            upload_impairment=Impairment(delay_ms=50),
            download_impairment=Impairment(),
        )
        cases = {
            "everyone else": (self.plan(), "everyone_else", EVERYONE_ELSE_QUEUE_ID),
            "a browser service profile": (
                compile_plan(
                    devices=(Device("alice", "Alice", "192.168.50.10", "normal"),),
                    profiles={"normal": Profile(id="normal", name="Normal")},
                    service_assignment_rules=(assignment,),
                ),
                "service_assignment_rules",
                "assignment:yt",
            ),
        }
        for name, (plan, key, queue_id) in cases.items():
            with self.subTest(name=name):
                payload = plan_to_payload(plan)
                move_alice_to_mark_15(payload)
                rule = payload[key] if key == "everyone_else" else payload[key][0]
                rule["mark"] = 15
                for queue in payload["queues"]:
                    if queue["device_id"] == queue_id:
                        queue["mark"] = 15

                with self.assertRaises(PlanCodecError):
                    plan_from_payload(payload)

    def test_round_trips_everyone_else_after_every_kind_of_rule(self) -> None:
        plan = compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "normal"),),
            profiles={"normal": Profile(id="normal", name="Normal")},
            service_quota_rules=(
                ServiceQuotaRule(
                    quota_id="video",
                    device_id="alice",
                    device_ip=IPv4Address("192.168.50.10"),
                    destination_addresses=(IPv4Address("203.0.113.10"),),
                    mark=0,
                    upload_impairment=Impairment(),
                    download_impairment=Impairment(bandwidth_kbit=500),
                    active=True,
                ),
            ),
            service_assignment_rules=(
                ServiceAssignmentRule(
                    assignment_id="yt",
                    device_id="alice",
                    device_ip=IPv4Address("192.168.50.10"),
                    domains=("youtube.com",),
                    mark=0,
                    upload_impairment=Impairment(delay_ms=50),
                    download_impairment=Impairment(),
                ),
            ),
            everyone_else=EveryoneElseRule(
                mark=0,
                upload_impairment=Impairment(delay_ms=100),
                download_impairment=Impairment(),
            ),
        )

        self.assertEqual(
            [rule.mark for rule in plan.mark_rules]
            + [rule.mark for rule in plan.service_quota_rules]
            + [rule.mark for rule in plan.service_assignment_rules]
            + [plan.everyone_else.mark],
            [1, 2, 3, 4],
        )
        self.assertEqual(plan_from_payload(plan_to_payload(plan)), plan)

if __name__ == "__main__":
    unittest.main()

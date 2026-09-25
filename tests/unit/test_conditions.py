import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.conditions import DeviceCondition, device_conditions
from jittersim.engine.plan import WebsiteFilter

ALICE = Device("alice", "Alice", "192.168.50.10", "profile")


def plan_with(upload=Impairment(), download=Impairment(), **changes):
    profile = Profile(id="profile", name="Profile", upload=upload, download=download)
    return compile_plan(devices=(ALICE,), profiles={"profile": profile}, **changes)


class DeviceConditionTests(unittest.TestCase):
    def test_a_device_without_effects_is_normal(self) -> None:
        self.assertEqual(device_conditions(plan_with()), {"alice": DeviceCondition("normal")})

    def test_effects_degrade_a_device_and_are_named(self) -> None:
        plan = plan_with(
            upload=Impairment(delay_ms=300, jitter_ms=50, loss_percent=10, loss_burst_packets=4),
            download=Impairment(bandwidth_kbit=2_000, duplicate_percent=1, corrupt_percent=2),
        )

        self.assertEqual(
            device_conditions(plan),
            {
                "alice": DeviceCondition(
                    "degraded",
                    (
                        "upload: delay 300 ms, jitter 50 ms, loss 10% in bursts",
                        "download: limit 2000 kbit/s, duplication 1%, corruption 2%",
                    ),
                )
            },
        )

    def test_reordering_counts_only_with_a_delay(self) -> None:
        # The renderer drops reordering without a delay, so it has no effect.
        plan = plan_with(upload=Impairment(reorder_percent=25))

        self.assertEqual(device_conditions(plan), {"alice": DeviceCondition("normal")})

    def test_outages_and_losing_everything_take_a_device_offline(self) -> None:
        cases = {
            "outage": plan_with(blocked_device_addresses=(ALICE.ipv4_address,)),
            "download: every packet lost": plan_with(download=Impairment(loss_percent=100)),
            "no allowed site resolved": plan_with(
                website_filters=(
                    WebsiteFilter(device_ip=IPv4Address("192.168.50.10"), allowed_addresses=()),
                )
            ),
        }
        for reason, plan in cases.items():
            with self.subTest(reason=reason):
                condition = device_conditions(plan)["alice"]
                self.assertEqual(condition.condition, "offline")
                self.assertIn(reason, condition.reasons)

    def test_blocked_sites_alone_keep_a_device_normal(self) -> None:
        plan = plan_with(
            website_filters=(
                WebsiteFilter(
                    device_ip=IPv4Address("192.168.50.10"),
                    allowed_addresses=None,
                    blocked_addresses=(IPv4Address("203.0.113.9"),),
                ),
            )
        )

        self.assertEqual(device_conditions(plan)["alice"].condition, "normal")


if __name__ == "__main__":
    unittest.main()

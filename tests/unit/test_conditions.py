import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, MacPolicy, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.conditions import DeviceCondition, device_conditions
from jittersim.engine.plan import ServiceAssignmentRule, ServiceQuotaRule, WebsiteFilter

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

    def test_a_mac_policy_that_drops_a_device_takes_it_offline(self) -> None:
        mac = "aa:bb:cc:dd:ee:ff"
        cases = {
            "MAC address denied": MacPolicy(mode="denylist", denylist=(mac,)),
            "MAC address not allowed": MacPolicy(
                mode="allowlist", allowlist=("11:22:33:44:55:66",)
            ),
        }
        for reason, policy in cases.items():
            with self.subTest(reason=reason):
                plan = plan_with(mac_policy=policy)

                condition = device_conditions(plan, mac_addresses={"alice": mac})["alice"]

                self.assertEqual(condition, DeviceCondition("offline", (reason,)))

    def test_service_rules_with_effects_degrade_part_of_a_device(self) -> None:
        def quota(quota_id, *, active, download):
            return ServiceQuotaRule(
                quota_id=quota_id,
                device_id="alice",
                device_ip=ALICE.ipv4_address,
                destination_addresses=(IPv4Address("203.0.113.10"),),
                mark=0,
                upload_impairment=Impairment(),
                download_impairment=download,
                active=active,
            )

        plan = plan_with(
            service_quota_rules=(
                quota("video", active=True, download=Impairment(loss_percent=100)),
                quota("music", active=False, download=Impairment(delay_ms=50)),
            ),
            service_assignment_rules=(
                ServiceAssignmentRule(
                    assignment_id="yt",
                    device_id="alice",
                    device_ip=ALICE.ipv4_address,
                    domains=("youtube.com",),
                    mark=0,
                    upload_impairment=Impairment(delay_ms=300),
                    download_impairment=Impairment(),
                ),
            ),
        )

        # They shape only some of the device's traffic, so it stays online.
        self.assertEqual(
            device_conditions(plan)["alice"],
            DeviceCondition(
                "degraded",
                (
                    "service limit video, download: loss 100%",
                    "browser service profile yt, upload: delay 300 ms",
                ),
            ),
        )

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

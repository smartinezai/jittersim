import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, MacPolicy, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import (
    GeneralProfileRule,
    ServiceAssignmentRule,
    ServiceListRule,
    WebsiteFilter,
)
from jittersim.networking.browser_proxy import ProxyError, destination_allowed, select_mark

SLOW = Profile(id="slow", name="Slow", upload=Impairment(delay_ms=100))
ALICE = IPv4Address("192.168.50.10")
STRANGER = IPv4Address("192.168.50.77")
PROTECTED = "02:00:00:00:00:01"
AFFECTED = "02:00:00:00:00:02"


def listed(list_id, *domains):
    return ServiceListRule(list_id, domains, 0, SLOW.upload, SLOW.download)


def device_plan(**kwargs):
    return compile_plan(
        devices=(Device("alice", "Alice", str(ALICE), "slow"),),
        profiles={"slow": SLOW},
        service_list_rules=(listed("streaming", "netflix.com"),),
        **kwargs,
    )


class ServiceListProxyTests(unittest.TestCase):
    def test_listed_hosts_and_their_subdomains_use_the_list_queue(self) -> None:
        plan = device_plan()

        for host in ("netflix.com", "www.netflix.com", "a.b.netflix.com"):
            self.assertEqual(
                select_mark(plan, client_ip=ALICE, host=host), (2, "service-list:streaming")
            )

    def test_other_hosts_keep_the_device_queue_and_lookalikes_do_not_match(self) -> None:
        plan = device_plan()

        for host in ("example.com", "notnetflix.com", "netflix.com.evil.example"):
            self.assertEqual(select_mark(plan, client_ip=ALICE, host=host), (1, "device"))

    def test_a_list_beats_a_browser_service_profile_for_the_same_host(self) -> None:
        plan = compile_plan(
            devices=(Device("alice", "Alice", str(ALICE), "slow"),),
            profiles={"slow": SLOW},
            service_assignment_rules=(
                ServiceAssignmentRule(
                    "video", "alice", ALICE, ("netflix.com",), 0, SLOW.upload, SLOW.download
                ),
            ),
            service_list_rules=(listed("streaming", "netflix.com"),),
        )

        self.assertEqual(
            select_mark(plan, client_ip=ALICE, host="netflix.com"), (3, "service-list:streaming")
        )

    def test_a_client_without_a_saved_device_still_gets_the_list_profile(self) -> None:
        plan = device_plan()

        self.assertEqual(
            select_mark(plan, client_ip=STRANGER, host="netflix.com"), (2, "service-list:streaming")
        )
        with self.assertRaisesRegex(ProxyError, "assigned device"):
            select_mark(plan, client_ip=STRANGER, host="example.com")

    def test_the_list_choice_ignores_the_devices_outage_flag(self) -> None:
        plan = device_plan(blocked_device_addresses=(ALICE,))

        self.assertEqual(
            select_mark(plan, client_ip=ALICE, host="netflix.com"), (2, "service-list:streaming")
        )
        with self.assertRaisesRegex(ProxyError, "disconnected"):
            select_mark(plan, client_ip=ALICE, host="example.com")

    def test_protected_clients_are_never_shaped(self) -> None:
        plan = device_plan(protected_mac_addresses=(PROTECTED,))

        self.assertEqual(
            select_mark(plan, client_ip=ALICE, host="netflix.com", client_mac=PROTECTED),
            (0, "unaffected"),
        )
        self.assertEqual(
            select_mark(plan, client_ip=ALICE, host="netflix.com", client_mac=AFFECTED),
            (2, "service-list:streaming"),
        )
        with self.assertRaisesRegex(ProxyError, "verified"):
            select_mark(plan, client_ip=ALICE, host="netflix.com")

    def test_global_access_rules_still_block_listed_hosts(self) -> None:
        plan = device_plan(
            protected_mac_addresses=(PROTECTED,),
            mac_policy=MacPolicy(mode="denylist", denylist=(AFFECTED,)),
        )

        with self.assertRaisesRegex(ProxyError, "global access rules"):
            select_mark(plan, client_ip=ALICE, host="netflix.com", client_mac=AFFECTED)

    def test_inactive_saved_mac_lists_do_not_block_browser_traffic(self) -> None:
        for general in (False, True):
            for client_mac in (AFFECTED, PROTECTED):
                for mode in ("disabled", "allowlist"):
                    policy = MacPolicy(
                        mode=mode, allowlist=(client_mac,), denylist=(client_mac,)
                    )
                    if general:
                        plan = compile_plan(
                            devices=(), profiles={"slow": SLOW},
                            protected_mac_addresses=(PROTECTED,),
                            general_profile=GeneralProfileRule("slow"), mac_policy=policy,
                        )
                    else:
                        plan = device_plan(
                            protected_mac_addresses=(PROTECTED,), mac_policy=policy
                        )
                    expected = ((0, "unaffected") if client_mac == PROTECTED else
                                (1, "general-profile" if general else "device"))
                    with self.subTest(general=general, client_mac=client_mac, mode=mode):
                        self.assertEqual(
                            select_mark(plan, client_ip=ALICE, host="example.com",
                                        client_mac=client_mac),
                            expected,
                        )

    def test_active_mac_rules_still_block_protected_browser_clients(self) -> None:
        policies = (
            MacPolicy(mode="denylist", denylist=(PROTECTED,)),
            MacPolicy(mode="allowlist", allowlist=(AFFECTED,)),
        )
        for policy in policies:
            plan = device_plan(protected_mac_addresses=(PROTECTED,), mac_policy=policy)
            with self.subTest(mode=policy.mode):
                with self.assertRaisesRegex(ProxyError, "global access rules"):
                    select_mark(plan, client_ip=ALICE, host="example.com", client_mac=PROTECTED)

    def test_the_general_profile_gives_way_to_a_list_even_during_its_outage(self) -> None:
        for outage in (False, True):
            plan = compile_plan(
                devices=(),
                profiles={"slow": SLOW},
                protected_mac_addresses=(PROTECTED,),
                general_profile=GeneralProfileRule("slow", outage_active=outage),
                service_list_rules=(listed("streaming", "netflix.com"),),
            )
            with self.subTest(outage=outage):
                self.assertEqual(
                    select_mark(plan, client_ip=STRANGER, host="netflix.com", client_mac=AFFECTED),
                    (2, "service-list:streaming"),
                )
                if outage:
                    with self.assertRaisesRegex(ProxyError, "temporarily disconnected"):
                        select_mark(
                            plan, client_ip=STRANGER, host="example.com", client_mac=AFFECTED
                        )
                else:
                    self.assertEqual(
                        select_mark(
                            plan, client_ip=STRANGER, host="example.com", client_mac=AFFECTED
                        ),
                        (1, "general-profile"),
                    )


class ListedDestinationTests(unittest.TestCase):
    """A listed host skips the profile's own website rules and outage but not global rules."""

    DESTINATION = "5.5.5.5"

    def test_the_general_outage_and_allowlist_do_not_block_a_listed_host(self) -> None:
        plan = compile_plan(
            devices=(),
            profiles={"slow": SLOW},
            general_profile=GeneralProfileRule("slow", (IPv4Address("1.1.1.1"),), True),
            service_list_rules=(listed("streaming", "netflix.com"),),
        )

        self.assertFalse(destination_allowed(plan, STRANGER, self.DESTINATION, AFFECTED))
        self.assertTrue(
            destination_allowed(plan, STRANGER, self.DESTINATION, AFFECTED, service_list=True)
        )

    def test_the_devices_website_rules_do_not_block_a_listed_host(self) -> None:
        plan = device_plan(
            website_filters=(
                WebsiteFilter(ALICE, (IPv4Address("1.1.1.1"),), (IPv4Address(self.DESTINATION),)),
            )
        )

        self.assertFalse(destination_allowed(plan, ALICE, self.DESTINATION))
        self.assertTrue(destination_allowed(plan, ALICE, self.DESTINATION, service_list=True))

    def test_global_rules_still_block_a_listed_host(self) -> None:
        blocked = device_plan(global_blocked_addresses=(IPv4Address(self.DESTINATION),))
        allowlisted = device_plan(global_allowed_addresses=(IPv4Address("1.1.1.1"),))

        for plan in (blocked, allowlisted):
            self.assertFalse(destination_allowed(plan, ALICE, self.DESTINATION, service_list=True))


if __name__ == "__main__":
    unittest.main()

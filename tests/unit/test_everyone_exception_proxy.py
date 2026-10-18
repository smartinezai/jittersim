"""The browser proxy follows the same split as the kernel rules: the exception is its own class."""

import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import GeneralProfileRule, WebsiteFilter
from jittersim.networking.browser_proxy import ProxyError, destination_allowed, select_mark

EVERYONE = Profile(id="everyone", name="Everyone", upload=Impairment(delay_ms=40))
OWN = Profile(id="own", name="Own", upload=Impairment(delay_ms=900))
AIR = IPv4Address("192.168.50.21")
OTHER = IPv4Address("192.168.50.77")
MAC = "02:00:00:00:00:02"


def plan(general=None, **kwargs):
    return compile_plan(
        devices=(Device("air", "Air", str(AIR), "own"),),
        profiles={"everyone": EVERYONE, "own": OWN},
        general_profile=general or GeneralProfileRule("everyone"),
        **kwargs,
    )


class ExceptionProxyTests(unittest.TestCase):
    def test_the_exception_uses_its_own_queue_and_everyone_else_the_shared_one(self) -> None:
        mixed = plan()

        self.assertEqual(select_mark(mixed, client_ip=AIR, host="example.com", client_mac=MAC),
                         (2, "device"))
        self.assertEqual(select_mark(mixed, client_ip=OTHER, host="example.com", client_mac=MAC),
                         (1, "general-profile"))

    def test_an_everyone_outage_does_not_cut_the_exception(self) -> None:
        mixed = plan(GeneralProfileRule("everyone", outage_active=True))

        self.assertEqual(select_mark(mixed, client_ip=AIR, host="example.com", client_mac=MAC),
                         (2, "device"))
        with self.assertRaises(ProxyError):
            select_mark(mixed, client_ip=OTHER, host="example.com", client_mac=MAC)

    def test_everyones_allowlist_does_not_apply_to_the_exception(self) -> None:
        allowed = IPv4Address("203.0.113.20")
        elsewhere = "198.51.100.9"
        mixed = plan(GeneralProfileRule("everyone", allowed_addresses=(allowed,)))

        self.assertTrue(destination_allowed(mixed, AIR, elsewhere, MAC))
        self.assertFalse(destination_allowed(mixed, OTHER, elsewhere, MAC))
        self.assertTrue(destination_allowed(mixed, OTHER, str(allowed), MAC))

    def test_the_exceptions_own_website_rules_apply_to_it_alone(self) -> None:
        blocked = IPv4Address("203.0.113.66")
        mixed = plan(website_filters=(WebsiteFilter(AIR, None, (blocked,)),))

        self.assertFalse(destination_allowed(mixed, AIR, str(blocked), MAC))
        self.assertTrue(destination_allowed(mixed, OTHER, str(blocked), MAC))


if __name__ == "__main__":
    unittest.main()

import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Profile, WebsitePolicy
from jittersim.engine.website import build_website_filters, resolve_domains


def fake_resolver(domain, _port, _family, _kind):
    values = {
        "example.com": "203.0.113.10",
        "allowed.example": "203.0.113.20",
        "blocked.example": "203.0.113.30",
    }
    if domain not in values:
        raise OSError("not found")
    return ((None, None, None, None, (values[domain], 0)),)


class WebsiteResolutionTests(unittest.TestCase):
    def test_skips_unresolvable_domains_and_deduplicates_addresses(self) -> None:
        addresses = resolve_domains(
            domains=("example.com", "example.com", "missing.example"),
            resolver=fake_resolver,
        )

        self.assertEqual(addresses, (IPv4Address("203.0.113.10"),))

    def test_builds_global_and_profile_filters(self) -> None:
        device = Device("alice", "Alice", "192.168.50.10", "slow")
        profile = Profile(
            id="slow",
            name="Slow",
            website_mode="allow_only",
            website_domains=("allowed.example",),
        )

        blocked, filters = build_website_filters(
            devices=(device,),
            profiles={"slow": profile},
            global_policy=WebsitePolicy(blocked_domains=("blocked.example",)),
            resolver=fake_resolver,
        )

        self.assertEqual(blocked, (IPv4Address("203.0.113.30"),))
        self.assertEqual(filters[0].allowed_addresses, (IPv4Address("203.0.113.20"),))


if __name__ == "__main__":
    unittest.main()

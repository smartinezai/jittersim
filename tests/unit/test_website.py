import unittest
from ipaddress import IPv4Address
from unittest.mock import patch

from jittersim.config.models import Device, Profile, WebsitePolicy
from jittersim.engine import website
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
    def setUp(self) -> None:
        with website._resolution_cache_lock:
            website._resolution_cache.clear()

    def tearDown(self) -> None:
        with website._resolution_cache_lock:
            website._resolution_cache.clear()

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
            blocked_domains_by_device={"alice": ("blocked.example",)},
            resolver=fake_resolver,
        )

        self.assertEqual(blocked, (IPv4Address("203.0.113.30"),))
        self.assertEqual(filters[0].allowed_addresses, (IPv4Address("203.0.113.20"),))
        self.assertEqual(filters[0].blocked_addresses, (IPv4Address("203.0.113.30"),))

    def test_default_resolution_is_cached_until_the_short_ttl_expires(self) -> None:
        result = ((None, None, None, None, ("203.0.113.10", 0)),)
        with patch("jittersim.engine.website.socket.getaddrinfo", return_value=result) as lookup:
            with patch("jittersim.engine.website.time.monotonic", return_value=10.0):
                resolve_domains(domains=("example.com",))
                resolve_domains(domains=("example.com",))
            self.assertEqual(lookup.call_count, 1)

            with patch("jittersim.engine.website.time.monotonic", return_value=71.0):
                resolve_domains(domains=("example.com",))
            self.assertEqual(lookup.call_count, 2)

    def test_default_resolution_cache_has_a_fixed_size(self) -> None:
        result = ((None, None, None, None, ("203.0.113.10", 0)),)
        with patch("jittersim.engine.website.socket.getaddrinfo", return_value=result):
            with patch("jittersim.engine.website.time.monotonic", return_value=10.0):
                for index in range(website._MAX_CACHED_DOMAINS + 1):
                    resolve_domains(domains=(f"host{index}.example",))

        self.assertEqual(len(website._resolution_cache), website._MAX_CACHED_DOMAINS)


if __name__ == "__main__":
    unittest.main()

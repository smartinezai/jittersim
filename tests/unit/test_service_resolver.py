import threading
import unittest
from ipaddress import IPv4Address

from jittersim.engine.service_resolver import (
    MAX_SEED_HOSTNAMES_PER_LIST,
    RESEND_SECONDS,
    ServiceAddressRefresher,
)
from jittersim.networking.helper import HelperClientError


def ip(value):
    return IPv4Address(value)


class Helper:
    def __init__(self) -> None:
        self.calls = []
        self.error = None

    def service_addresses(self, addresses):
        if self.error is not None:
            raise self.error
        self.calls.append(addresses)
        return sum(len(values) for values in addresses.values())


class Clock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self):
        return self.now


def payload(*lists):
    return {
        "service_list_rules": [
            {"list_id": list_id, "domains": list(domains)} for list_id, domains in lists
        ]
    }


class RefresherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = Helper()
        self.clock = Clock()
        self.dns = {
            "netflix.com": [ip("203.0.113.1"), ip("203.0.113.2")],
            "nflxvideo.net": [ip("203.0.113.2"), ip("203.0.113.3")],
            "claude.ai": [ip("198.51.100.7")],
        }
        self.applied = payload(
            ("streaming", ["netflix.com", "nflxvideo.net"]), ("ai", ["claude.ai"])
        )
        self.refresher = ServiceAddressRefresher(
            applied_plan=lambda: self.applied,
            helper_client=self.helper,
            resolver=lambda domain: self.dns.get(domain, ()),
            clock=self.clock,
        )

    def test_sends_each_lists_resolved_addresses_once(self) -> None:
        self.assertTrue(self.refresher.refresh())

        (call,) = self.helper.calls
        self.assertEqual(
            call,
            {
                "streaming": [ip("203.0.113.1"), ip("203.0.113.2"), ip("203.0.113.3")],
                "ai": [ip("198.51.100.7")],
            },
        )

    def test_unchanged_answers_are_not_resent_until_the_refresh_interval(self) -> None:
        self.refresher.refresh()

        self.assertFalse(self.refresher.refresh())
        self.clock.now += RESEND_SECONDS - 1
        self.assertFalse(self.refresher.refresh())
        self.clock.now += 2
        self.assertTrue(self.refresher.refresh())
        self.assertEqual(len(self.helper.calls), 2)

    def test_new_answers_are_sent_straight_away(self) -> None:
        self.refresher.refresh()
        self.dns["claude.ai"] = [ip("198.51.100.8")]

        self.assertTrue(self.refresher.refresh())
        self.assertEqual(self.helper.calls[-1]["ai"], [ip("198.51.100.8")])

    def test_every_hostname_a_list_can_hold_is_resolved(self) -> None:
        from jittersim.config.models import MAX_SERVICE_LIST_DOMAINS

        self.assertGreaterEqual(MAX_SEED_HOSTNAMES_PER_LIST, MAX_SERVICE_LIST_DOMAINS)

    def test_a_reapplied_plan_is_sent_again_because_its_sets_started_over(self) -> None:
        self.refresher.refresh()
        self.assertFalse(self.refresher.refresh())

        self.applied = payload(
            ("streaming", ["netflix.com", "nflxvideo.net"]), ("ai", ["claude.ai"])
        )

        self.assertTrue(self.refresher.refresh())
        self.assertEqual(len(self.helper.calls), 2)

    def test_a_changed_plan_is_resolved_straight_away(self) -> None:
        self.refresher.refresh()
        self.applied = payload(("streaming", ["netflix.com"]))

        self.assertTrue(self.refresher.refresh())
        self.assertEqual(set(self.helper.calls[-1]), {"streaming"})

    def test_nothing_is_sent_without_lists_or_answers(self) -> None:
        self.applied = None
        self.assertFalse(self.refresher.refresh())
        self.applied = payload()
        self.assertFalse(self.refresher.refresh())
        self.applied = payload(("x", ["unknown.example"]))
        self.assertFalse(self.refresher.refresh())
        self.assertEqual(self.helper.calls, [])

    def test_only_the_first_hostnames_of_a_list_are_resolved(self) -> None:
        seen = []
        names = [f"h{number}.example" for number in range(MAX_SEED_HOSTNAMES_PER_LIST + 10)]
        self.applied = payload(("big", names))
        refresher = ServiceAddressRefresher(
            applied_plan=lambda: self.applied,
            helper_client=self.helper,
            resolver=lambda domain: seen.append(domain) or (),
            clock=self.clock,
        )

        refresher.refresh()

        self.assertEqual(sorted(seen), sorted(names[:MAX_SEED_HOSTNAMES_PER_LIST]))

    def test_a_failing_resolver_for_one_name_does_not_stop_the_others(self) -> None:
        def resolver(domain):
            if domain == "netflix.com":
                raise OSError("dns down")
            return self.dns.get(domain, ())

        refresher = ServiceAddressRefresher(
            applied_plan=lambda: self.applied,
            helper_client=self.helper,
            resolver=resolver,
            clock=self.clock,
        )

        self.assertTrue(refresher.refresh())
        self.assertEqual(self.helper.calls[0]["streaming"], [ip("203.0.113.2"), ip("203.0.113.3")])

    def test_a_busy_or_missing_helper_is_retried_on_the_next_round(self) -> None:
        self.helper.error = HelperClientError("busy", reason="helper-busy")
        self.assertFalse(self.refresher.refresh())

        self.helper.error = None
        self.assertTrue(self.refresher.refresh())

    def test_the_background_loop_refreshes_and_stops_cleanly(self) -> None:
        sent = threading.Event()
        original = self.helper.service_addresses
        self.helper.service_addresses = lambda addresses: (original(addresses), sent.set())[0]
        refresher = ServiceAddressRefresher(
            applied_plan=lambda: self.applied,
            helper_client=self.helper,
            resolver=lambda domain: self.dns.get(domain, ()),
            clock=self.clock,
            poll_seconds=0.01,
        )

        refresher.start()
        try:
            self.assertTrue(sent.wait(2))
        finally:
            refresher.stop()

        self.assertFalse(refresher.running)


if __name__ == "__main__":
    unittest.main()

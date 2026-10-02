import threading
import time
import unittest
from ipaddress import IPv4Address
from unittest.mock import patch

from jittersim.config.models import Device, Profile, WebsitePolicy
from jittersim.config.storage import ConfigRepository
from jittersim.engine import website
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.service import compile_stored_plan
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

    def test_blocked_domains_alone_do_not_create_an_allowlist(self) -> None:
        device = Device("alice", "Alice", "192.168.50.10", "normal")

        _, filters = build_website_filters(
            devices=(device,),
            profiles={"normal": Profile(id="normal", name="Normal")},
            global_policy=WebsitePolicy(),
            blocked_domains_by_device={"alice": ("blocked.example",)},
            resolver=fake_resolver,
        )

        self.assertIsNone(filters[0].allowed_addresses)
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


ADDRESSES = {
    "example.com": "203.0.113.10",
    "allowed.example": "203.0.113.20",
    "blocked.example": "203.0.113.30",
    "slow.example": "203.0.113.40",
    "other.example": "203.0.113.50",
}


def address_of(domain):
    if domain.startswith("host"):
        return "203.0.113." + domain[4 : domain.index(".")]
    return ADDRESSES[domain]


class SlowLookups:
    """A resolver that answers at once except for the names it holds back."""

    def __init__(self, slow=("slow.example",), delay=0.0) -> None:
        self.slow = set(slow)
        self.delay = delay
        self.release = threading.Event()
        self.calls = []
        self.running = 0
        self.most_running = 0
        self._lock = threading.Lock()

    def __call__(self, domain, _port, _family, _kind):
        with self._lock:
            self.calls.append(domain)
            self.running += 1
            self.most_running = max(self.most_running, self.running)
        try:
            if domain in self.slow:
                self.release.wait(timeout=10)
            if self.delay:
                time.sleep(self.delay)
            return ((None, None, None, None, (address_of(domain), 0)),)
        finally:
            with self._lock:
                self.running -= 1


class LookupDeadlineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._reset_shared_state()
        self.addCleanup(self._reset_shared_state)
        patcher = patch.object(website, "RESOLVE_WAIT_SECONDS", 0.3)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lookups = SlowLookups()
        # Held back lookups must not outlive the test.
        self.addCleanup(self.lookups.release.set)

    @staticmethod
    def _reset_shared_state() -> None:
        with website._resolution_cache_lock:
            website._resolution_cache.clear()
            website._pending.clear()
            website._recent_timeouts.clear()
            website._reported.clear()


class WebsiteLookupDeadlineTests(LookupDeadlineTestCase):
    def test_a_name_that_does_not_answer_in_time_does_not_hold_back_the_others(self) -> None:
        started = time.monotonic()
        with website.lookup_session() as session:
            addresses = resolve_domains(
                domains=("example.com", "slow.example", "allowed.example"),
                resolver=self.lookups,
            )

        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(addresses, (IPv4Address("203.0.113.10"), IPv4Address("203.0.113.20")))
        self.assertEqual(session.timed_out, ["slow.example"])

    def test_a_lookup_outside_a_plan_session_is_bounded_too(self) -> None:
        started = time.monotonic()

        addresses = resolve_domains(domains=("slow.example", "example.com"), resolver=self.lookups)

        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(addresses, (IPv4Address("203.0.113.10"),))

    def test_every_lookup_of_one_plan_shares_the_same_deadline(self) -> None:
        self.lookups.slow.add("other.example")
        started = time.monotonic()
        with website.lookup_session() as session:
            first = resolve_domains(domains=("slow.example", "example.com"), resolver=self.lookups)
            used = time.monotonic() - started
            second = resolve_domains(domains=("other.example", "allowed.example"), resolver=self.lookups)

        # The budget is gone after the first call, so the second looks nothing up and waits
        # for nothing. Names it did not get to are listed with the ones that were too slow.
        self.assertGreater(used, 0.25)
        self.assertLess(time.monotonic() - started - used, 0.25)
        self.assertEqual(first, (IPv4Address("203.0.113.10"),))
        self.assertEqual(second, ())
        self.assertEqual(session.timed_out, ["slow.example", "other.example", "allowed.example"])
        self.assertNotIn("other.example", self.lookups.calls)
        self.assertNotIn("allowed.example", self.lookups.calls)

    def test_at_most_a_fixed_number_of_lookups_run_at_once(self) -> None:
        names = tuple(f"host{number}.example" for number in range(1, 13))
        lookups = SlowLookups(slow=(), delay=0.05)
        with patch.object(website, "RESOLVE_WAIT_SECONDS", 5.0):
            with patch.object(website, "RESOLVER_THREADS", 3):
                addresses = resolve_domains(domains=names, resolver=lookups)

        self.assertEqual(len(addresses), 12)
        self.assertLessEqual(lookups.most_running, 3)

    def test_a_session_inside_a_session_joins_the_outer_one(self) -> None:
        with website.lookup_session() as outer:
            with website.lookup_session() as inner:
                self.assertIs(inner, outer)

    def test_the_default_lookup_keeps_a_late_answer_and_does_not_repeat_the_wait(self) -> None:
        calls = []
        release = threading.Event()
        self.addCleanup(release.set)

        def getaddrinfo(domain, port, family, kind):
            calls.append(domain)
            if domain == "slow.example":
                release.wait(timeout=10)
            return ((None, None, None, None, (address_of(domain), 0)),)

        with patch("jittersim.engine.website.socket.getaddrinfo", side_effect=getaddrinfo):
            with website.lookup_session() as first:
                found = resolve_domains(domains=("slow.example", "example.com"))
            self.assertEqual(found, (IPv4Address("203.0.113.10"),))
            self.assertEqual(first.timed_out, ["slow.example"])

            # A name that just timed out is not waited for again and not looked up again.
            started = time.monotonic()
            with website.lookup_session() as second:
                self.assertEqual(resolve_domains(domains=("slow.example",)), ())
            self.assertLess(time.monotonic() - started, 0.25)
            self.assertEqual(second.timed_out, ["slow.example"])
            # Even after that memory ends, the lookup still running is joined and not repeated.
            with website._resolution_cache_lock:
                website._recent_timeouts.clear()
            with website.lookup_session():
                resolve_domains(domains=("slow.example",))
            self.assertEqual(calls.count("slow.example"), 1)

            # When the held back lookup ends its answer fills the cache for the next plan.
            release.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with website._resolution_cache_lock:
                    if "slow.example" in website._resolution_cache:
                        break
                time.sleep(0.01)
            with website.lookup_session() as third:
                late = resolve_domains(domains=("slow.example",))

        self.assertEqual(late, (IPv4Address("203.0.113.40"),))
        self.assertEqual(third.timed_out, [])
        self.assertEqual(calls.count("slow.example"), 1)

    def test_website_filters_keep_the_resolved_names_when_one_times_out(self) -> None:
        device = Device("alice", "Alice", "192.168.50.10", "slow")
        profile = Profile(
            id="slow",
            name="Slow",
            website_mode="allow_only",
            website_domains=("allowed.example", "slow.example"),
        )

        with website.lookup_session() as session:
            blocked, filters = build_website_filters(
                devices=(device,),
                profiles={"slow": profile},
                global_policy=WebsitePolicy(blocked_domains=("blocked.example",)),
                resolver=self.lookups,
            )

        self.assertEqual(blocked, (IPv4Address("203.0.113.30"),))
        self.assertEqual(filters[0].allowed_addresses, (IPv4Address("203.0.113.20"),))
        self.assertEqual(session.timed_out, ["slow.example"])

    def test_the_names_that_timed_out_are_logged_once_in_plain_words(self) -> None:
        self.lookups.slow.add("other.example")
        with self.assertLogs("jittersim.engine.website", "WARNING") as logs:
            for _ in range(3):
                with website.lookup_session():
                    resolve_domains(domains=("slow.example",), resolver=self.lookups)
                # Another thread builds a plan that times out on nothing in between.
                with website.lookup_session():
                    resolve_domains(domains=("example.com",), resolver=self.lookups)
            self.assertEqual(len(logs.records), 1)
            with website.lookup_session():
                resolve_domains(domains=("slow.example", "other.example"), resolver=self.lookups)
            self.assertEqual(len(logs.records), 2)
            # The same names are logged again after ten minutes.
            website._reported[("slow.example",)] -= 601
            with website.lookup_session():
                resolve_domains(domains=("slow.example",), resolver=self.lookups)
            self.assertEqual(len(logs.records), 3)
            logs.records.pop()

        first = logs.records[0].getMessage()
        self.assertIn("slow.example", first)
        self.assertIn("could not be looked up in time", first)
        second = logs.records[1].getMessage()
        self.assertIn("other.example", second)
        self.assertIn("slow.example", second)

    def test_a_plan_that_looks_everything_up_in_time_logs_nothing(self) -> None:
        with patch.object(website.LOGGER, "warning") as warning:
            with website.lookup_session():
                resolve_domains(domains=("example.com", "allowed.example"), resolver=self.lookups)

        warning.assert_not_called()

    def test_the_names_are_described_in_plain_words(self) -> None:
        self.assertEqual(
            website.describe_timed_out_names(("a.example", "b.example")),
            "a.example, b.example",
        )
        many = tuple(f"host{number}.example" for number in range(1, 13))
        text = website.describe_timed_out_names(many)
        self.assertIn("host1.example", text)
        self.assertNotIn("host12.example", text)
        self.assertTrue(text.endswith("and 4 more"))


class PlanLookupTests(LookupDeadlineTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(
            Profile(
                id="strict",
                name="Strict",
                website_mode="allow_only",
                website_domains=("allowed.example", "slow.example"),
            )
        )
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "strict"))

        def cached(*, domain):
            if domain == "slow.example":
                self.lookups.release.wait(timeout=10)
            return (IPv4Address(address_of(domain)),)

        patcher = patch("jittersim.engine.website._cached_addresses", side_effect=cached)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_scheduler_plan_keeps_the_names_that_resolved_and_names_the_others(self) -> None:
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=None)
        started = time.monotonic()

        with self.assertLogs("jittersim.engine.website", "WARNING") as logs:
            prepared = scheduler.prepare_plan(repository=self.repository, now=100)

        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(prepared.unresolved_names, ("slow.example",))
        self.assertEqual(
            prepared.plan.website_filters[0].allowed_addresses,
            (IPv4Address("203.0.113.20"),),
        )
        self.assertIn("slow.example", logs.output[0])

    def test_a_plan_without_timeouts_has_no_unresolved_names(self) -> None:
        self.lookups.release.set()
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=None)

        prepared = scheduler.prepare_plan(repository=self.repository, now=100)

        self.assertEqual(prepared.unresolved_names, ())

    def test_the_stored_plan_is_bounded_too(self) -> None:
        started = time.monotonic()

        with self.assertLogs("jittersim.engine.website", "WARNING"):
            plan = compile_stored_plan(repository=self.repository)

        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(plan.website_filters[0].allowed_addresses, (IPv4Address("203.0.113.20"),))


if __name__ == "__main__":
    unittest.main()

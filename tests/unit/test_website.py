import threading
from concurrent.futures import ThreadPoolExecutor
import time
import unittest
from ipaddress import IPv4Address
from unittest.mock import patch

from jittersim.config.models import MAX_GLOBAL_BLOCKED_DOMAINS, Device, Profile, WebsitePolicy
from jittersim.config.storage import ConfigRepository
from jittersim.engine import website
from jittersim.engine.lookup_pool import BoundedLookupPool
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.service import compile_stored_plan
from jittersim.engine.website import build_website_filters, resolve_domains


class FakeClock:
    """Stands in for the time module inside website.py only."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


def numbered_answer(name, port, *_):
    """A distinct address for each name, after a short lookup delay like a real resolver."""
    time.sleep(0.001)
    number = int(name.split(".")[0][1:]) if name.startswith("h") else 65535
    return ((None, None, None, None, (f"10.9.{number >> 8}.{number & 255}", port)),)


def wait_for_renewals() -> None:
    """Wait for the background lookups that renew expired answers."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with website._resolution_cache_lock:
            running = list(website._pending.values())
        if not running:
            return
        for future in running:
            future.result(timeout=5)
        # Future waiters can wake before its pending-removal callback runs.
        time.sleep(0.001)
    raise AssertionError("background renewals did not finish")


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
            website._recent_timeouts.clear()

    def tearDown(self) -> None:
        with website._resolution_cache_lock:
            website._resolution_cache.clear()
            website._recent_timeouts.clear()

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
                wait_for_renewals()
            self.assertEqual(lookup.call_count, 2)

    def test_an_expired_answer_is_used_at_once_while_it_is_renewed(self) -> None:
        old = ((None, None, None, None, ("203.0.113.10", 0)),)
        new = ((None, None, None, None, ("203.0.113.11", 0)),)
        renewal = threading.Event()

        def slow_new_answer(*arguments):
            renewal.wait(5)
            return new

        with patch("jittersim.engine.website.socket.getaddrinfo", return_value=old):
            with patch("jittersim.engine.website.time.monotonic", return_value=10.0):
                resolve_domains(domains=("example.com",))
        with patch("jittersim.engine.website.socket.getaddrinfo", side_effect=slow_new_answer):
            with patch("jittersim.engine.website.time.monotonic", return_value=71.0):
                started = time.perf_counter()
                self.assertEqual(
                    resolve_domains(domains=("example.com",)), (IPv4Address("203.0.113.10"),)
                )
                # The plan did not wait for the renewal.
                self.assertLess(time.perf_counter() - started, 1.0)
                renewal.set()
                wait_for_renewals()
                self.assertEqual(
                    resolve_domains(domains=("example.com",)), (IPv4Address("203.0.113.11"),)
                )

    def test_concurrent_renewals_leave_a_new_name_after_one_running_lookup(self) -> None:
        names = tuple(f"renew{number}.example" for number in range(32))
        old = (IPv4Address("203.0.113.10"),)
        pool = BoundedLookupPool(workers=8, name="website-renewal-limit-test")
        release = threading.Semaphore(0)
        started = threading.Condition()
        running = []
        callers_ready = threading.Barrier(5)
        clock = FakeClock()
        clock.now = 71.0

        def lookup(name, *_):
            if name in names:
                with started:
                    running.append(name)
                    started.notify_all()
                if not release.acquire(timeout=5):
                    raise AssertionError("test did not release renewal")
            return ((None, None, None, None, ("203.0.113.11", 0)),)

        def renew_batch(batch):
            callers_ready.wait(timeout=5)
            return resolve_domains(domains=batch)

        with website._resolution_cache_lock:
            website._resolution_cache.update((name, (70.0, old)) for name in names)
        with patch.object(website, "_lookup_pool", pool), patch.object(website, "time", clock), patch(
            "jittersim.engine.website.socket.getaddrinfo", side_effect=lookup
        ):
            with ThreadPoolExecutor(max_workers=5) as callers:
                try:
                    calls = [callers.submit(renew_batch, names[offset:offset + 8])
                             for offset in range(0, 32, 8)]
                    callers_ready.wait(timeout=5)
                    self.assertEqual([call.result(timeout=5) for call in calls], [old] * 4)
                    self.assertEqual(pool.unfinished(), 8)
                    with started:
                        self.assertTrue(started.wait_for(lambda: len(running) == 8, timeout=2))
                    cold = callers.submit(resolve_domains, domains=("new.example",))
                    deadline = time.monotonic() + 2
                    while pool.unfinished() != 9 and time.monotonic() < deadline:
                        time.sleep(0.001)
                    self.assertEqual(pool.unfinished(), 9)
                    # One completed renewal must let the new lookup run immediately.
                    release.release()
                    self.assertEqual(cold.result(timeout=2), (IPv4Address("203.0.113.11"),))
                finally:
                    for _ in names:
                        release.release()
                    wait_for_renewals()

    def test_failed_renewals_keep_the_original_expiry_and_pause_retries(self) -> None:
        old = (IPv4Address("203.0.113.10"),)
        for failure in (OSError, ValueError):
            with self.subTest(failure=failure):
                clock = FakeClock()
                clock.now = 71.0
                with website._resolution_cache_lock:
                    website._resolution_cache["example.com"] = (70.0, old)
                    website._recent_timeouts.clear()
                with patch.object(website, "time", clock), patch(
                    "jittersim.engine.website.socket.getaddrinfo", side_effect=failure("DNS failed")
                ) as lookup:
                    self.assertEqual(resolve_domains(domains=("example.com",)), old)
                    wait_for_renewals()
                    self.assertEqual(website._resolution_cache["example.com"], (70.0, old))
                    clock.now += website._TIMEOUT_MEMORY_SECONDS - 0.001
                    self.assertEqual(resolve_domains(domains=("example.com",)), old)
                    wait_for_renewals()
                    self.assertEqual(lookup.call_count, 1)
                    clock.now += 0.001
                    self.assertEqual(resolve_domains(domains=("example.com",)), old)
                    wait_for_renewals()
                    self.assertEqual(lookup.call_count, 2)
                    # Even a failure just before expiry cannot keep the old answer alive.
                    clock.now = 70.0 + website._STALE_SECONDS - 0.001
                    self.assertEqual(resolve_domains(domains=("example.com",)), old)
                    wait_for_renewals()
                    self.assertEqual(lookup.call_count, 3)
                    clock.now += 0.001
                    self.assertIsNone(website._stale_entry("example.com"))
                    self.assertEqual(resolve_domains(domains=("example.com",)), ())
                    self.assertEqual(lookup.call_count, 3)
                    clock.now += website._TIMEOUT_MEMORY_SECONDS
                    self.assertEqual(resolve_domains(domains=("example.com",)), ())
                    self.assertEqual(lookup.call_count, 4)
                    self.assertEqual(website._fresh_addresses("example.com"), ())

    def test_retry_memory_can_hold_a_failed_renewal_for_every_cached_name(self) -> None:
        old = (IPv4Address("203.0.113.10"),)
        names = tuple(f"failed{number}.example" for number in range(website._MAX_CACHED_DOMAINS))
        clock = FakeClock()
        clock.now = 71.0
        with website._resolution_cache_lock:
            website._resolution_cache.update((name, (70.0, old)) for name in names)
        with patch.object(website, "time", clock), patch(
            "jittersim.engine.website.socket.getaddrinfo", side_effect=OSError("DNS failed")
        ) as lookup:
            # Complete each renewal at the same instant, filling the retry memory.
            for name in names:
                self.assertEqual(website._cached_addresses(domain=name), old)
            self.assertEqual(lookup.call_count, len(names))
            self.assertEqual(len(website._recent_timeouts), len(names))
            with patch.object(website, "_start_lookup") as start:
                self.assertEqual(resolve_domains(domains=names), old)
                start.assert_not_called()

    def test_a_failed_renewal_that_finishes_after_the_stale_window_drops_the_old_answer(self):
        old = (IPv4Address("203.0.113.10"),)
        clock = FakeClock()
        clock.now = 71.0

        def failure(*_):
            clock.now = 70.0 + website._STALE_SECONDS
            raise OSError("DNS failed after the stale window")

        with website._resolution_cache_lock:
            website._resolution_cache["example.com"] = (70.0, old)
        with patch.object(website, "time", clock), patch(
            "jittersim.engine.website.socket.getaddrinfo", side_effect=failure
        ):
            self.assertEqual(website._cached_addresses(domain="example.com"), ())
            self.assertEqual(website._fresh_addresses("example.com"), ())

    def test_a_successful_empty_renewal_replaces_the_old_addresses(self) -> None:
        old = (IPv4Address("203.0.113.10"),)
        clock = FakeClock()
        clock.now = 71.0
        with website._resolution_cache_lock:
            website._resolution_cache["example.com"] = (70.0, old)
        with patch.object(website, "time", clock), patch(
            "jittersim.engine.website.socket.getaddrinfo", return_value=()
        ) as lookup:
            self.assertEqual(resolve_domains(domains=("example.com",)), old)
            wait_for_renewals()
            self.assertEqual(resolve_domains(domains=("example.com",)), ())
            self.assertEqual(website._resolution_cache["example.com"], (131.0, ()))
            self.assertEqual(lookup.call_count, 1)

    def test_a_renewal_can_recover_after_the_failure_retry_pause(self) -> None:
        old = (IPv4Address("203.0.113.10"),)
        clock = FakeClock()
        clock.now = 71.0
        new = ((None, None, None, None, ("203.0.113.11", 0)),)
        with website._resolution_cache_lock:
            website._resolution_cache["example.com"] = (70.0, old)
        with patch.object(website, "time", clock), patch(
            "jittersim.engine.website.socket.getaddrinfo", side_effect=[OSError("DNS failed"), new]
        ) as lookup:
            self.assertEqual(resolve_domains(domains=("example.com",)), old)
            wait_for_renewals()
            clock.now += website._TIMEOUT_MEMORY_SECONDS
            self.assertEqual(resolve_domains(domains=("example.com",)), old)
            wait_for_renewals()
            self.assertEqual(resolve_domains(domains=("example.com",)), (IPv4Address("203.0.113.11"),))
            self.assertEqual(website._resolution_cache["example.com"][0], 161.0)
            self.assertEqual(lookup.call_count, 2)

    def test_an_answer_older_than_the_stale_window_is_looked_up_again(self) -> None:
        old = ((None, None, None, None, ("203.0.113.10", 0)),)
        new = ((None, None, None, None, ("203.0.113.11", 0)),)
        with patch("jittersim.engine.website.socket.getaddrinfo", return_value=old):
            with patch("jittersim.engine.website.time.monotonic", return_value=10.0):
                resolve_domains(domains=("example.com",))
        late = 10.0 + website._RESOLUTION_CACHE_SECONDS + website._STALE_SECONDS + 1
        with patch("jittersim.engine.website.socket.getaddrinfo", return_value=new):
            with patch("jittersim.engine.website.time.monotonic", return_value=late):
                self.assertEqual(
                    resolve_domains(domains=("example.com",)), (IPv4Address("203.0.113.11"),)
                )

    def test_a_name_waits_for_a_place_when_other_lookups_fill_the_pool(self) -> None:
        from jittersim.engine.lookup_pool import MAX_UNFINISHED_LOOKUPS

        deadline = time.monotonic() + 5
        while website._lookup_pool.unfinished() and time.monotonic() < deadline:
            time.sleep(0.01)  # lookups left by earlier tests
        release = threading.Event()
        held = [website._lookup_pool.submit(release.wait, 5) for _ in range(MAX_UNFINISHED_LOOKUPS)]
        self.assertIsNone(website._lookup_pool.submit(release.wait, 5))
        threading.Timer(0.2, release.set).start()
        result = ((None, None, None, None, ("203.0.113.10", 0)),)
        with patch("jittersim.engine.website.socket.getaddrinfo", return_value=result):
            with website.lookup_session() as session:
                found = resolve_domains(domains=("waits.example.com",))
        for future in held:
            future.result(timeout=5)
        self.assertEqual((found, session.timed_out), ((IPv4Address("203.0.113.10"),), []))

    def test_a_full_block_list_keeps_every_address_past_the_stale_window(self) -> None:
        """One plan a second for longer than the cache life plus the stale window.

        Every name must keep its addresses on every pass, and a new name must still be
        looked up while many renewals are due.
        """

        clock = FakeClock()
        names = [f"h{n}.example.com" for n in range(MAX_GLOBAL_BLOCKED_DOMAINS)]
        last = int(website._RESOLUTION_CACHE_SECONDS + website._STALE_SECONDS) + 120
        with patch("jittersim.engine.website.time", clock), patch(
            "jittersim.engine.website.socket.getaddrinfo", side_effect=numbered_answer
        ):
            for second in range(last):
                clock.now = float(second)
                with website.lookup_session() as session:
                    found = resolve_domains(domains=names)
                self.assertEqual((len(found), session.timed_out), (len(names), []), second)
                wait_for_renewals()
            # Many answers are due now. A name that was never looked up is not skipped.
            clock.now += website._RESOLUTION_CACHE_SECONDS
            with website.lookup_session() as session:
                found = resolve_domains(domains=names + ["new.example.com"])
            self.assertEqual((len(found), session.timed_out), (len(names) + 1, []))

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

    @staticmethod
    def _release_lookups(lookups, names) -> None:
        lookups.release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with website._resolution_cache_lock:
                pending = any(name in website._pending for name in names)
            if not pending and not lookups.running:
                return
            time.sleep(0.01)
        raise AssertionError("blocked lookups did not finish during test cleanup")

    def test_successive_timeouts_share_one_worker_limit(self) -> None:
        names = tuple(f"host{number}.example" for number in range(1, 41))
        lookups = SlowLookups(slow=names)
        with patch("jittersim.engine.website.socket.getaddrinfo", side_effect=lookups):
            try:
                with patch.object(website, "RESOLVE_WAIT_SECONDS", 0.05):
                    for offset in range(0, len(names), 8):
                        resolve_domains(domains=names[offset : offset + 8])
                self.assertLessEqual(lookups.most_running, 8)
            finally:
                self._release_lookups(lookups, names)

    def test_queue_is_bounded_and_recovers_after_late_answers(self) -> None:
        names = tuple(f"host{number}.example" for number in range(1, 97))
        lookups = SlowLookups(slow=names)
        with patch("jittersim.engine.website.socket.getaddrinfo", side_effect=lookups):
            try:
                with patch.object(website, "RESOLVE_WAIT_SECONDS", 0.02):
                    for offset in range(0, len(names), 8):
                        resolve_domains(domains=names[offset : offset + 8])
                with website._resolution_cache_lock:
                    pending = len(website._pending)
                self.assertLessEqual(pending, 32)
                self.assertLessEqual(lookups.most_running, 8)
            finally:
                self._release_lookups(lookups, names)
            self.assertEqual(resolve_domains(domains=("example.com",)), (IPv4Address("203.0.113.10"),))

    def test_large_fast_batches_finish_within_the_original_deadline(self) -> None:
        names = tuple(f"host{number}.example" for number in range(1, 97))
        lookups = SlowLookups(slow=(), delay=0.001)
        with patch.object(website, "RESOLVE_WAIT_SECONDS", 3.0):
            found = resolve_domains(domains=names, resolver=lookups)
        self.assertEqual(len(found), len(names))
        self.assertEqual(set(lookups.calls), set(names))
        self.assertLessEqual(lookups.most_running, 8)

    def test_concurrent_calls_share_one_worker_limit(self) -> None:
        names = tuple(f"host{number}.example" for number in range(1, 41))
        lookups = SlowLookups(slow=names)
        with patch("jittersim.engine.website.socket.getaddrinfo", side_effect=lookups):
            try:
                with patch.object(website, "RESOLVE_WAIT_SECONDS", 0.1):
                    with ThreadPoolExecutor(max_workers=5) as callers:
                        calls = [
                            callers.submit(resolve_domains, domains=names[offset : offset + 8])
                            for offset in range(0, len(names), 8)
                        ]
                        for call in calls:
                            self.assertEqual(call.result(timeout=3), ())
                self.assertLessEqual(lookups.most_running, 8)
            finally:
                self._release_lookups(lookups, names)

    def test_late_answer_remains_fresh_from_its_completion(self) -> None:
        clock = [1.0]

        def getaddrinfo(domain, _port, _family, _kind):
            clock[0] += 120.0
            return ((None, None, None, None, (address_of(domain), 0)),)

        with patch.object(website.time, "monotonic", side_effect=lambda: clock[0]):
            with patch.object(website.socket, "getaddrinfo", side_effect=getaddrinfo):
                addresses = website._cached_addresses(domain="slow.example")
            self.assertEqual(website._fresh_addresses("slow.example"), addresses)

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

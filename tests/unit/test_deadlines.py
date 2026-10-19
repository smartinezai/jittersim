"""Direct tests of the shared time budget (networking/deadlines.py).

mutmut does not mutate decorated functions, so `deadline_scope` was only reached through
Panic. These tests hold its rules without Panic: the earlier deadline wins, the floor and the
deadline are always restored, and a thread starts without a budget.
"""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from jittersim.networking import deadlines
from jittersim.networking.deadlines import (
    bounded_wait,
    command_timeout,
    deadline_scope,
    scope_remaining,
)

NOW = 1_000.0
seconds = st.floats(min_value=0.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)
offsets = st.floats(min_value=-10_000.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)


def clock(value=NOW):
    # Replace the module's own `time`, so no other code sees a frozen clock.
    return patch.object(deadlines, "time", SimpleNamespace(monotonic=lambda: value))


class DeadlineScopeTests(unittest.TestCase):
    def test_there_is_no_budget_outside_a_scope(self):
        self.assertIsNone(scope_remaining())
        self.assertEqual(command_timeout(7.5), 7.5)

    def test_a_scope_yields_its_deadline_and_reports_the_time_left(self):
        with clock(), deadline_scope(NOW + 30.0) as deadline:
            self.assertEqual(deadline, NOW + 30.0)
            self.assertEqual(scope_remaining(), 30.0)

    def test_the_time_left_can_be_negative(self):
        with clock(), deadline_scope(NOW - 5.0):
            self.assertEqual(scope_remaining(), -5.0)

    def test_the_budget_ends_with_the_scope(self):
        with clock():
            with deadline_scope(NOW + 30.0):
                pass
            self.assertIsNone(scope_remaining())

    def test_the_budget_ends_when_the_scope_raises(self):
        with clock():
            with self.assertRaises(RuntimeError):
                with deadline_scope(NOW + 30.0, floor=4.0):
                    raise RuntimeError("boom")
            self.assertIsNone(scope_remaining())
            self.assertEqual(bounded_wait(9.0, NOW - 1.0), 0.0)

    def test_an_inner_scope_cannot_extend_the_outer_deadline(self):
        with clock(), deadline_scope(NOW + 10.0):
            with deadline_scope(NOW + 50.0) as inner:
                self.assertEqual(inner, NOW + 10.0)
                self.assertEqual(scope_remaining(), 10.0)

    def test_an_inner_scope_can_shorten_the_deadline_and_the_outer_one_returns(self):
        with clock(), deadline_scope(NOW + 50.0):
            with deadline_scope(NOW + 10.0) as inner:
                self.assertEqual(inner, NOW + 10.0)
                self.assertEqual(scope_remaining(), 10.0)
            self.assertEqual(scope_remaining(), 50.0)

    def test_the_floor_defaults_to_zero_and_belongs_to_the_scope(self):
        with clock():
            with deadline_scope(NOW - 1.0):
                self.assertEqual(bounded_wait(9.0, NOW - 1.0), 0.0)
            with deadline_scope(NOW - 1.0, floor=2.5):
                self.assertEqual(bounded_wait(9.0, NOW - 1.0), 2.5)
                with deadline_scope(NOW - 1.0, floor=0.5):
                    self.assertEqual(bounded_wait(9.0, NOW - 1.0), 0.5)
                self.assertEqual(bounded_wait(9.0, NOW - 1.0), 2.5)
            self.assertEqual(bounded_wait(9.0, NOW - 1.0), 0.0)

    def test_a_new_thread_starts_without_a_budget(self):
        seen = []
        with clock(), deadline_scope(NOW + 30.0, floor=3.0):
            worker = threading.Thread(
                target=lambda: seen.append((scope_remaining(), command_timeout(8.0)))
            )
            worker.start()
            worker.join(5)
        self.assertEqual(seen, [(None, 8.0)])

    @settings(max_examples=60, deadline=None, database=None, derandomize=True)
    @given(first=offsets, second=offsets, floor=seconds)
    def test_nested_scopes_keep_the_earlier_deadline(self, first, second, floor):
        with clock(), deadline_scope(NOW + first, floor=floor):
            with deadline_scope(NOW + second) as inner:
                self.assertEqual(inner, min(NOW + first, NOW + second))
            self.assertEqual(scope_remaining(), (NOW + first) - NOW)


class BoundedWaitTests(unittest.TestCase):
    def test_without_a_deadline_the_cap_applies_whatever_the_floor_and_reserve(self):
        self.assertEqual(bounded_wait(6.0, None, reserve=100.0, floor=50.0), 6.0)
        with deadline_scope(NOW - 100.0, floor=50.0):
            self.assertEqual(bounded_wait(6.0, None), 6.0)

    def test_the_cap_wins_while_there_is_plenty_of_time(self):
        with clock():
            self.assertEqual(bounded_wait(5.0, NOW + 60.0), 5.0)

    def test_the_time_left_wins_when_it_is_smaller_than_the_cap(self):
        with clock():
            self.assertEqual(bounded_wait(5.0, NOW + 3.0), 3.0)

    def test_the_reserve_is_kept_back_from_the_time_left(self):
        with clock():
            self.assertEqual(bounded_wait(5.0, NOW + 3.0, reserve=1.0), 2.0)
            self.assertEqual(bounded_wait(5.0, NOW + 60.0, reserve=1.0), 5.0)

    def test_nothing_is_left_after_the_deadline_without_a_floor(self):
        with clock():
            self.assertEqual(bounded_wait(5.0, NOW - 2.0), 0.0)
            self.assertEqual(bounded_wait(5.0, NOW + 1.0, reserve=4.0), 0.0)

    def test_the_floor_is_what_a_late_step_still_gets_but_never_more_than_the_cap(self):
        with clock():
            self.assertEqual(bounded_wait(5.0, NOW - 2.0, floor=1.5), 1.5)
            self.assertEqual(bounded_wait(5.0, NOW - 2.0, floor=9.0), 5.0)

    def test_the_floor_does_not_shorten_a_wait_that_still_has_time(self):
        with clock():
            self.assertEqual(bounded_wait(5.0, NOW + 3.0, floor=1.0), 3.0)

    def test_an_explicit_floor_replaces_the_scope_floor_even_when_it_is_zero(self):
        with clock(), deadline_scope(NOW - 1.0, floor=2.0):
            self.assertEqual(bounded_wait(5.0, NOW - 1.0), 2.0)
            self.assertEqual(bounded_wait(5.0, NOW - 1.0, floor=0.0), 0.0)
            self.assertEqual(bounded_wait(5.0, NOW - 1.0, floor=1.0), 1.0)

    @settings(max_examples=200, deadline=None, database=None, derandomize=True)
    @given(cap=seconds, offset=offsets, reserve=seconds, floor=seconds)
    def test_the_wait_follows_the_rule_and_stays_inside_zero_and_the_cap(
        self, cap, offset, reserve, floor
    ):
        with clock():
            result = bounded_wait(cap, NOW + offset, reserve=reserve, floor=floor)
        expected = max(0.0, min(cap, max(floor, (NOW + offset) - NOW - reserve)))
        self.assertEqual(result, expected)
        self.assertGreaterEqual(result, 0.0)
        self.assertLessEqual(result, cap)


class CommandTimeoutTests(unittest.TestCase):
    def test_a_command_gets_its_own_timeout_while_the_scope_has_time(self):
        with clock(), deadline_scope(NOW + 60.0):
            self.assertEqual(command_timeout(10.0), 10.0)

    def test_a_command_is_shortened_to_the_time_left(self):
        with clock(), deadline_scope(NOW + 4.0):
            self.assertEqual(command_timeout(10.0), 4.0)

    def test_a_command_after_the_deadline_gets_the_scope_floor(self):
        with clock(), deadline_scope(NOW - 3.0, floor=2.0):
            self.assertEqual(command_timeout(10.0), 2.0)
        with clock(), deadline_scope(NOW - 3.0):
            self.assertEqual(command_timeout(10.0), 0.0)

    @settings(max_examples=100, deadline=None, database=None, derandomize=True)
    @given(timeout=seconds, offset=offsets, floor=seconds)
    def test_a_command_never_waits_longer_than_asked_or_than_what_is_left(
        self, timeout, offset, floor
    ):
        with clock(), deadline_scope(NOW + offset, floor=floor):
            result = command_timeout(timeout)
        left = (NOW + offset) - NOW
        self.assertEqual(result, max(0.0, min(timeout, max(floor, left))))
        self.assertLessEqual(result, timeout)


if __name__ == "__main__":
    unittest.main()

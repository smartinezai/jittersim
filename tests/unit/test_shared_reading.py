"""The small memo that lets open dashboard tabs share one slow read of the helper."""

import threading
import time
from unittest.mock import patch

import pytest

from jittersim.api import shared_reading
from jittersim.api.shared_reading import SharedReading


class Clock:
    def __init__(self):
        self.now = 1_000.0

    def monotonic(self):
        return self.now


class Reads:
    """A read that counts its calls and can be told to block or to fail."""

    def __init__(self):
        self.calls = 0
        self.value = 1
        self.error = None
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def __call__(self):
        self.calls += 1
        self.started.set()
        assert self.release.wait(10), "the test never released the read"
        if self.error is not None:
            raise self.error
        return self.value


@pytest.fixture
def clock():
    fake = Clock()
    with patch.object(shared_reading, "time", fake):
        yield fake


def in_thread(function, *arguments):
    """Run a call in a thread. Return the thread and a list that will hold its outcome."""

    outcome = []

    def run():
        try:
            outcome.append(("value", function(*arguments)))
        except BaseException as error:  # the test compares errors too
            outcome.append(("error", error))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def test_a_call_within_max_age_takes_the_kept_result(clock):
    reading, read = SharedReading(), Reads()
    assert reading.get(read, 1.0) == 1
    read.value = 2
    clock.now += 0.9
    assert reading.get(read, 1.0) == 1
    assert read.calls == 1


def test_a_call_after_max_age_reads_again(clock):
    reading, read = SharedReading(), Reads()
    reading.get(read, 1.0)
    read.value = 2
    clock.now += 1.0
    assert reading.get(read, 1.0) == 2
    assert read.calls == 2


def test_each_caller_names_how_old_a_result_may_be(clock):
    reading, read = SharedReading(), Reads()
    reading.get(read, 2.0)
    clock.now += 1.5
    assert reading.get(read, 2.0) == 1 and read.calls == 1
    assert reading.get(read, 1.0) == 1 and read.calls == 2


def test_max_age_zero_always_reads(clock):
    reading, read = SharedReading(), Reads()
    for _ in range(3):
        reading.get(read, 0.0)
    assert read.calls == 3


def test_a_slow_read_is_kept_from_its_completion(clock):
    reading = SharedReading()
    calls = []

    def slow_read():
        calls.append(clock.now)
        clock.now += 8.0
        return False  # A helper timeout becomes a degraded health answer.

    assert reading.get(slow_read, 1.0) is False
    assert reading.get(slow_read, 1.0) is False
    assert len(calls) == 1
    clock.now += 1.0
    assert reading.get(slow_read, 1.0) is False
    assert len(calls) == 2


def test_drop_makes_the_kept_result_unusable(clock):
    reading, read = SharedReading(), Reads()
    reading.get(read, 10.0)
    reading.drop()
    read.value = 2
    assert reading.get(read, 10.0) == 2
    assert reading.get(read, 10.0) == 2
    assert read.calls == 2


def test_an_error_is_not_kept(clock):
    reading, read = SharedReading(), Reads()
    read.error = RuntimeError("helper down")
    with pytest.raises(RuntimeError):
        reading.get(read, 10.0)
    read.error = None
    assert reading.get(read, 10.0) == 1
    assert read.calls == 2


def test_callers_that_arrive_during_a_read_share_it():
    reading, read = SharedReading(), Reads()
    read.release.clear()
    first, first_outcome = in_thread(reading.get, read, 1.0)
    assert read.started.wait(10)
    others = [in_thread(reading.get, read, 1.0) for _ in range(5)]
    time.sleep(0.4)
    read.release.set()
    for thread, _ in [(first, first_outcome), *others]:
        thread.join(10)
    assert read.calls == 1
    assert [outcome[0] for _, outcome in [(first, first_outcome), *others]] == [("value", 1)] * 6


def test_callers_that_arrive_during_a_failing_read_share_its_error_but_not_for_later():
    reading, read = SharedReading(), Reads()
    read.release.clear()
    read.error = RuntimeError("helper hung")
    first, first_outcome = in_thread(reading.get, read, 1.0)
    assert read.started.wait(10)
    others = [in_thread(reading.get, read, 1.0) for _ in range(3)]
    time.sleep(0.4)
    read.release.set()
    for thread, _ in [(first, first_outcome), *others]:
        thread.join(10)
    outcomes = [outcome[0] for _, outcome in [(first, first_outcome), *others]]
    assert read.calls == 1
    assert [kind for kind, _ in outcomes] == ["error"] * 4
    assert len({id(error) for _, error in outcomes}) == 1
    read.error = None
    assert reading.get(read, 1.0) == 1
    assert read.calls == 2


def test_a_read_in_progress_when_dropped_is_neither_kept_nor_joined():
    reading, read = SharedReading(), Reads()
    read.release.clear()
    old, old_outcome = in_thread(reading.get, read, 10.0)
    assert read.started.wait(10)
    reading.drop()
    # A caller after the drop starts its own read instead of waiting for the older one.
    fresh = Reads()
    fresh.value = 7
    assert reading.get(fresh, 10.0) == 7
    read.release.set()
    old.join(10)
    # The older caller still gets what it asked for, but nobody else sees it.
    assert old_outcome == [("value", 1)]
    assert reading.get(fresh, 10.0) == 7
    assert fresh.calls == 1


def test_drop_does_not_wait_for_a_read_in_progress():
    reading, read = SharedReading(), Reads()
    read.release.clear()
    blocked, _ = in_thread(reading.get, read, 1.0)
    assert read.started.wait(10)
    dropper, _ = in_thread(reading.drop)
    dropper.join(2)
    assert not dropper.is_alive()
    read.release.set()
    blocked.join(10)

"""A concurrent guard writer no longer fails Panic's save (gap audit 2026-10-09, finding 8).

PanicGuard.write took its lock without waiting, so a provision by access-boot or
the installer at the same moment turned Panic's save into an error and Panic
failed closed.
"""

import fcntl
import os
import threading
import time

import pytest

from jittersim import panic_guard as panic_guard_module
from tests.unit.test_persistent_panic_guard import guard, short_path, stopped_state  # noqa: F401


def hold_guard_lock(persistent, seconds):
    persistent.write(stopped_state())
    held = threading.Event()

    def holder():
        descriptor = os.open(persistent.path.parent / ".guard.lock", os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            held.set()
            time.sleep(seconds)
        finally:
            os.close(descriptor)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert held.wait(5)
    return thread


def test_panic_save_waits_for_a_short_concurrent_writer(short_path):  # noqa: F811
    persistent = guard(short_path)
    holder = hold_guard_lock(persistent, 0.5)
    try:
        assert persistent.write(stopped_state(True)) == stopped_state(True)
    finally:
        holder.join(5)
    assert persistent.read() == stopped_state(True)


def test_the_wait_is_bounded(short_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(panic_guard_module, "GUARD_LOCK_WAIT_SECONDS", 0.3)
    persistent = guard(short_path)
    holder = hold_guard_lock(persistent, 3)
    try:
        started = time.monotonic()
        with pytest.raises(OSError):
            persistent.write(stopped_state(True))
        assert time.monotonic() - started < 2
    finally:
        holder.join(5)
    assert persistent.read() == stopped_state()

"""Provision the same private stop record used by the deployed ad controller."""

import os

from jittersim.panic_guard import PANIC_GUARD_FIELDS, PANIC_GUARD_FORMAT, PanicGuard


def provision_guard(directory):
    guard = PanicGuard(directory / "panic-state.json", owner_uid=os.geteuid())
    guard.write(
        {"version": PANIC_GUARD_FORMAT, **dict.fromkeys(PANIC_GUARD_FIELDS, False)},
        initialize_only=True,
    )
    return guard

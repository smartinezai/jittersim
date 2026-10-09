"""Modules that wrap every privileged helper request, Panic included, stay reviewed.

The update gate (PANIC_SOURCE_FILES and PANIC_APPROVED_SOURCES in updates.py) hashes
only panic_guard.py, networking/helper.py and networking/access.py. Adding more files
there would make every older approved bundle, and with it every rollback target,
fail admission. These modules run around normal-access (Panic) and Reset on every
helper dispatch, so their exact bytes are pinned here instead.
"""

import hashlib
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src/jittersim"

# Reviewed in review-panic-consumers-outage-logging-2026-10-09 (approve-with-conditions, F1).
PINNED_PANIC_DISPATCH_MODULES = {
    "diagnostic_records.py": "d40a3b15302ae2428727181dbe7fb69ad52f2a1dbb1650a1fb89853d84d2f0d8",
    "diagnostic_privacy.py": "add098a0a8fa879795cda12e70bfc9acfdc8801b1bf1a7e983ff85674c9d8193",
}


@pytest.mark.parametrize("name", sorted(PINNED_PANIC_DISPATCH_MODULES))
def test_panic_dispatch_module_bytes_are_the_reviewed_bytes(name):
    actual = hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
    assert actual == PINNED_PANIC_DISPATCH_MODULES[name], (
        f"src/jittersim/{name} changed (sha256 {actual}). It runs on the privileged "
        "helper dispatch of every request, including Panic (normal-access) and Reset, "
        "and also installs the helper's logging and exception hooks. A change needs a "
        "new independent Panic consumer review (no blocking I/O, no new lock waits, no "
        "exception escaping the wrapper) before this pin is updated to the reviewed hash."
    )


def test_helper_still_uses_exactly_the_pinned_dispatch_modules():
    helper = (SOURCE / "networking/helper.py").read_text()
    assert "from jittersim.diagnostic_privacy import" in helper
    assert "from jittersim.diagnostic_records import" in helper
    assert "@recorded_payload\n    def handle_payload" in helper

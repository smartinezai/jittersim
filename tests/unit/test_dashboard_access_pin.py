"""The module that decides who may open the dashboard stays reviewed.

The update gate (PANIC_SOURCE_FILES and PANIC_APPROVED_SOURCES in updates.py) hashes
only panic_guard.py, networking/helper.py and networking/access.py. Adding more files
there would make every older approved bundle, and with it every rollback target,
fail admission. networking/dashboard_access.py runs inside the privileged helper on
every dashboard-access request and now holds the protection-derived grants, so its
exact bytes are pinned here instead.
"""

import hashlib
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src/jittersim"

# Reviewed in review-protected-dashboard-access-2026-10-09 (approve-with-conditions, R4).
PINNED_DASHBOARD_ACCESS_MODULES = {
    "networking/dashboard_access.py": "d0cb7e30a305e222efe7c95af603da052783f57a6c65ca47b3a8b72d310007bf",
}


def test_dashboard_access_module_bytes_are_the_reviewed_bytes():
    for name, expected in PINNED_DASHBOARD_ACCESS_MODULES.items():
        actual = hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
        assert actual == expected, (
            f"src/jittersim/{name} changed (sha256 {actual}). The privileged helper uses it "
            "on every dashboard-access request, including the status checks in front of "
            "Panic (normal-access), and it decides which devices are allowed. A change needs "
            "a new independent Panic consumer review (no blocking I/O, no new lock waits, no "
            "widened grant) before this pin is updated to the reviewed hash."
        )


def test_helper_still_takes_its_access_decisions_from_the_pinned_module():
    helper = (SOURCE / "networking/helper.py").read_text()
    assert "from .dashboard_access import (" in helper
    assert "store.access_details(devices, identity)" in helper
    assert 'store.sync_protected(payload.get("devices"))' in helper

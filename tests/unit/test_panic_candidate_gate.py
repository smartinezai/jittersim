"""Standalone rollback checks require reviewed startup source without importing it."""

import hashlib
import os
import shutil
from pathlib import Path

import pytest

from jittersim.panic_guard import (
    PANIC_COMPATIBILITY_FILES,
    PanicGuardError,
    check_panic_candidate,
)


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "code"
    source = Path(__file__).resolve().parents[2]
    approved = {}
    for name in PANIC_COMPATIBILITY_FILES:
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
        approved[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    return root, approved


@pytest.mark.parametrize("failed,stopped", [(False, True), (True, True), (False, False)])
def test_required_protection_refuses_old_candidate_even_after_fresh_start(
    candidate, failed, stopped
):
    root, approved = candidate
    state = {
        "version": 1,
        "panic_stop_latched": stopped,
        "panic_recovery_failed": failed,
        "control_revision_required": True,
    }
    check_panic_candidate(state, root, [approved], owner_uid=os.getuid())
    # An older helper has no persistent guard consumer. Its label is irrelevant.
    (root / "src/jittersim/panic_guard.py").unlink()
    with pytest.raises(PanicGuardError, match="cannot preserve Panic protection"):
        check_panic_candidate(state, root, [approved], owner_uid=os.getuid())


@pytest.mark.parametrize("damage", ["changed-consumer", "symlink", "group-write", "oversized"])
def test_candidate_evidence_never_accepts_unreviewed_or_unsafe_source(candidate, damage, tmp_path):
    root, approved = candidate
    path = root / "src/jittersim/networking/access.py"
    if damage == "changed-consumer":
        path.write_text("# Startup protection removed.\n")
    elif damage == "symlink":
        moved = tmp_path / "moved.py"
        path.rename(moved)
        path.symlink_to(moved)
    elif damage == "group-write":
        path.chmod(0o660)
    else:
        path.write_text(" " * (1024 * 1024 + 1))
    state = {
        "version": 1,
        "panic_stop_latched": True,
        "panic_recovery_failed": False,
        "control_revision_required": True,
    }
    with pytest.raises(PanicGuardError):
        check_panic_candidate(state, root, [approved], owner_uid=os.getuid())


def test_clear_guard_also_refuses_a_candidate_that_cannot_preserve_future_panic():
    state = {
        "version": 1,
        "panic_stop_latched": False,
        "panic_recovery_failed": False,
        "control_revision_required": False,
    }
    with pytest.raises(PanicGuardError, match="cannot preserve Panic protection"):
        check_panic_candidate(state, Path("/no-package"), [], owner_uid=os.getuid())

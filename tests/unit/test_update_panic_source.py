"""Use the reviewed producer before an outage and reread after writers stop."""

import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim import panic_guard, updates
from tests.unit.test_update_preparation import pi as _pi_fixture


@pytest.fixture(name="pi")
def stand_in_pi():
    yield from _pi_fixture.__wrapped__()


@pytest.fixture
def panic_source(tmp_path):
    source = tmp_path / "source"
    checkout = Path(__file__).resolve().parents[2]
    for name in updates.PANIC_SOURCE_FILES:
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checkout / name, destination)
    return source


def legacy_state(**changes):
    state = dict(version=1, gateway_state="available", persistence_enabled=False,
                 outage_observed=False, outage_stop_latched=False, reset_pending=False,
                 recovery_pending=False, consecutive_failures=0, consecutive_successes=0,
                 panic_stop_latched=False, household_stop_latched=False,
                 panic_recovery_failed=False, control_revision_required=False)
    state.update(changes)
    return state


def protection(source, tmp_path, **options):
    return updates.PanicProtection(source, guard_path=tmp_path / "guard/state.json",
                                   legacy_path=tmp_path / "legacy.json", owner_uid=os.geteuid(),
                                   **options)


def test_default_bundle_accepts_the_exact_reviewed_consumers(panic_source):
    module = updates._panic_candidate(panic_source)
    assert module.PANIC_GUARD_FORMAT == 2


def test_missing_legacy_refuses_before_creating_persistent_state(panic_source, tmp_path):
    safety = protection(panic_source, tmp_path)
    with pytest.raises(updates.PanicProtectionError):
        safety.inspect(panic_source)
    assert not (tmp_path / "guard").exists()


def test_after_stop_producer_reads_current_flags_and_does_not_publish_preview(panic_source, tmp_path):
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(legacy_state()))
    safety = protection(panic_source, tmp_path)
    safety.inspect(panic_source)
    assert not (tmp_path / "guard").exists()
    legacy.write_text(json.dumps(legacy_state(panic_stop_latched=True, household_stop_latched=True,
                                             panic_recovery_failed=True, control_revision_required=True)))
    safety.provision(panic_source)
    guard = panic_guard.PanicGuard(tmp_path / "guard/state.json", owner_uid=os.geteuid())
    assert all(guard.read()[field] for field in panic_guard.PANIC_GUARD_FIELDS)


def test_unknown_current_intent_after_stop_is_not_repaired(panic_source, tmp_path):
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(legacy_state()))
    safety = protection(panic_source, tmp_path)
    safety.inspect(panic_source)
    legacy.write_text('{"version": 1}')
    with pytest.raises(updates.PanicProtectionError):
        safety.provision(panic_source)
    assert not (tmp_path / "guard").exists()


@pytest.mark.parametrize("latched", [False, True])
def test_established_guard_refuses_old_consumers_even_when_clear(panic_source, tmp_path, latched):
    safety = protection(panic_source, tmp_path)
    state = dict(version=2, **{name: latched for name in panic_guard.PANIC_GUARD_FIELDS})
    panic_guard.PanicGuard(tmp_path / "guard/state.json", owner_uid=os.geteuid()).write(state)
    old = tmp_path / "old"
    old.mkdir()
    with pytest.raises(updates.PanicProtectionError):
        safety.inspect(old)
    assert json.loads((tmp_path / "guard/state.json").read_text()) == state


def test_retained_stdlib_producer_survives_missing_application_source(panic_source, tmp_path):
    safety = protection(panic_source, tmp_path)
    (tmp_path / "legacy.json").write_text(json.dumps(legacy_state()))
    safety.inspect(panic_source)
    retained = tmp_path / "panic-manager.py"
    safety.retain(retained)
    safety.provision(panic_source)
    # The retained producer needs no installed application import. The selected
    # source is still independently checked before any restart.
    recovered = protection(tmp_path / "missing", tmp_path, retained=retained)
    recovered.inspect(panic_source)
    retained.write_text("# unsupported producer")
    with pytest.raises(updates.PanicProtectionError):
        protection(panic_source, tmp_path, retained=retained)


def test_actual_manual_bootstrap_keeps_the_verified_producer_separate(panic_source, tmp_path):
    manager_source = panic_source / "src/jittersim/updates.py"
    shutil.copyfile(updates.__file__, manager_source)
    (tmp_path / "legacy.json").write_text(json.dumps(legacy_state()))
    original = updates.PanicProtection

    def candidate(source, **options):
        return original(source, guard_path=tmp_path / "guard/state.json",
                        legacy_path=tmp_path / "legacy.json", owner_uid=os.geteuid(), **options)

    with patch.object(updates, "PanicProtection", candidate):
        with updates.update_lock(tmp_path / "updates") as descriptor:
            updates.bootstrap_manager(manager_source, tmp_path / "retained-manager.py",
                                      root=tmp_path / "updates", descriptor=descriptor)
    assert (tmp_path / "retained-manager.py").read_bytes() == manager_source.read_bytes()
    assert (tmp_path / "jittersim-panic-guard.py").read_bytes() == \
        (panic_source / updates.PANIC_SOURCE_FILES[0]).read_bytes()


def test_actual_apply_refuses_unsupported_recovery_before_stopping_and_keeps_intent(pi, tmp_path):
    (tmp_path / "legacy.json").write_text(json.dumps(legacy_state()))
    commands, run = pi.recorder()
    # This is the real default admission route. Its guard paths alone are
    # redirected so it cannot read or mutate this Mac's system directories.
    original = getattr(updates, "PanicProtection", None)

    def candidate(source, **options):
        return original(source, guard_path=tmp_path / "guard/state.json",
                        legacy_path=tmp_path / "legacy.json", owner_uid=os.geteuid(), **options)

    with patch.object(updates, "PanicProtection", candidate, create=True):
        with pytest.raises(updates.UpdateError, match="Panic"):
            pi.apply(run, lambda: True, isolate_panic=False)
    assert not any(command[:2] == ["systemctl", "stop"] for command in commands)
    assert (pi.code / "old-file").read_text() == "previous installation"
    assert updates.read_json(pi.root / "request.json")
    assert not (tmp_path / "guard").exists()


@pytest.mark.parametrize("failure", ["none", "install", "unknown-intent"])
def test_actual_apply_rereads_after_stop_and_keeps_guard_outside_recovery(pi, tmp_path, failure):
    checkout = Path(__file__).resolve().parents[2]
    for destination in (pi.code, pi.root / "staged"):
        for name in updates.PANIC_SOURCE_FILES:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(checkout / name, target)
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(legacy_state()))
    original = updates.PanicProtection
    seen_stop = False

    def candidate(source, **options):
        return original(source, guard_path=tmp_path / "guard/state.json",
                        legacy_path=legacy, owner_uid=os.geteuid(), **options)

    def run(command, timeout):
        nonlocal seen_stop
        if command[:2] == ["systemctl", "stop"] and not seen_stop:
            assert not (tmp_path / "guard").exists()
            seen_stop = True
            latest = {"version": 1} if failure == "unknown-intent" else legacy_state(
                panic_stop_latched=True, household_stop_latched=True,
                panic_recovery_failed=True, control_revision_required=True)
            legacy.write_text(json.dumps(latest))
        if failure == "install" and command[:4] == [str(pi.code / ".venv/bin/python"), "-m", "pip", "install"]:
            raise updates.UpdateError("controlled installation failure")

    with patch.object(updates, "PanicProtection", candidate):
        if failure == "none":
            pi.apply(run, lambda: True, isolate_panic=False)
        else:
            with pytest.raises(updates.UpdateError):
                pi.apply(run, lambda: True, isolate_panic=False)
    assert seen_stop
    if failure == "unknown-intent":
        assert not (tmp_path / "guard").exists()
        assert updates.read_json(pi.root / "request.json")["restart_only"] is True
    else:
        state = panic_guard.PanicGuard(tmp_path / "guard/state.json", owner_uid=os.geteuid()).read()
        assert all(state[field] for field in panic_guard.PANIC_GUARD_FIELDS)
        assert not any(path.name == "state.json" for path in updates.recovery_backup(pi.root).rglob("*"))

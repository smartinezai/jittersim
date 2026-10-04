"""A signed candidate may migrate only complete proven legacy stop intent."""

import json
import os

import pytest

from jittersim import panic_guard as module
from jittersim.panic_guard import PanicGuard


def legacy_state(stopped=False, failed=False):
    return {
        "version": 1,
        "persistence_enabled": True,
        "gateway_state": "available",
        "outage_observed": False,
        "outage_stop_latched": False,
        "reset_pending": False,
        "recovery_pending": False,
        "consecutive_failures": 0,
        "consecutive_successes": 2,
        "panic_stop_latched": stopped,
        "panic_recovery_failed": failed,
        "control_revision_required": stopped,
    }


def fixture(tmp_path):
    persistent = PanicGuard(tmp_path / "control" / "panic-state.json", owner_uid=os.getuid())
    old = tmp_path / "gateway-state.json"
    return persistent, old


@pytest.mark.parametrize("stopped,failed", [(False, False), (True, False), (True, True)])
def test_upgrade_producer_persists_exact_known_legacy_state_before_replacement(
    tmp_path, stopped, failed
):
    persistent, old = fixture(tmp_path)
    before = legacy_state(stopped, failed)
    old.write_text(json.dumps(before))
    state = persistent.provision_upgrade(old)
    assert state == {
        "version": 2,
        "panic_stop_latched": stopped,
        "household_stop_latched": stopped,
        "panic_recovery_failed": failed,
        "control_revision_required": stopped,
    }
    # Runtime loss and a process crash before source replacement leave a readable guard.
    old.unlink()
    reconstructed = PanicGuard(persistent.path, owner_uid=os.getuid())
    assert reconstructed.read() == state
    assert reconstructed.provision_upgrade(old) == state


@pytest.mark.parametrize("damage", ["missing", "malformed", "missing-flags", "boolean-version"])
def test_unknown_first_upgrade_never_creates_clear_or_synthetic_stop_state(tmp_path, damage):
    persistent, old = fixture(tmp_path)
    if damage != "missing":
        state = legacy_state()
        if damage == "malformed":
            state = {"version": 1}
        elif damage == "missing-flags":
            for field in module.PANIC_GUARD_FIELDS:
                state.pop(field, None)
        else:
            state["version"] = True
        old.write_text(json.dumps(state))
    with pytest.raises(OSError):
        persistent.provision_upgrade(old)
    assert not persistent.path.parent.exists()


def test_upgrade_does_not_repair_an_existing_missing_record_even_with_valid_legacy(tmp_path):
    persistent, old = fixture(tmp_path)
    persistent.write(
        {
            "version": 1,
            "panic_stop_latched": True,
            "panic_recovery_failed": True,
            "control_revision_required": True,
        }
    )
    persistent.path.unlink()
    old.write_text(json.dumps(legacy_state()))
    with pytest.raises(OSError):
        persistent.provision_upgrade(old)
    assert not persistent.path.exists()


def test_actual_producer_cli_reports_only_guard_state_and_refuses_unproven_first_upgrade(
    tmp_path, monkeypatch, capsys
):
    persistent, old = fixture(tmp_path)
    monkeypatch.setattr(module, "PanicGuard", lambda: persistent)
    monkeypatch.setattr(module, "PANIC_LEGACY_PATH", old, raising=False)
    assert module.main(["--provision-upgrade"]) == 1
    assert capsys.readouterr().out == ""
    assert not persistent.path.parent.exists()
    old.write_text(json.dumps(legacy_state(True)))
    assert module.main(["--provision-upgrade"]) == 0
    assert json.loads(capsys.readouterr().out) == persistent.read()


@pytest.mark.parametrize("stopped,failed", [(False, False), (True, False), (True, True)])
def test_read_only_upgrade_inspection_preserves_proven_flags_without_creating_guard(
    tmp_path, stopped, failed
):
    persistent, old = fixture(tmp_path)
    before = json.dumps(legacy_state(stopped, failed))
    old.write_text(before)
    inspected = persistent.inspect_upgrade(old)
    assert inspected == {
        "version": 2,
        "panic_stop_latched": stopped,
        "household_stop_latched": stopped,
        "panic_recovery_failed": failed,
        "control_revision_required": stopped,
    }
    assert not persistent.path.parent.exists()
    assert old.read_text() == before


@pytest.mark.parametrize("damage", ["missing", "old-schema", "boolean-version", "missing-flags"])
def test_read_only_upgrade_inspection_refuses_unknown_state_without_creating_guard(
    tmp_path, damage
):
    persistent, old = fixture(tmp_path)
    if damage != "missing":
        state = legacy_state()
        if damage == "old-schema":
            state = {"version": 1}
        elif damage == "boolean-version":
            state["version"] = True
        else:
            state.pop("panic_stop_latched")
        old.write_text(json.dumps(state))
    before = old.read_bytes() if old.exists() else None
    with pytest.raises(OSError):
        persistent.inspect_upgrade(old)
    assert not persistent.path.parent.exists()
    assert (old.read_bytes() if old.exists() else None) == before


def test_producer_rechecks_current_panic_after_read_only_upgrade_inspection(tmp_path):
    persistent, old = fixture(tmp_path)
    old.write_text(json.dumps(legacy_state()))
    assert not persistent.inspect_upgrade(old)["panic_stop_latched"]
    old.write_text(json.dumps(legacy_state(True, True)))
    current = persistent.provision_upgrade(old)
    assert current["panic_stop_latched"] and current["household_stop_latched"]
    assert current["panic_recovery_failed"] and current["control_revision_required"]
    assert persistent.read() == current


def test_read_only_upgrade_inspection_preserves_established_guard_and_refuses_missing_record(
    tmp_path,
):
    persistent, old = fixture(tmp_path)
    old.write_text(json.dumps(legacy_state()))
    current = persistent.write(
        {
            "version": 2,
            "panic_stop_latched": True,
            "household_stop_latched": True,
            "panic_recovery_failed": True,
            "control_revision_required": True,
        }
    )
    before = persistent.path.read_bytes()
    assert persistent.inspect_upgrade(old) == current
    assert persistent.path.read_bytes() == before
    persistent.path.unlink()
    with pytest.raises(OSError):
        persistent.inspect_upgrade(old)
    assert not persistent.path.exists()

"""Fresh admission cannot relabel partial or historical installation state."""

import fcntl
import os

import pytest

from jittersim import panic_guard as module
from jittersim.panic_guard import PanicGuard


def setup(tmp_path):
    persistent = PanicGuard(tmp_path / "control" / "panic-state.json", owner_uid=os.getuid())
    witnesses = tuple(
        tmp_path / name
        for name in (
            "code",
            "data",
            "access",
            "config",
            "run",
            "unit",
            "command",
            "manager",
            "dispatcher",
            "rule",
        )
    )
    update_root = tmp_path / "updates"
    return persistent, witnesses, update_root


@pytest.mark.parametrize("index", range(10))
def test_any_prior_installation_witness_refuses_fresh_admission(tmp_path, index):
    persistent, witnesses, update_root = setup(tmp_path)
    witnesses[index].touch()
    with pytest.raises(OSError):
        persistent.provision_fresh(witnesses=witnesses, update_root=update_root)
    assert not persistent.path.parent.exists()


def test_dangling_witness_is_history_and_never_followed(tmp_path):
    persistent, witnesses, update_root = setup(tmp_path)
    witnesses[0].symlink_to(tmp_path / "missing")
    with pytest.raises(OSError):
        persistent.provision_fresh(witnesses=witnesses, update_root=update_root)
    assert not persistent.path.parent.exists()


@pytest.mark.parametrize("history", ["request.json", "status.json", "backup", "staged", "other"])
def test_update_history_cannot_be_misclassified_as_a_launcher_lock(tmp_path, history):
    persistent, witnesses, update_root = setup(tmp_path)
    update_root.mkdir(mode=0o700)
    (update_root / "lock").touch(mode=0o600)
    (update_root / history).touch()
    with pytest.raises(OSError):
        persistent.provision_fresh(witnesses=witnesses, update_root=update_root)
    assert not persistent.path.parent.exists()


@pytest.mark.parametrize("mode", [0o600, 0o644])
def test_only_a_private_launcher_lock_allows_clear_fresh_state(tmp_path, mode):
    persistent, witnesses, update_root = setup(tmp_path)
    update_root.mkdir(mode=0o700)
    (update_root / "lock").touch(mode=mode)
    state = persistent.provision_fresh(witnesses=witnesses, update_root=update_root)
    assert state == {
        "version": 2,
        "panic_stop_latched": False,
        "household_stop_latched": False,
        "panic_recovery_failed": False,
        "control_revision_required": False,
    }
    assert persistent.read() == state


@pytest.mark.parametrize(
    "damage", ["folder-mode", "folder-link", "lock-link", "lock-mode", "lock-data"]
)
def test_untrusted_or_unexpected_launcher_state_refuses_fresh_admission(tmp_path, damage):
    persistent, witnesses, update_root = setup(tmp_path)
    update_root.mkdir(mode=0o700)
    lock = update_root / "lock"
    lock.touch(mode=0o600)
    if damage == "folder-mode":
        update_root.chmod(0o755)
    elif damage == "folder-link":
        moved = tmp_path / "moved"
        update_root.rename(moved)
        update_root.symlink_to(moved, target_is_directory=True)
    elif damage == "lock-link":
        moved = tmp_path / "moved"
        lock.rename(moved)
        lock.symlink_to(moved)
    elif damage == "lock-mode":
        lock.chmod(0o666)
    else:
        lock.write_text("unexpected old state")
    with pytest.raises(OSError):
        persistent.provision_fresh(witnesses=witnesses, update_root=update_root)
    assert not persistent.path.parent.exists()


def test_existing_guard_is_never_cleared_by_a_fresh_producer(tmp_path):
    persistent, witnesses, update_root = setup(tmp_path)
    stopped = {
        "version": 2,
        "panic_stop_latched": True,
        "household_stop_latched": True,
        "panic_recovery_failed": True,
        "control_revision_required": True,
    }
    persistent.write(stopped)
    assert persistent.provision_fresh(witnesses=witnesses, update_root=update_root) == stopped
    persistent.path.unlink()
    with pytest.raises(OSError):
        persistent.provision_fresh(witnesses=witnesses, update_root=update_root)


def test_cli_fresh_producer_requires_real_launcher_lock_before_writing(
    tmp_path, monkeypatch, capsys
):
    persistent, witnesses, update_root = setup(tmp_path)
    update_root.mkdir(mode=0o700)
    lock = update_root / "lock"
    lock.touch(mode=0o600)
    monkeypatch.setattr(module, "PanicGuard", lambda: persistent)
    monkeypatch.setattr(module, "PANIC_FRESH_WITNESSES", witnesses, raising=False)
    monkeypatch.setattr(module, "PANIC_FRESH_UPDATE_ROOT", update_root, raising=False)
    monkeypatch.setattr(module, "PANIC_INSTALL_LOCK_FD", -1, raising=False)
    assert module.main(["--provision-fresh"]) == 1
    assert capsys.readouterr().out == ""
    assert not persistent.path.parent.exists()
    with lock.open("r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setattr(module, "PANIC_INSTALL_LOCK_FD", handle.fileno())
        assert module.main(["--provision-fresh"]) == 0
        assert '"panic_stop_latched": false' in capsys.readouterr().out

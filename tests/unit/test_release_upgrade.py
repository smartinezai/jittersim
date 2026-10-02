"""Exercise upgrades from the service layout observed on the live Pi."""

import importlib.util
import subprocess
from pathlib import Path

import pytest


def load_script(name):
    path = Path(__file__).resolve().parents[2] / "deploy/raspberry-pi" / name
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


services = load_script("retire-services.py")
release = load_script("replace-source.py")


RETIRED = ("jittersim-ap.service", "old-example.service")


def test_this_release_retires_nothing():
    assert services.OBSOLETE_SERVICES == ()


def test_retirement_preserves_current_services_and_running_wifi(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "OBSOLETE_SERVICES", RETIRED)
    units = tmp_path / "units"
    units.mkdir()
    backup = tmp_path / "private-backup"
    for name in (*services.OBSOLETE_SERVICES, "jittersim-helper.service", "other.service"):
        (units / name).write_text("original " + name)
    calls = []
    services.retire_services(units, backup, run=calls.append, check=lambda _: None,
                            clear=lambda _: None)
    assert {item.name for item in units.iterdir()} == {"jittersim-helper.service", "other.service"}
    assert ["disable", "jittersim-ap.service"] in calls
    assert not any("--now" in call and "jittersim-ap.service" in call for call in calls)
    assert not any("stop" in call for call in calls)
    for name in services.OBSOLETE_SERVICES:
        assert (backup / name).read_text() == "original " + name
        assert (backup / name).stat().st_mode & 0o777 == 0o600
    assert backup.stat().st_mode & 0o777 == 0o700
    services.retire_services(units, backup, run=calls.append, check=lambda _: None,
                            clear=lambda _: None)
    assert calls.count(["disable", "jittersim-ap.service"]) == 1


def test_a_failed_retirement_keeps_the_service_and_recovery_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "OBSOLETE_SERVICES", RETIRED)
    units = tmp_path / "units"
    units.mkdir()
    name = services.OBSOLETE_SERVICES[0]
    (units / name).write_text("recover this")

    def fail(command):
        raise subprocess.CalledProcessError(1, command)

    with pytest.raises(subprocess.CalledProcessError):
        services.retire_services(units, tmp_path / "backup", run=fail, check=lambda _: None,
                                clear=lambda _: None)
    assert (units / name).read_text() == "recover this"
    assert (tmp_path / "backup" / name).read_text() == "recover this"


@pytest.mark.parametrize("returncode", [0, 1])
def test_active_or_unreadable_updater_prevents_retirement(tmp_path, returncode):
    (tmp_path / "jittersim-update.service").touch()
    with pytest.raises(RuntimeError):
        services.check_update_idle(
            tmp_path, run=lambda *a, **k: subprocess.CompletedProcess(a, returncode),
        )


@pytest.mark.parametrize("returncode", [3, 4])
def test_inactive_or_unloaded_updater_allows_upgrade(tmp_path, returncode):
    (tmp_path / "jittersim-update.service").touch()
    services.check_update_idle(
        tmp_path, run=lambda *a, **k: subprocess.CompletedProcess(a, returncode),
    )


@pytest.mark.parametrize("state", [0, 1, 4])
def test_only_failed_units_have_their_failure_state_reset(state):
    calls = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, state)

    services.clear_failure("jittersim-dns.service", run=run)
    assert (["systemctl", "reset-failed", "jittersim-dns.service"] in calls) == (state == 0)


def source_tree(root):
    for name in ("src", "deploy"):
        (root / name).mkdir(parents=True)
        (root / name / "current.py").write_text("current release")
    for name in ("pyproject.toml", "README.md"):
        (root / name).write_text("new " + name)


def test_source_replacement_removes_old_modules_and_preserves_settings(tmp_path):
    source, installed = tmp_path / "source", tmp_path / "installed"
    source_tree(source)
    source_tree(installed)
    (installed / "src/obsolete.py").touch()
    (installed / "deploy/obsolete.service").touch()
    (installed / "build/lib").mkdir(parents=True)
    (installed / "build/lib/obsolete.py").touch()
    (installed / ".venv").mkdir()
    (installed / ".venv/keep").write_text("keep environment")
    release.replace_source(source, installed)
    release.replace_source(source, installed)
    assert not (installed / "src/obsolete.py").exists()
    assert not (installed / "deploy/obsolete.service").exists()
    assert not (installed / "build").exists()
    assert (installed / "src/current.py").read_text() == "current release"
    assert (installed / ".venv/keep").read_text() == "keep environment"


def test_incomplete_source_does_not_change_the_installed_release(tmp_path):
    source, installed = tmp_path / "source", tmp_path / "installed"
    source_tree(source)
    source_tree(installed)
    (source / "README.md").unlink()
    (installed / "src/current.py").write_text("working release")
    with pytest.raises(FileNotFoundError):
        release.replace_source(source, installed)
    assert (installed / "src/current.py").read_text() == "working release"
    assert not list(installed.glob(".release-*"))


def test_failed_folder_swap_restores_the_previous_release(tmp_path, monkeypatch):
    source, installed = tmp_path / "source", tmp_path / "installed"
    source_tree(source)
    source_tree(installed)
    (installed / "src/current.py").write_text("working release")
    original_rename = Path.rename

    def fail_second_folder(path, target):
        if path.name == "deploy" and path.parent.name.startswith(".release-"):
            raise OSError("disk error during replacement")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_second_folder)
    with pytest.raises(OSError, match="disk error"):
        release.replace_source(source, installed)
    assert (installed / "src/current.py").read_text() == "working release"
    assert (installed / "deploy/current.py").read_text() == "current release"
    assert not list(installed.glob(".release-*"))


def test_installing_from_the_current_folder_clears_old_build_output(tmp_path):
    source_tree(tmp_path)
    (tmp_path / "build").mkdir()
    (tmp_path / "build/obsolete.py").touch()
    release.replace_source(tmp_path, tmp_path)
    assert not (tmp_path / "build").exists()
    assert (tmp_path / "src/current.py").read_text() == "current release"


def test_failed_recovery_keeps_the_original_folders(tmp_path, monkeypatch):
    source, installed = tmp_path / "source", tmp_path / "installed"
    source_tree(source)
    source_tree(installed)
    (installed / "src/current.py").write_text("working release")
    original_rename = Path.rename

    def fail_activation_and_recovery(path, target):
        if (path.name == "deploy" and path.parent.name.startswith(".release-")) or (
            path.name == "src" and path.parent.name == "previous"
        ):
            raise OSError("disk error")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_activation_and_recovery)
    with pytest.raises(RuntimeError, match="Old folders are kept"):
        release.replace_source(source, installed)
    recovery = next(installed.glob(".release-*/previous"))
    assert (recovery / "src/current.py").read_text() == "working release"

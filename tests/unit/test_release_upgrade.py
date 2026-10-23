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


services = load_script("check-update-idle.py")
release = load_script("replace-source.py")


@pytest.mark.parametrize("returncode", [0, 1])
def test_active_or_unreadable_updater_prevents_install(tmp_path, returncode):
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


def test_the_release_label_of_the_source_is_installed_with_it(tmp_path):
    source, installed = tmp_path / "source", tmp_path / "installed"
    source_tree(source)
    source_tree(installed)
    (installed / ".jittersim-release.json").write_text('{"version": "old1234"}')
    (source / ".jittersim-release.json").write_text('{"version": "new5678"}')
    release.replace_source(source, installed)
    assert (installed / ".jittersim-release.json").read_text() == '{"version": "new5678"}'


def test_a_source_without_a_label_removes_the_old_label(tmp_path):
    source, installed = tmp_path / "source", tmp_path / "installed"
    source_tree(source)
    source_tree(installed)
    (installed / ".jittersim-release.json").write_text('{"version": "old1234"}')
    release.replace_source(source, installed)
    assert not (installed / ".jittersim-release.json").exists()
    assert (installed / "src/current.py").read_text() == "current release"


def test_a_failed_swap_restores_the_old_label(tmp_path, monkeypatch):
    source, installed = tmp_path / "source", tmp_path / "installed"
    source_tree(source)
    source_tree(installed)
    (installed / ".jittersim-release.json").write_text('{"version": "old1234"}')
    (source / ".jittersim-release.json").write_text('{"version": "new5678"}')
    original_rename = Path.rename

    def fail_label(path, target):
        if path.name == ".jittersim-release.json" and path.parent.name.startswith(".release-"):
            raise OSError("disk error during replacement")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_label)
    with pytest.raises(OSError, match="disk error"):
        release.replace_source(source, installed)
    assert (installed / ".jittersim-release.json").read_text() == '{"version": "old1234"}'
    assert not list(installed.glob(".release-*"))

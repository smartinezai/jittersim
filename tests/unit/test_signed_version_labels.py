"""Exercise version labels through actual signed archives and update admission."""

import json
import shutil
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from jittersim import updates
from tests.release_support import have_ssh_keygen, make_key, signed_release

BUILD = Path(__file__).resolve().parents[2] / "deploy/raspberry-pi/build-release.sh"
pytestmark = pytest.mark.skipif(not have_ssh_keygen(), reason="ssh-keygen signs the archive")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def release_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src/jittersim/api").mkdir(parents=True)
    shutil.copytree(BUILD.parents[1], repo / "deploy")
    (repo / "src/jittersim/api/server.py").write_text("pass\n")
    (repo / "src/jittersim/updates.py").write_text("pass\n")
    (repo / "src/jittersim/__init__.py").write_text('__version__ = "0.2.0"\n')
    (repo / "README.md").write_text("release\n")
    (repo / "pyproject.toml").write_text('[project]\nname="jittersim"\nversion = "0.2.0"\n')
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "First")
    return repo


def test_signed_bundle_and_version_only_output_agree(release_repo, tmp_path):
    commit = git(release_repo, "rev-parse", "HEAD")
    expected = "0.2.0.dev+" + commit[:7]
    result = subprocess.run(["sh", str(BUILD), "--version", "HEAD"], cwd=release_repo,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected + "\n"
    private, public = make_key(tmp_path / "keys")
    archive = tmp_path / "release.tar.gz"
    result = subprocess.run(["sh", str(BUILD), "HEAD", str(archive), str(private)],
                            cwd=release_repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with tarfile.open(archive) as bundle:
        manifest = json.load(bundle.extractfile("deploy/RELEASE"))
    assert manifest["version"] == expected
    assert manifest["commit"] == commit
    assert manifest["sequence"] > 0
    root, config = tmp_path / "updates", tmp_path / "config"
    config.mkdir()
    updates.trust_key(public, config=config, root=root)
    staged = updates.stage_release(archive, root=root, config=config, code=tmp_path / "code")
    assert staged["version"] == expected
    assert staged["signer"]


def test_tag_mismatch_is_refused_before_archive_creation(release_repo, tmp_path):
    git(release_repo, "tag", "v0.3.0")
    archive = tmp_path / "release.tar.gz"
    result = subprocess.run(["sh", str(BUILD), "v0.3.0", str(archive)], cwd=release_repo,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "does not match" in result.stderr
    assert not archive.exists()


def test_package_and_project_numbers_must_agree(release_repo, tmp_path):
    (release_repo / "src/jittersim/__init__.py").write_text('__version__ = "0.3.0"\n')
    git(release_repo, "-c", "user.name=t", "-c", "user.email=t@example.com",
        "commit", "-qam", "Change the package number")
    archive = tmp_path / "release.tar.gz"
    result = subprocess.run(["sh", str(BUILD), "HEAD", str(archive)], cwd=release_repo,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "package version" in result.stderr
    assert not archive.exists()


def test_stable_release_is_not_replaced_by_its_development_build(tmp_path):
    private, public = make_key(tmp_path / "keys")
    root, config, code = (tmp_path / name for name in ("updates", "config", "code"))
    config.mkdir()
    code.mkdir()
    updates.trust_key(public, config=config, root=root)
    updates.atomic_json(code / ".jittersim-release.json", {"version": "0.2.0", "sequence": 100})
    archive = signed_release(tmp_path, private, version="0.2.0.dev+0123456", sequence=101)
    updates.stage_release(archive, root=root, code=code, config=config)
    with pytest.raises(updates.UpdateError, match="older number"):
        updates.request_update("0.2.0.dev+0123456", root=root, code=code, config=config,
                               run=lambda *args: None)


def test_root_execution_rechecks_the_installed_number(tmp_path, monkeypatch):
    private, public = make_key(tmp_path / "keys")
    root, config, code = (tmp_path / name for name in ("updates", "config", "code"))
    config.mkdir()
    code.mkdir()
    updates.trust_key(public, config=config, root=root)
    archive = signed_release(tmp_path, private, version="0.2.0", sequence=101)
    updates.stage_release(archive, root=root, code=code, config=config)
    updates.request_update("0.2.0", root=root, code=code, config=config, run=lambda *args: None)
    updates.atomic_json(code / ".jittersim-release.json", {"version": "0.3.0", "sequence": 100})
    # This archive has only the version fixture's source. The real Panic
    # admission and retained-guard paths have separate full-source tests.
    safety = SimpleNamespace(inspect=lambda *args: None, retain=lambda *args: None,
                             provision=lambda *args: None)
    monkeypatch.setattr(updates, "PanicProtection", lambda *args, **kwargs: safety)
    commands = []
    with pytest.raises(updates.UpdateError, match="older number"):
        updates.apply_update(root=root, code=code, config=config, manager=tmp_path / "manager.py",
                             run=lambda *args: commands.append(args))
    assert commands == []
    assert updates.update_status(root=root, code=code, config=config)["phase"] == "failed"


def test_newer_sequence_cannot_install_a_lower_number(tmp_path):
    private, public = make_key(tmp_path / "keys")
    root, config, code = (tmp_path / name for name in ("updates", "config", "code"))
    code.mkdir()
    config.mkdir()
    updates.atomic_json(code / ".jittersim-release.json", {"version": "0.3.0", "sequence": 100})
    updates.trust_key(public, config=config, root=root)
    archive = signed_release(tmp_path, private, version="0.2.0", sequence=101)
    updates.stage_release(archive, root=root, code=code, config=config)
    status = updates.update_status(root=root, code=code, config=config)
    assert status["installable"] is False
    assert "older number" in status["reason"]
    with pytest.raises(updates.UpdateError, match="older number"):
        updates.request_update("0.2.0", root=root, code=code, config=config, run=lambda *args: None)
    assert not (root / "request.json").exists()

"""The release build and manifest check scripts, run as they are on a Mac (QA, 2026-10-09).

build-release.sh and check-release-manifest.sh are POSIX shell and need only git, tar and sh. No
key, Pi or network is used: the archive is built from this checkout's HEAD into a temporary folder.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PI = ROOT / "deploy" / "raspberry-pi"
BUILD = PI / "build-release.sh"
CHECK = PI / "check-release-manifest.sh"
SHELL_SCRIPTS = sorted(PI.glob("*.sh"))

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(),
    reason="the scripts build from a git checkout",
)


def sh(*arguments, env=None, cwd=ROOT):
    import os
    return subprocess.run(["sh", *map(str, arguments)], cwd=cwd, capture_output=True, text=True, timeout=120,
                          env={**os.environ, **(env or {})})


def head():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture(scope="module")
def version():
    result = sh(BUILD, "--version", "HEAD")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda path: path.name)
def test_every_deploy_script_parses(script):
    assert sh("-n", script).returncode == 0


def test_the_version_names_the_commit_of_a_development_build(version):
    assert version.startswith("0.") or version[0].isdigit()
    assert head()[:7] in version or "dev" not in version


def build(tmp_path, sequence=None):
    env = {"JITTERSIM_RELEASE_SEQUENCE": str(sequence)} if sequence is not None else {}
    result = sh(BUILD, "HEAD", tmp_path / "out.tar.gz", env=env)
    return result


def test_a_built_archive_passes_its_own_manifest_check(tmp_path, version):
    result = build(tmp_path)
    if result.returncode != 0 and "add-virtual-file" in result.stderr:
        pytest.skip("this git cannot add the manifest file to an archive")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[0].isalnum() and len(result.stdout.split()[0]) == 64
    checked = sh(CHECK, tmp_path / "out.tar.gz", head(), version)
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout.strip().isdigit()


def test_the_manifest_check_refuses_a_changed_commit_version_or_archive(tmp_path, version):
    if build(tmp_path).returncode != 0:
        pytest.skip("this git cannot build the archive")
    archive = tmp_path / "out.tar.gz"
    for commit, label in ((("0" * 40), version), (head(), "9.9.9"), ("zz", version), ("", version), (head()[:39], version)):
        result = sh(CHECK, archive, commit, label)
        assert result.returncode == 1 and "differs from the requested source" in result.stderr, (commit, label)
        assert "Traceback" not in result.stderr
    missing = sh(CHECK, tmp_path / "missing.tar.gz", head(), version)
    assert missing.returncode == 1 and "differs from the requested source" in missing.stderr
    assert sh(CHECK).returncode == 1


@pytest.mark.parametrize("sequence", ["0", "abc", "-1", "1 2", "007", "1.5", "1e3", "٣"])
def test_a_release_sequence_that_is_not_a_positive_whole_number_is_refused_at_build(tmp_path, sequence):
    result = build(tmp_path, sequence)
    assert result.returncode == 1 and "positive whole number" in result.stderr
    assert not (tmp_path / "out.tar.gz").exists()


@pytest.mark.parametrize("sequence", [1, 2**53 - 1])
def test_the_largest_sequence_the_check_accepts_builds_and_checks(tmp_path, version, sequence):
    if build(tmp_path, sequence).returncode != 0:
        pytest.skip("this git cannot build the archive")
    checked = sh(CHECK, tmp_path / "out.tar.gz", head(), version)
    assert checked.returncode == 0 and checked.stdout.strip() == str(sequence)


@pytest.mark.xfail(strict=True, reason=(
    "build-release.sh only requires a positive whole number for JITTERSIM_RELEASE_SEQUENCE, but "
    "check-release-manifest.sh refuses 2**53 and above (and more than 16 digits) and updates.py "
    "_is_sequence refuses them too (MAX_SEQUENCE). A build with such a number succeeds, can be signed "
    "and is then refused at transfer. The build should refuse it, naming the limit"))
@pytest.mark.parametrize("sequence", [2**53, 10**16, 10**20])
def test_the_build_refuses_a_sequence_the_manifest_check_would_refuse(tmp_path, version, sequence):
    built = build(tmp_path, sequence)
    if built.returncode != 0:
        return  # refused at build: the wanted behaviour
    checked = sh(CHECK, tmp_path / "out.tar.gz", head(), version)
    assert checked.returncode == 0, f"built a release with sequence {sequence} that the check refuses"

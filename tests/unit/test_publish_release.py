"""publish-release.sh with a fake gh on PATH: nothing reaches GitHub."""

import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests.release_support import have_ssh_keygen, make_key

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "raspberry-pi" / "publish-release.sh"

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
files = {name: os.path.exists(name) for name in sys.argv[4:6]}
sizes = {name: os.path.getsize(name) for name in sys.argv[4:6] if os.path.exists(name)}
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "cwd_files": sorted(os.listdir("."))}) + "\n")
sys.exit(int(os.environ.get("FAKE_GH_EXIT", "0")))
'''


@pytest.fixture
def setup(tmp_path):
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    gh = bin_directory / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "deploy", repo / "deploy")
    for name in ("pyproject.toml", "README.md"):
        (repo / name).write_text(name)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n")
    env = {"PATH": f"{bin_directory}:{os.environ['PATH']}", "HOME": str(tmp_path / "home"),
           "FAKE_LOG": str(tmp_path / "gh.log"), "TMPDIR": str(tmp_path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    (tmp_path / "home").mkdir()
    for command in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(repo), *command], check=True, env=env)
    return {"repo": repo, "env": env, "log": tmp_path / "gh.log", "tmp": tmp_path}


def run(setup, *arguments, env=None):
    script = setup["repo"] / "deploy" / "raspberry-pi" / "publish-release.sh"
    return subprocess.run(["sh", str(script), *arguments], capture_output=True, text=True,
                          env={**setup["env"], **(env or {})}, timeout=120, cwd=setup["repo"])


def calls(setup):
    return [json.loads(line) for line in setup["log"].read_text().splitlines()] \
        if setup["log"].exists() else []


@pytest.mark.skipif(not have_ssh_keygen(), reason="ssh-keygen signs the release")
def test_it_builds_signs_and_creates_a_release_with_exactly_two_assets(setup):
    private, _ = make_key(setup["tmp"] / "keys")
    result = run(setup, "--signing-key", str(private), "HEAD")
    assert result.returncode == 0, result.stderr
    [call] = calls(setup)
    short = subprocess.run(["git", "-C", str(setup["repo"]), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    assert call["argv"][:5] == ["release", "create", f"release-{short}",
                                "jittersim-release.tar.gz", "jittersim-release.tar.gz.sig"]
    # gh ran next to exactly the two files it was told to upload.
    assert call["cwd_files"] == ["jittersim-release.tar.gz", "jittersim-release.tar.gz.sig"]
    assert not list(setup["tmp"].glob("jittersim-publish.*")), "the build folder is removed"


def test_dry_run_prints_the_commands_and_runs_nothing(setup):
    result = run(setup, "--dry-run", "HEAD")
    assert result.returncode == 0, result.stderr
    assert "build-release.sh" in result.stdout
    assert "gh release create release-" in result.stdout
    assert "jittersim-release.tar.gz.sig" in result.stdout
    assert calls(setup) == []
    assert not list(setup["tmp"].glob("jittersim-publish.*"))


def test_a_dirty_tree_is_refused_unless_allowed(setup):
    (setup["repo"] / "src" / "a.py").write_text("x = 2\n")
    refused = run(setup, "--dry-run", "HEAD")
    assert refused.returncode == 1
    assert "uncommitted changes" in refused.stderr
    assert calls(setup) == []
    allowed = run(setup, "--dry-run", "--allow-dirty", "HEAD")
    assert allowed.returncode == 0
    assert "not part of the archive" in allowed.stdout


def test_a_missing_key_or_gh_failure_gives_a_plain_message(setup):
    missing = run(setup, "--signing-key", str(setup["tmp"] / "nokey"), "HEAD")
    assert missing.returncode == 2 and "no release signing key" in missing.stderr
    assert calls(setup) == []
    if have_ssh_keygen():
        private, _ = make_key(setup["tmp"] / "keys")
        failed = run(setup, "--signing-key", str(private), "HEAD", env={"FAKE_GH_EXIT": "1"})
        assert failed.returncode == 1 and "gh could not create the release" in failed.stderr


def test_bad_arguments_are_refused(setup):
    for arguments in (["--bogus", "HEAD"], [], ["--tag", "bad tag", "HEAD"], ["-x"],
                      ["nosuchref", "--dry-run"]):
        assert run(setup, *arguments).returncode == 2, arguments

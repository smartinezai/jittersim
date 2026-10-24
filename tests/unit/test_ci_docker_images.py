"""Docker runners must use their own build, even when other worktrees build too."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit import test_local_ci
from tests.unit.test_local_ci import ROOT, run_script

scratch_repository = test_local_ci.scratch_repository


@pytest.fixture
def docker_stub(tmp_path, monkeypatch):
    directory = tmp_path / "docker-bin"
    directory.mkdir()
    log = tmp_path / "docker-calls.jsonl"
    program = directory / "docker"
    program.write_text("#!" + str(Path(sys.executable).resolve()) + "\n" + r'''
import hashlib, json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['DOCKER_CALLS'], 'a') as stream:
    stream.write(json.dumps(args) + '\n')
if args[0] == 'info':
    if '--format' in args:
        print(8 if 'NCPU' in args[-1] else 8589934592)
elif args[0] == 'build':
    if os.environ.get('BUILD_FAIL'):
        sys.exit(17)
    image = 'sha256:' + hashlib.sha256(args[-1].encode()).hexdigest()
    if not os.environ.get('MISSING_ID'):
        Path(args[args.index('--iidfile') + 1]).write_text(os.environ.get('IMAGE_ID', image))
    if os.environ.get('BUILD_BARRIER'):
        barrier = Path(os.environ['BUILD_BARRIER'])
        (barrier / image[7:]).touch()
        until = time.monotonic() + 15
        while len(list(barrier.iterdir())) < 2:
            if time.monotonic() > until:
                sys.exit(18)
            time.sleep(0.01)
elif args[0] == 'run':
    sys.exit(int(os.environ.get('RUN_EXIT', '0')))
''')
    program.chmod(0o755)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("DOCKER_CALLS", str(log))
    monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ["PATH"])
    return directory, log


def calls(log, command):
    return [args for args in map(json.loads, log.read_text().splitlines()) if args[0] == command]


def release_run(scratch_repository, tmp_path, docker_stub):
    repository, _git = scratch_repository
    (repository / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    version = "%d.%d" % sys.version_info[:2]
    venv_python = repository / (".venv-local-ci-" + version) / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    # Environment setup and wheel preparation are unrelated to image selection. No pip or network.
    venv_python.write_text("#!/bin/sh\nexit 0\n")
    venv_python.chmod(0o755)
    return run_script(
        "--allow-dirty", "--python", version, "release-checks", cwd=repository,
        script=repository / "scripts" / "local-ci.sh", path_first=[docker_stub[0]])


def test_release_checks_run_the_built_image_and_keep_its_identity(
        scratch_repository, tmp_path, docker_stub):
    result = release_run(scratch_repository, tmp_path, docker_stub)
    assert result.returncode == 0, result.stdout + result.stderr
    build, = calls(docker_stub[1], "build")
    assert "-t" not in build
    image_file = Path(build[build.index("--iidfile") + 1])
    image = image_file.read_text()
    runs = calls(docker_stub[1], "run")
    assert len(runs) == 13
    assert all(image in args and "jittersim-wifi-release" not in args for args in runs)
    assert "release-image-default-command" in result.stdout
    assert image in (image_file.parent / "meta.tsv").read_text()


@pytest.mark.parametrize("fault,value", [
    ("BUILD_FAIL", "1"), ("MISSING_ID", "1"), ("IMAGE_ID", "sha256:bad"),
    ("IMAGE_ID", "sha256:" + "z" * 64), ("IMAGE_ID", "jittersim-wifi-release"),
])
def test_release_checks_never_run_after_a_failed_build_or_invalid_id(
        scratch_repository, tmp_path, docker_stub, monkeypatch, fault, value):
    monkeypatch.setenv(fault, value)
    result = release_run(scratch_repository, tmp_path, docker_stub)
    assert result.returncode == 1, result.stdout + result.stderr
    assert not calls(docker_stub[1], "run")
    expected = "FAIL" if fault == "BUILD_FAIL" else "BLOCKED"
    assert expected in result.stdout


def kernel_copy(directory):
    script = directory / "tests" / "kernel" / "run-in-docker.sh"
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "tests" / "kernel" / "run-in-docker.sh", script)
    return script


def test_simultaneous_kernel_builds_run_their_own_images_and_clean_temp_files(
        tmp_path, docker_stub, monkeypatch):
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    image_temp = tmp_path / "image-temp"
    image_temp.mkdir()
    monkeypatch.setenv("BUILD_BARRIER", str(barrier))
    monkeypatch.setenv("TMPDIR", str(image_temp))
    repositories = [tmp_path / "worktree-a", tmp_path / "worktree-b"]
    processes = [subprocess.Popen(["sh", str(kernel_copy(path))], cwd=path)
                 for path in repositories]
    try:
        assert [process.wait(timeout=30) for process in processes] == [0, 0]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    builds = calls(docker_stub[1], "build")
    runs = calls(docker_stub[1], "run")
    expected = {"sha256:" + hashlib.sha256(str(path).encode()).hexdigest() for path in repositories}
    assert {args[-1] for args in runs} == expected
    assert all("-t" not in args for args in builds)
    assert len({args[args.index("--iidfile") + 1] for args in builds}) == 2
    assert list(image_temp.iterdir()) == []


@pytest.mark.parametrize("fault,value,expected", [
    ("BUILD_FAIL", "1", 17), ("MISSING_ID", "1", 1), ("IMAGE_ID", "sha256:bad", 1),
    ("RUN_EXIT", "23", 23),
])
def test_kernel_runner_stops_on_build_failure_and_preserves_run_exit(
        tmp_path, docker_stub, monkeypatch, fault, value, expected):
    monkeypatch.setenv(fault, value)
    image_temp = tmp_path / "image-temp"
    image_temp.mkdir()
    monkeypatch.setenv("TMPDIR", str(image_temp))
    result = subprocess.run(["sh", str(kernel_copy(tmp_path / "repository"))], timeout=30)
    assert result.returncode == expected
    assert len(calls(docker_stub[1], "run")) == (1 if fault == "RUN_EXIT" else 0)
    assert list(image_temp.iterdir()) == []

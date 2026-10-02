"""Verify isolated container ownership and truthful failure evidence without Docker."""

import io
import json
import subprocess
import tarfile
from contextlib import contextmanager

import pytest

from tests.browser import gui_container
from tests.browser.gui_capture import STATES

COMMIT = "a" * 40
BASE = "python:3.13-slim-bookworm@sha256:" + "b" * 64


def test_invalid_base_never_calls_docker(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(gui_container.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    for value in ("python:latest", "python:3.13-slim-bookworm", BASE + ";touch /tmp/x",
                  "python:3.12-slim-bookworm@sha256:" + "b" * 64):
        with pytest.raises(ValueError):
            gui_container.run(value, tmp_path)
    assert calls == []


def test_dirty_source_never_calls_docker(tmp_path, monkeypatch):
    calls = []

    def execute(command, **_kwargs):
        calls.append(command)
        if "--show-toplevel" in command:
            return subprocess.CompletedProcess(command, 0, stdout=str(tmp_path) + "\n")
        if "HEAD" in command:
            return subprocess.CompletedProcess(command, 0, stdout=COMMIT + "\n")
        return subprocess.CompletedProcess(command, 0, stdout=b" M src/example.py\n")

    monkeypatch.setattr(gui_container.subprocess, "run", execute)
    with pytest.raises(ValueError, match="source changes"):
        gui_container.run(BASE, tmp_path)
    assert all(command[0] == "git" for command in calls)
    assert not (tmp_path / "test-results").exists()


def test_run_timeout_cleans_only_owned_resources_and_records_failure(tmp_path, monkeypatch):
    @contextmanager
    def context(*_args):
        yield tmp_path

    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs))
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="sha256:" + "c" * 64 + "\n")
        if command[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="secret-value")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(gui_container, "clean_source", lambda *_args: (tmp_path, COMMIT))
    monkeypatch.setattr(gui_container, "source_context", context)
    monkeypatch.setattr(gui_container.subprocess, "run", execute)
    with pytest.raises(subprocess.TimeoutExpired):
        gui_container.run(BASE, tmp_path)
    directory = next((tmp_path / "test-results").iterdir())
    text = (directory / "container-report.json").read_text()
    report = json.loads(text)
    assert report["status"] == "failed"
    assert report["complete"] is False
    assert report["stage"] == "run"
    assert report["built_image_id"] == "sha256:" + "c" * 64
    assert "secret-value" not in text
    assert calls[-2][0] == ["docker", "rm", "--force", report["container_name"]]
    assert calls[-1][0] == ["docker", "image", "rm", report["image_tag"]]
    assert calls[0][1]["timeout"] == 900
    runtime = next(item for item in calls if item[0][:2] == ["docker", "run"])
    assert runtime[1]["timeout"] == 1500
    assert runtime[0][runtime[0].index("--name") + 1] == report["container_name"]
    assert "JITTERSIM_GUI_SOURCE_COMMIT=" + COMMIT in runtime[0]
    assert "JITTERSIM_GUI_SOURCE_DIRTY=0" in runtime[0]
    assert report["image_tag"] != report["container_name"]


def test_cleanup_has_one_total_deadline_and_no_shared_operations(monkeypatch):
    times = iter((0, 0, 16))
    monkeypatch.setattr(gui_container.time, "monotonic", lambda: next(times))
    calls = []

    def timeout(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(gui_container.subprocess, "run", timeout)
    result = gui_container.cleanup_owned("owned-container", "owned-image")
    assert calls[0][0] == ["docker", "rm", "--force", "owned-container"]
    assert calls[0][1]["timeout"] == 15
    assert len(calls) == 1
    assert result[-1] == {"resource": "image", "status": "timeout"}


def test_runner_manifest_requires_real_matrix_and_matching_source(tmp_path):
    report = {"status": "complete", "complete": True, "commit": COMMIT,
              "source_dirty": False, "expected_states": 40,
              "states": [{"id": state["id"], "status": "complete"} for state in STATES]}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(report))
    gui_container.verify_runner_manifest(path, COMMIT)
    for change in ({"source_dirty": True}, {"commit": "d" * 40}, {"complete": False},
                   {"states": [{"id": str(index), "status": "complete"} for index in range(40)]}):
        path.write_text(json.dumps({**report, **change}))
        with pytest.raises(RuntimeError):
            gui_container.verify_runner_manifest(path, COMMIT)


def archive_process(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as output:
        for name, value, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.linkname = "/outside"
            member.size = len(value) if kind == tarfile.REGTYPE else 0
            output.addfile(member, io.BytesIO(value) if kind == tarfile.REGTYPE else None)
    buffer.seek(0)

    class Process:
        stdout = buffer

        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

    return Process()


def test_source_archive_ignores_live_files_and_cleans_its_temporary_directory(tmp_path, monkeypatch):
    (tmp_path / "ignored-secret").write_text("not-in-image")

    def archive(command, **_kwargs):
        assert command[command.index(COMMIT) + 1:] == ["pyproject.toml", "README.md", "src", "tests"]
        return archive_process([
            ("tests/browser/Dockerfile", b"ARG BROWSER_BASE_IMAGE\nFROM ${BROWSER_BASE_IMAGE}\n", tarfile.REGTYPE),
            ("src/example.py", b"tracked content", tarfile.REGTYPE),
        ])

    monkeypatch.setattr(gui_container.subprocess, "Popen", archive)
    with gui_container.source_context(tmp_path, COMMIT) as context:
        assert (context / "src/example.py").read_text() == "tracked content"
        assert not (context / "ignored-secret").exists()
        temporary = context.parent
    assert not temporary.exists()


@pytest.mark.parametrize("name,kind", [("../outside", tarfile.REGTYPE),
                                      ("link", tarfile.SYMTYPE)])
def test_unsafe_archive_is_rejected_before_extracting(tmp_path, monkeypatch, name, kind):
    monkeypatch.setattr(gui_container.subprocess, "Popen", lambda *_args, **_kwargs:
                        archive_process([(name, b"", kind)]))
    with pytest.raises(RuntimeError, match="unsafe entry"):
        with gui_container.source_context(tmp_path, COMMIT):
            pytest.fail("Unsafe source must not produce a build context")


@pytest.mark.parametrize("limit,entries", [
    ("bytes", [("large", b"x" * 11, tarfile.REGTYPE)]),
    ("entries", [("one", b"", tarfile.DIRTYPE), ("two", b"", tarfile.DIRTYPE),
                 ("three", b"", tarfile.DIRTYPE)]),
])
def test_archive_limits_reject_before_docker(tmp_path, monkeypatch, limit, entries):
    monkeypatch.setattr(gui_container, "MAX_CONTEXT_BYTES", 10 if limit == "bytes" else 1024)
    monkeypatch.setattr(gui_container, "MAX_CONTEXT_ENTRIES", 2)
    monkeypatch.setattr(gui_container.subprocess, "Popen", lambda *_args, **_kwargs:
                        archive_process(entries))
    with pytest.raises(RuntimeError, match="limit"):
        with gui_container.source_context(tmp_path, COMMIT):
            pytest.fail("Over-limit source must not produce a build context")


def test_preparation_failure_records_failure_without_docker(tmp_path, monkeypatch):
    @contextmanager
    def invalid_context(*_args):
        raise RuntimeError("secret-detail")
        yield

    calls = []
    monkeypatch.setattr(gui_container, "clean_source", lambda *_args: (tmp_path, COMMIT))
    monkeypatch.setattr(gui_container, "source_context", invalid_context)
    monkeypatch.setattr(gui_container.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError):
        gui_container.run(BASE, tmp_path)
    path = next((tmp_path / "test-results").iterdir()) / "container-report.json"
    text = path.read_text()
    report = json.loads(text)
    assert report["status"] == "failed"
    assert report["stage"] == "prepare"
    assert report["cleanup"] == []
    assert "secret-detail" not in text
    assert calls == []


def test_staged_dockerfile_must_honor_requested_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_container.subprocess, "Popen", lambda *_args, **_kwargs:
                        archive_process([("tests/browser/Dockerfile", b"FROM python:latest", tarfile.REGTYPE)]))
    with pytest.raises(RuntimeError, match="pinned base image"):
        with gui_container.source_context(tmp_path, COMMIT):
            pytest.fail("An unpinned Dockerfile must not produce a build context")


CACHED = "jittersim-browser-tests@sha256:" + "e" * 64


def portable_wheel(directory, suffix="py3-none-any", server=b"new server", helper=b"new helper"):
    directory.mkdir(exist_ok=True)
    path = directory / ("jittersim-0.1.0-" + suffix + ".whl")
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("jittersim/__init__.py", b"")
        archive.writestr("jittersim/api/server.py", server)
        archive.writestr("jittersim/networking/helper.py", helper)
        archive.writestr("jittersim/api/access.css", b"safe CSS")
        archive.writestr("jittersim-0.1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
    return path


def test_wheel_requires_one_portable_file_and_records_all_runtime_hashes(tmp_path):
    path = portable_wheel(tmp_path)
    identity = gui_container.wheel_identity(tmp_path)
    assert identity["bytes"] == path.stat().st_size
    assert set(identity["runtime_files_sha256"]) == {
        "jittersim/__init__.py", "jittersim/api/server.py", "jittersim/networking/helper.py",
        "jittersim/api/access.css"}
    portable_wheel(tmp_path, suffix="cp313-cp313-linux_x86_64")
    with pytest.raises(RuntimeError, match="one universal"):
        gui_container.wheel_identity(tmp_path)
    path.unlink()
    with pytest.raises(RuntimeError, match="one universal"):
        gui_container.wheel_identity(tmp_path)


def test_cached_identity_rejects_old_source_tests_and_helper_mismatch(tmp_path, monkeypatch):
    import zipfile
    from types import SimpleNamespace

    wheel_dir = tmp_path / "wheels"
    wheel_path = portable_wheel(wheel_dir)
    identity = gui_container.wheel_identity(wheel_dir)
    site = tmp_path / "site-packages"
    with zipfile.ZipFile(wheel_path) as archive:
        archive.extractall(site)
    package = site / "jittersim/__init__.py"
    server = site / "jittersim/api/server.py"
    tests = "/jittersim-gui-baseline/tests/__init__.py"
    fixture = "/jittersim-gui-baseline/tests/browser/fixture.py"
    monkeypatch.setattr(gui_container, "sys", SimpleNamespace(modules={}))
    gui_container.verify_cached_install(package, server, tests, fixture, identity)
    with pytest.raises(RuntimeError, match="old checkout"):
        gui_container.verify_cached_install(tmp_path / "old/jittersim/__init__.py", server,
                                            tests, fixture, identity)
    with pytest.raises(RuntimeError, match="old tests"):
        gui_container.verify_cached_install(package, server, "/jittersim/tests/__init__.py",
                                            fixture, identity)
    (site / "jittersim/networking/helper.py").write_bytes(b"stale helper")
    with pytest.raises(RuntimeError, match="differs"):
        gui_container.verify_cached_install(package, server, tests, fixture, identity)
    (site / "jittersim/networking/helper.py").write_bytes(b"new helper")
    gui_container.sys.modules["jittersim.old"] = SimpleNamespace(__file__="/jittersim/src/jittersim/old.py")
    with pytest.raises(RuntimeError, match="old source"):
        gui_container.verify_cached_install(package, server, tests, fixture, identity)


def cached_preparation(tmp_path, monkeypatch):
    context_path = tmp_path / "context"
    (context_path / "tests/browser").mkdir(parents=True)
    (context_path / "tests/__init__.py").write_text("")
    (context_path / "tests/browser/gui_container.py").write_text("current tests")
    captured = {}

    @contextmanager
    def context(*_args):
        yield context_path

    def wheel(_context, directory):
        portable_wheel(directory)
        captured["wheel"] = gui_container.wheel_identity(directory)
        return captured["wheel"]

    monkeypatch.setattr(gui_container, "clean_source", lambda *_args: (tmp_path, COMMIT))
    monkeypatch.setattr(gui_container, "source_context", context)
    monkeypatch.setattr(gui_container, "build_host_wheel", wheel)
    return captured


@pytest.mark.parametrize("failure", ["copy-inputs", "start", "copy-artifacts"])
def test_cached_failures_cleanup_without_binds_or_cached_image_mutation(tmp_path, monkeypatch, failure):
    captured = cached_preparation(tmp_path, monkeypatch)
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="sha256:" + "c" * 64 + "\n")
        if command[:2] == ["docker", "cp"] and failure == "copy-inputs":
            raise subprocess.CalledProcessError(1, command, output="secret value")
        if command[:2] == ["docker", "start"] and failure == "start":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="secret value")
        return subprocess.CompletedProcess(command, 0)

    def copy_out(command, **_kwargs):
        calls.append(command)
        if failure == "copy-artifacts":
            raise OSError("secret copy error")
        return archive_process([("cached-runtime.json", b"{}", tarfile.REGTYPE)])

    monkeypatch.setattr(gui_container.subprocess, "run", execute)
    monkeypatch.setattr(gui_container.subprocess, "Popen", copy_out)
    expected = {"copy-inputs": subprocess.CalledProcessError, "start": subprocess.TimeoutExpired,
                "copy-artifacts": OSError}[failure]
    with pytest.raises(expected):
        gui_container.run(None, tmp_path, CACHED)
    directory = next((tmp_path / "test-results").iterdir())
    text = (directory / "container-report.json").read_text()
    report = json.loads(text)
    assert report["verification_mode"] == "cached-runtime"
    assert report["complete"] is False
    assert report["stage"] == failure
    assert report["wheel"] == captured["wheel"]
    assert "secret value" not in text and "secret copy error" not in text
    assert calls[0][-1] == CACHED
    assert all(command[:2] not in (["docker", "build"], ["docker", "pull"]) for command in calls)
    assert calls[-1] == ["docker", "rm", "--force", report["container_name"]]
    create = next(command for command in calls if command[:2] == ["docker", "create"])
    assert "--pull=never" in create and "--rm" not in create
    assert create[create.index("--workdir") + 1] == "/jittersim-gui-baseline"
    assert "PYTHONPATH=" in create and "PYTHONDONTWRITEBYTECODE=1" in create
    assert "--volume" not in create and "--mount" not in create
    assert all(command[:3] != ["docker", "image", "rm"] for command in calls)
    if failure == "start":
        assert report["artifact_copy"]["status"] == "copied-partial"
        assert (directory / "cached-runtime.json").exists()


def test_cached_success_copies_complete_evidence_before_cleanup(tmp_path, monkeypatch):
    captured = cached_preparation(tmp_path, monkeypatch)
    calls = []

    def execute(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="sha256:" + "c" * 64 + "\n")

    def copy_out(command, **_kwargs):
        calls.append(command)
        wheel = captured["wheel"]
        manifest = {"status": "complete", "complete": True, "commit": COMMIT,
                    "source_dirty": False, "expected_states": 40,
                    "runtime_package": {"location_kind": "installed", "server_source_sha256":
                                        wheel["server_source_sha256"]},
                    "states": [{"id": state["id"], "status": "complete"} for state in STATES]}
        return archive_process([
            ("screenshots/manifest.json", json.dumps(manifest).encode(), tarfile.REGTYPE),
            ("axe/manifest.json", json.dumps(manifest).encode(), tarfile.REGTYPE),
            ("cached-runtime.json", json.dumps({"wheel": wheel, "verification_mode": "cached-runtime"}).encode(), tarfile.REGTYPE),
        ])

    monkeypatch.setattr(gui_container.subprocess, "run", execute)
    monkeypatch.setattr(gui_container.subprocess, "Popen", copy_out)
    directory = gui_container.run(None, tmp_path, CACHED)
    report = json.loads((directory / "container-report.json").read_text())
    assert report["complete"] is True and report["artifact_copy"]["status"] == "copied"
    assert (directory / "axe/manifest.json").exists()
    assert calls[-2][:2] == ["docker", "cp"]
    assert calls[-1] == ["docker", "rm", "--force", report["container_name"]]


@pytest.mark.parametrize("name,kind", [("../outside", tarfile.REGTYPE), ("screenshots/link", tarfile.SYMTYPE)])
def test_copy_out_rejects_unsafe_entries(tmp_path, monkeypatch, name, kind):
    monkeypatch.setattr(gui_container.subprocess, "Popen", lambda *_args, **_kwargs:
                        archive_process([(name, b"", kind)]))
    with pytest.raises(RuntimeError, match="unsafe entry"):
        gui_container.copy_container_artifacts("owned-container", tmp_path)


def test_copy_out_byte_limit_checked_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_container, "MAX_RETURN_BYTES", 10)
    monkeypatch.setattr(gui_container.subprocess, "Popen", lambda *_args, **_kwargs:
                        archive_process([("screenshots/large.png", b"x" * 11, tarfile.REGTYPE)]))
    with pytest.raises(RuntimeError, match="byte limit"):
        gui_container.copy_container_artifacts("owned-container", tmp_path)
    assert not (tmp_path / "screenshots/large.png").exists()


def test_cached_reference_and_exclusive_modes_reject_before_inspection(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(gui_container.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    for reference in ("jittersim-browser-tests:latest", "other@sha256:" + "e" * 64):
        with pytest.raises(ValueError):
            gui_container.run(None, tmp_path, reference)
    with pytest.raises(ValueError):
        gui_container.run(BASE, tmp_path, CACHED)
    assert calls == []


def test_host_wheel_timeout_cleans_its_process_group(tmp_path, monkeypatch):
    class Process:
        def wait(self, timeout):
            assert timeout == 120
            raise subprocess.TimeoutExpired("pip-wheel", timeout)

    process = Process()
    commands = []
    cleaned = []

    def start(command, **kwargs):
        commands.append((command, kwargs))
        return process

    monkeypatch.setattr(gui_container.subprocess, "Popen", start)
    monkeypatch.setattr(gui_container, "stop_fixture", lambda value: cleaned.append(value))
    with pytest.raises(subprocess.TimeoutExpired):
        gui_container.build_host_wheel(tmp_path / "context", tmp_path / "wheels")
    assert cleaned == [process]
    assert "--no-deps" in commands[0][0]
    assert commands[0][1]["start_new_session"] is True


def test_oversized_wheel_fails_before_installation(tmp_path, monkeypatch):
    portable_wheel(tmp_path)
    monkeypatch.setattr(gui_container, "MAX_WHEEL_BYTES", 10)
    with pytest.raises(RuntimeError, match="byte limit"):
        gui_container.wheel_identity(tmp_path)

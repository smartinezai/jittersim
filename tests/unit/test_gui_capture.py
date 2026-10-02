"""Protect GUI evidence limits and failure reporting independently of the browser."""

import json
import subprocess
import time
from contextlib import contextmanager

import pytest

from tests.browser import gui_capture
from tests.browser.gui_baseline import source_statistics


def test_artifacts_reject_escape_symlinks_and_oversize(tmp_path):
    store = gui_capture.ArtifactStore(tmp_path / "artifacts", max_bytes=10)
    for name in ("../outside", "/outside", "nested/output", "", "."):
        with pytest.raises(gui_capture.CaptureLimitError):
            store.write_bytes(name, b"x")
    store.write_bytes("first", b"12345")
    with pytest.raises(gui_capture.CaptureLimitError):
        store.write_bytes("second", b"123456")
    assert not (store.root / "second").exists()
    (store.root / "link").symlink_to(tmp_path / "outside")
    with pytest.raises(gui_capture.CaptureLimitError):
        store.write_bytes("link", b"x")


def test_existing_artifacts_cannot_look_like_a_new_run(tmp_path):
    (tmp_path / "manifest.json").write_text('{"complete": true}')
    with pytest.raises(gui_capture.CaptureLimitError):
        gui_capture.ArtifactStore(tmp_path)


def test_completion_rejects_duplicates_missing_and_failed_states():
    expected = ({"id": "a"}, {"id": "b"})
    valid = [{"id": "a", "status": "complete"}, {"id": "b", "status": "complete"}]
    gui_capture.validate_completion(valid, expected)
    for records in (valid[:1], [valid[0], valid[0]],
                    [valid[0], {"id": "b", "status": "failed"}], valid + [valid[0]]):
        with pytest.raises(RuntimeError):
            gui_capture.validate_completion(records, expected)


def test_failed_callback_saves_partial_manifest_without_error_values(tmp_path, monkeypatch):
    @contextmanager
    def session():
        class Browser:
            version = "test-browser"
        yield Browser(), "http://127.0.0.1:1234"

    @contextmanager
    def state(*_args):
        yield object(), {"request_counts": {}}

    def failure(*_args):
        raise ValueError("secret-token-not-for-reports")

    monkeypatch.setattr(gui_capture, "capture_session", session)
    monkeypatch.setattr(gui_capture, "open_state", state)
    monkeypatch.setattr(gui_capture, "current_commit", lambda: "a" * 40)
    with pytest.raises(ValueError):
        gui_capture.run_matrix(tmp_path, failure, "test")
    raw = (tmp_path / "manifest.json").read_text()
    report = json.loads(raw)
    assert not report["complete"]
    assert report["status"] == "failed"
    assert report["states"][0]["status"] == "failed"
    assert len(report["states"]) == 1
    assert "secret-token" not in raw


def test_deadline_restores_outer_limit():
    with pytest.raises(gui_capture.CaptureTimeout):
        with gui_capture.deadline(0.05):
            with gui_capture.deadline(1):
                time.sleep(0.2)


def test_fixture_cleanup_kills_group_after_termination_timeout(monkeypatch):
    class Process:
        pid = 1234
        stdout = None
        waits = 0

        def poll(self):
            return None

        def wait(self, timeout):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("fixture", timeout)

    signals = []
    monkeypatch.setattr(gui_capture.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    process = Process()
    gui_capture.stop_fixture(process)
    assert process.waits == 2
    assert signals == [(1234, gui_capture.signal.SIGTERM), (1234, gui_capture.signal.SIGKILL)]


def test_request_reports_drop_query_and_external_paths():
    assert gui_capture.request_endpoint("http://127.0.0.1:1/api/state?token=secret",
                                        "http://127.0.0.1:1") == "/api/state"
    assert gui_capture.request_endpoint("http://127.0.0.1:1/user-secret",
                                        "http://127.0.0.1:1") == "other-local"
    assert gui_capture.request_endpoint("http://127.0.0.1:1/api/device/secret",
                                        "http://127.0.0.1:1") == "other-local"
    assert gui_capture.request_endpoint("https://other.test/token", "http://127.0.0.1:1") is None


def test_source_counts_include_declared_text_occurrences():
    result = source_statistics(["/* #abc */ a {color:#abc; font-size:12px; margin:0;}",
                                "b {color:rgb(1,2,3); font-size:12px!important;}"], ["let a=1"])
    assert result["style_blocks"] == 2
    assert result["inline_script_blocks"] == 1
    assert result["color_text_occurrences"] == {"#abc": 2, "rgb(1,2,3)": 1}
    assert result["important_occurrences"] == 1
    assert result["property_value_occurrences"]["font-size"] == {
        "12px": 1, "12px!important": 1}


def test_fixture_malformed_port_always_cleans_process(monkeypatch):
    import io

    class Process:
        stdout = io.StringIO("not-a-port\n")

    class Selector:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def register(self, *_args):
            pass

        def select(self, **_kwargs):
            return [object()]

    cleaned = []
    process = Process()
    monkeypatch.setattr(gui_capture.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(gui_capture.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(gui_capture, "stop_fixture", lambda item: cleaned.append(item))
    with pytest.raises(RuntimeError, match="invalid port"):
        with gui_capture.capture_session():
            pytest.fail("Malformed startup must not create a session")
    assert cleaned == [process]


def test_artifact_budget_reserves_failed_manifest(tmp_path):
    store = gui_capture.ArtifactStore(tmp_path, max_bytes=1024)
    store.write_bytes("capture.png", b"x" * 900)
    with pytest.raises(gui_capture.CaptureLimitError):
        store.write_bytes("second.png", b"x" * 50)
    store.write_json("manifest.json", {"complete": False, "status": "failed"})
    assert json.loads((tmp_path / "manifest.json").read_text())["complete"] is False
    store.check_limits()


def test_missing_or_changed_artifact_fails_completeness(tmp_path):
    store = gui_capture.ArtifactStore(tmp_path)
    store.write_json("report.json", {"state": "devices"})
    (tmp_path / "report.json").unlink()
    with pytest.raises(gui_capture.CaptureLimitError, match="missing or changed"):
        store.check_limits()
    (tmp_path / "report.json").write_text("different")
    with pytest.raises(gui_capture.CaptureLimitError, match="missing or changed"):
        store.check_limits()


@pytest.mark.parametrize("commit,dirty", [
    (None, "0"), ("a" * 40, None), ("", "0"), ("not-a-commit", "0"),
    ("a" * 40, ""), ("a" * 40, "false"), ("a" * 40, "unknown"),
])
def test_container_identity_never_guesses_missing_or_invalid_metadata(monkeypatch, commit, dirty):
    monkeypatch.delenv("JITTERSIM_GUI_SOURCE_COMMIT", raising=False)
    monkeypatch.delenv("JITTERSIM_GUI_SOURCE_DIRTY", raising=False)
    if commit is not None:
        monkeypatch.setenv("JITTERSIM_GUI_SOURCE_COMMIT", commit)
    if dirty is not None:
        monkeypatch.setenv("JITTERSIM_GUI_SOURCE_DIRTY", dirty)
    with pytest.raises(RuntimeError, match="explicit dirty flag"):
        gui_capture.source_identity()


@pytest.mark.parametrize("dirty", ["0", "1"])
def test_container_identity_is_labelled_supplied_without_git(monkeypatch, dirty):
    monkeypatch.setenv("JITTERSIM_GUI_SOURCE_COMMIT", "a" * 40)
    monkeypatch.setenv("JITTERSIM_GUI_SOURCE_DIRTY", dirty)

    def no_git(*_args, **_kwargs):
        pytest.fail("Explicit container identity must not require a Git checkout")

    monkeypatch.setattr(gui_capture.subprocess, "run", no_git)
    assert gui_capture.source_identity() == {
        "commit": "a" * 40, "source_dirty": dirty == "1", "source_identity_method": "supplied"}

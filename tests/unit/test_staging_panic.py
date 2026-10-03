"""Slow signed-release staging must leave Panic and health available."""

import threading
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperClient, HelperClientError, HelperService, UnixHelperServer
from jittersim.networking.topology import Topology


@pytest.fixture
def helper_folder():
    # macOS limits Unix socket paths to 104 bytes.
    with tempfile.TemporaryDirectory(prefix="stage-", dir="/tmp") as folder:
        yield Path(folder)


@pytest.mark.parametrize("upload_id", [None, "a" * 32])
def test_panic_releases_network_while_root_staging_is_held(helper_folder, upload_id):
    entered, release = threading.Event(), threading.Event()
    executor = FakeExecutor()
    access = Mock()
    service = HelperService(NetworkApplier(
        executor=executor, topology=Topology(uplink_interface="eth0", test_interface="wlan0")),
        access_manager=access)
    server = UnixHelperServer(helper_folder / "helper.sock", service)
    serving = threading.Thread(target=server.serve_forever)
    serving.start()
    client = HelperClient(socket_path=server.socket_path, timeout_seconds=6)
    staging = {}

    def held_stage(*args, **kwargs):
        entered.set()
        assert release.wait(10), "test did not release root staging"
        return {"version": "1.2.3"}

    def stage():
        try:
            staging["result"] = client.stage_uploaded_release(upload_id=upload_id)
        except HelperClientError as error:
            staging["error"] = error.reason

    with patch("jittersim.networking.helper.stage_upload", held_stage), \
            patch("jittersim.networking.helper.update_status", return_value={"phase": "ready"}):
        worker = threading.Thread(target=stage)
        worker.start()
        try:
            assert entered.wait(3)
            assert client.ping()["ok"]
            answer = client.normal_access()
            assert answer["experiments_stopped"] and answer["access_disabled"]
            assert executor.commands
            access.disable.assert_called_once()
            try:
                client.stage_uploaded_release(upload_id=upload_id)
            except HelperClientError as error:
                assert error.reason == "helper-busy"
            else:
                raise AssertionError("a second root staging operation was accepted")
        finally:
            release.set()
            worker.join(5)
            server.shutdown()
            server.server_close()
            serving.join(5)
    assert "error" not in staging
    assert staging["result"]["ok"]


def test_identified_client_refuses_an_older_helper_even_after_a_stale_capability_check(helper_folder):
    class OlderHelper:
        writes = []

        def handle_payload(self, payload):
            if payload["action"] == "capabilities":
                # A helper replacement can happen between these requests.
                return {"ok": True, "identified_release_uploads": True}
            if payload["action"] == "update-stage-upload":
                self.writes.append(payload)
                return {"ok": True}
            return {"ok": False, "error": "unknown action"}

    service = OlderHelper()
    server = UnixHelperServer(helper_folder / "helper.sock", service)
    serving = threading.Thread(target=server.serve_forever)
    serving.start()
    try:
        client = HelperClient(socket_path=server.socket_path)
        with pytest.raises(HelperClientError, match="update.*helper|helper.*update"):
            client.stage_uploaded_release(upload_id="a" * 32)
        assert service.writes == []
    finally:
        server.shutdown()
        server.server_close()
        serving.join(5)


@pytest.mark.parametrize("identifier", [None, "", "../escape", True])
def test_identified_action_without_a_valid_identifier_never_uses_legacy_staging(identifier):
    service = HelperService(NetworkApplier(
        executor=FakeExecutor(), topology=Topology(uplink_interface="eth0", test_interface="wlan0")))
    with patch("jittersim.networking.helper.stage_upload") as stage:
        result = service.handle_payload({"action": "update-stage-identified-upload", "upload_id": identifier})
    assert result["ok"] is False
    assert "identifier" in result["error"]
    stage.assert_not_called()

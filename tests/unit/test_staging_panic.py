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


def test_panic_releases_network_while_root_staging_is_held(helper_folder):
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

    def held_stage():
        entered.set()
        assert release.wait(10), "test did not release root staging"
        return {"version": "1.2.3"}

    def stage():
        try:
            staging["result"] = client.stage_uploaded_release()
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
                client.stage_uploaded_release()
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

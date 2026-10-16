"""Status reads the dashboard polls never invalidate a client scan in flight."""

from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim.networking.helper import READ_ONLY_ACTIONS, HelperClient


@pytest.mark.parametrize("payload", [
    {"action": "update-online-status", "job": None},
    {"action": "ad-blocking"},
])
def test_status_reads_are_read_only_and_keep_the_scan_generation(payload):
    client = HelperClient(socket_path=Path("/nonexistent/helper.sock"), timeout_seconds=5)
    assert payload["action"] in READ_ONLY_ACTIONS
    before = client._snapshots.generation
    with patch.object(HelperClient, "_send", return_value={"ok": True}) as send:
        client._request(payload)
    send.assert_called_once()
    assert client._snapshots.generation == before


def test_the_configure_write_still_invalidates_the_scan():
    client = HelperClient(socket_path=Path("/nonexistent/helper.sock"), timeout_seconds=5)
    assert "ad-blocking-configure" not in READ_ONLY_ACTIONS
    before = client._snapshots.generation
    with patch.object(HelperClient, "_send", return_value={"ok": True}):
        client._request({"action": "ad-blocking-configure"})
    assert client._snapshots.generation > before

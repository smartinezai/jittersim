"""Exercise browser stream deadlines and cancellation without waiting for a network."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_dashboard_stream_deadlines_and_cancellation():
    script = Path(__file__).with_name("dashboard_speed_stream_client.cjs")
    result = subprocess.run(
        [shutil.which("node"), "--test", str(script)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr

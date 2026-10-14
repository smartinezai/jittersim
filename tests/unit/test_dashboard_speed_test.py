"""Check request timing and end messages for the device speed-test dashboard."""

import inspect
import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML, run_server
from jittersim.api.speed_tests import READ_ATTEMPTS, READ_RETRY_SECONDS
from jittersim.networking.helper import HelperClient

SCRIPTS = re.findall(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL)
DASHBOARD_SCRIPT = SCRIPTS[0]
NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")


def run_node(source):
    result = subprocess.run(["node", "-e", source], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@NEEDS_NODE
def test_start_timeout_covers_both_sequential_helper_reads():
    start = DASHBOARD_SCRIPT.index("const REQUEST_TIMEOUT_MS")
    end = DASHBOARD_SCRIPT.index("// The requests the Pi has not answered", start)
    timeout_source = DASHBOARD_SCRIPT[start:end]
    timeouts = run_node(
        timeout_source
        + "\nconsole.log(JSON.stringify(["
        + "requestTimeoutMs('/api/speed-tests/start', 'POST'),"
        + "requestTimeoutMs('/api/speed-tests/start', 'GET'),"
        + "requestTimeoutMs('/api/impact', 'GET'),"
        + "requestTimeoutMs('/api/apply', 'POST')]));"
    )

    helper_timeout = inspect.signature(HelperClient).parameters["timeout_seconds"].default
    bundled_timeout = inspect.signature(run_server).parameters["helper_timeout_seconds"].default
    helper_budget_ms = 2 * (
        READ_ATTEMPTS * helper_timeout + (READ_ATTEMPTS - 1) * READ_RETRY_SECONDS
    ) * 1000

    assert helper_timeout == bundled_timeout == 8.0
    assert timeouts == [75000, 15000, 15000, 45000]
    assert timeouts[0] - helper_budget_ms >= 20000


@NEEDS_NODE
def test_end_message_marks_saved_changed_and_fault_results_incomplete():
    start = DASHBOARD_SCRIPT.index("function speedTestEndMessage(")
    end = DASHBOARD_SCRIPT.index("async function speedTestFinished(", start)
    message_source = DASHBOARD_SCRIPT[start:end]
    messages = run_node(
        message_source
        + "\nconsole.log(JSON.stringify(["
        + "speedTestEndMessage({result: {status: 'complete'}}),"
        + "speedTestEndMessage({result: {status: 'finished-early'}}),"
        + "speedTestEndMessage({result: {status: 'changed'}, ended_reason: 'Queues changed.'}),"
        + "speedTestEndMessage({result: {status: 'fault', summary: {ended_reason: 'Read failed.'}}}),"
        + "speedTestEndMessage({result: null, ended_reason: 'Cancelled. Nothing was saved.'})"
        + "]));"
    )

    assert messages == [
        "Speed test saved.",
        "Speed test saved.",
        "Speed test saved, but may be incomplete. Queues changed.",
        "Speed test saved, but may be incomplete. Read failed.",
        "Cancelled. Nothing was saved.",
    ]

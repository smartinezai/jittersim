"""Gateway reads never wait unbounded on the network lock (gap audit 2026-10-09, finding 6).

They run while the request handler holds the operation lock. A slow reset during a
router outage holds the network lock, and an unbounded wait would keep the operation
lock from Apply and Reset for as long.
"""

import tempfile
import threading
import time
from pathlib import Path

import pytest

from jittersim.networking.helper import HelperClientError
from tests.integration.test_capacity_stalled_clients import served_helper


@pytest.mark.parametrize("call", ["gateway_status", "set_gateway_persistence"])
def test_gateway_requests_answer_busy_instead_of_waiting_for_a_slow_reset(call):
    with tempfile.TemporaryDirectory(prefix="js-gwlock-", dir="/tmp") as temporary:
        directory = Path(temporary).resolve()
        with served_helper(directory) as (service, client, server):
            assert service._network_lock.acquire(blocking=False)
            outcome = {}

            def request():
                started = time.monotonic()
                try:
                    if call == "gateway_status":
                        outcome["answer"] = client.gateway_status()
                    else:
                        outcome["answer"] = client.set_gateway_persistence(enabled=True)
                except HelperClientError as error:
                    outcome["error"] = error
                outcome["seconds"] = time.monotonic() - started

            worker = threading.Thread(target=request, daemon=True)
            worker.start()
            worker.join(5)
            stuck = worker.is_alive()
            service._network_lock.release()
            worker.join(10)
            assert not stuck, "the gateway request waited unbounded on the network lock"
            assert outcome["error"].reason == "helper-busy"
            assert outcome["seconds"] < 4.0
            # The operation lock was handed back with the busy answer.
            assert not server.operation_lock.locked()

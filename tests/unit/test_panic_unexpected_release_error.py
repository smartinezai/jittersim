"""An unexpected error in the household release still gets a Panic answer (review O4).

Panic caught only OSError, ValueError and CommandExecutionError from
AccessManager.disable(). Anything else escaped before the result was saved, so the
helper recorded no incomplete recovery and the client got no answer.
"""

import json

from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperService
from jittersim.networking.topology import Topology


class BrokenAccessManager:
    release_incomplete = False
    error = None

    def disable(self, **_):
        raise RuntimeError("unexpected release bug")


def test_an_unexpected_release_error_records_incomplete_recovery_and_answers(tmp_path):
    gateway = tmp_path / "gateway.json"
    service = HelperService(
        NetworkApplier(executor=FakeExecutor(), topology=Topology("eth0", "wlan0")),
        access_manager=BrokenAccessManager(), gateway_state_path=gateway,
    )
    answer = service.handle_payload({"action": "normal-access"})
    assert answer["ok"] is False
    assert answer["experiments_stopped"] is True
    assert answer["access_disabled"] is False
    assert service._panic_recovery_failed is True
    assert json.loads(gateway.read_text())["panic_recovery_failed"] is True
    # The network lock is free again for the retry.
    assert service._network_lock.acquire(blocking=False)
    service._network_lock.release()

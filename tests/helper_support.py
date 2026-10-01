"""Helpers that were installed before protected DNS capture existed."""

import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import (
    HelperClient,
    HelperService,
    UnixHelperServer,
    _parse_dns_query_output,
)
from jittersim.networking.topology import Topology


class OldHelperService(HelperService):
    """Answer like the helper before the exclusion list: no capability key and no filter."""

    def _handle_payload(self, payload):
        action = payload.get("action") if isinstance(payload, dict) else None
        if action == "capabilities":
            return {"ok": True, "general_profile": True, "service_lists": True}
        if action == "observe-dns":
            output = self._applier.capture_dns_queries()
            return {
                "ok": True,
                "action": "observe-dns",
                "observations": _parse_dns_query_output(output, observed_at=int(time.time())),
            }
        return super()._handle_payload(payload)


class OlderHelperService(OldHelperService):
    """Answer like a helper that did not know the capabilities action at all."""

    def _handle_payload(self, payload):
        if isinstance(payload, dict) and payload.get("action") == "capabilities":
            return {"ok": False, "error": "unknown action"}
        return super()._handle_payload(payload)


@contextmanager
def serving(service_class, executor=None):
    """Serve one helper over a socket and give back a client and the recording executor."""

    executor = executor or FakeExecutor()
    service = service_class(
        NetworkApplier(
            executor=executor,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )
    )
    with tempfile.TemporaryDirectory() as directory:
        socket_path = Path(directory) / "helper.sock"
        server = UnixHelperServer(socket_path, service)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield HelperClient(socket_path=socket_path), executor
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def captures(executor):
    """Return the packet captures an executor was asked to run."""

    return [command for command in executor.commands if command.executable == "tcpdump"]

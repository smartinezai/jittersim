"""Serve a disposable dashboard and stateful Wi-Fi helper."""

import tempfile
import threading
from pathlib import Path

from jittersim.api.server import ControlServer
from jittersim.config.storage import ConfigRepository
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.commands import CommandResult
from jittersim.networking.helper import HelperClient, HelperService, UnixHelperServer
from jittersim.networking.topology import Topology
from tests.wifi_support import WifiExecutor


class DashboardWifiExecutor(WifiExecutor):
    def run(self, command):
        if command.executable == "nft":
            self.commands.append(command)
            return CommandResult(0, stdout='{"nftables":[]}')
        return super().run(command)


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        service = HelperService(NetworkApplier(executor=DashboardWifiExecutor(),
                                              topology=Topology("eth0", "wlan0")))
        with UnixHelperServer(root / "wifi.sock", service) as helper:
            worker = threading.Thread(target=helper.serve_forever, daemon=True)
            worker.start()
            try:
                with ConfigRepository(root / "config.db") as repository:
                    with ControlServer(
                        ("127.0.0.1", 0), repository, admin_token="browser-test",
                        helper_client=HelperClient(socket_path=root / "wifi.sock"),
                    ) as server:
                        print(server.server_address[1], flush=True)
                        server.serve_forever()
            finally:
                helper.shutdown()
                worker.join(timeout=3)


if __name__ == "__main__":
    main()

"""Disposable dashboard with a simulated helper for browser checks."""

import tempfile
from pathlib import Path

from jittersim.api.server import ControlServer
from jittersim.config.models import Device, Impairment, Profile, Scenario, ScenarioStep
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from tests.unit.test_api import FakeHelper


class DemoHelper(FakeHelper):
    def release_status(self):
        self.release_status_calls += 1
        return {
            "phase": "idle",
            "installed": "test-release",
            "available": "candidate-release",
            "rollback_available": False,
            "message": "A verified release is staged and ready.",
        }

    def connected_devices(self):
        devices = super().connected_devices()
        devices[0]["hostname"] = "Test phone"
        return devices


def main():
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "browser.db"
        with ConfigRepository(database) as repository:
            repository.save_profile(Profile("normal", "Normal connection"))
            repository.save_profile(
                Profile("slow", "Slow connection", download=Impairment(delay_ms=300))
            )
            repository.save_device(
                Device("alice", "Test phone", "192.168.50.178", "normal", "dc:1e:d5:3e:97:4c")
            )
            repository.save_scenario(
                Scenario(
                    "commute", "Commute", (ScenarioStep("slow", 10), ScenarioStep("normal", 10))
                )
            )
            helper = DemoHelper()
            scheduler = RuntimeScheduler(database_path=database, helper_client=helper)
            with ControlServer(
                ("127.0.0.1", 0),
                repository,
                admin_token="browser-test",
                helper_client=helper,
                scheduler=scheduler,
            ) as server:
                print(server.server_port, flush=True)
                try:
                    server.serve_forever()
                finally:
                    scheduler.stop()


if __name__ == "__main__":
    main()

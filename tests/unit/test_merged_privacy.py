"""A protected device never reaches main's activity log or applied states.

The Pi line keeps protected devices out of every plan (`eligible_clients`),
so the scheduler has no target state for them and main's activity log, which
is built from target state changes, never names them.
"""

import tempfile
import unittest
from pathlib import Path

from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile, Timer
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler

ALICE = "ba:bb:00:00:00:01"
BOB = "ba:bb:00:00:00:02"


class ProtectedDevicePrivacyTests(unittest.TestCase):
    def test_a_protected_device_never_reaches_the_activity_log(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "settings.db"
        with ConfigRepository(path) as repository:
            repository.save_profile(Profile(id="slow", name="Slow", upload=Impairment(delay_ms=50)))
            repository.save_profile(Profile(id="fast", name="Fast", upload=Impairment(delay_ms=5)))
            repository.save_device(Device("alice", "Alice", "192.168.50.10", "slow", ALICE))
            repository.save_device(Device("bob", "Bob", "192.168.50.11", "slow", BOB))
            repository.save_unaffected_device(UnaffectedDevice(ALICE, "Alice", 1))
            scheduler = RuntimeScheduler(database_path=path, helper_client=None)
            snapshot = {
                "association_status_available": True,
                "devices": [
                    {"mac_address": ALICE, "ipv4_address": "192.168.50.10", "association_verified": True},
                    {"mac_address": BOB, "ipv4_address": "192.168.50.11", "association_verified": True},
                ],
            }
            prepared = scheduler.prepare_plan(
                repository=repository, now=1000.0, connected_snapshot=snapshot
            )
            self.assertIn("bob", prepared.states)
            self.assertNotIn("alice", prepared.states)
            self.assertEqual([queue.device_id for queue in prepared.plan.queues].count("alice"), 0)
            self.assertIn(ALICE, prepared.plan.protected_mac_addresses)

            # The first plan is the starting point. A timer for each device then
            # changes what the network applies; only Bob's change is recorded.
            scheduler._record_activity(repository=repository, states=prepared.states, now=1000.0)
            for device_id in ("alice", "bob"):
                repository.save_timer(
                    Timer(f"{device_id}-timer", device_id, "fast", started_at=1000, expires_at=2000)
                )
            changed = scheduler.prepare_plan(
                repository=repository, now=1001.0, connected_snapshot=snapshot
            )
            self.assertEqual(changed.states["bob"].source.kind, "timer")
            self.assertNotIn("alice", changed.states)
            scheduler._record_activity(repository=repository, states=changed.states, now=1001.0)
            entries = repository.list_activity()
            self.assertTrue(entries)
            for entry in entries:
                self.assertNotIn("alice", entry["message"].lower(), entry)
                self.assertNotIn("alice", entry.get("device_ids", ()), entry)
            self.assertTrue(any("bob" in entry.get("device_ids", ()) for entry in entries), entries)


if __name__ == "__main__":
    unittest.main()

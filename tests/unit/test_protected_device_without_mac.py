"""A saved device without a MAC address never gets a profile next to protected clients.

The owner saw `Aeromac` in Saved devices with a profile and no MAC address while the
Unaffected list held the same laptop by MAC. Identity is by MAC so a saved device that
has none cannot be checked against the list. The plan must leave it out.
"""

import tempfile
import unittest
from pathlib import Path

from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository
from jittersim.engine.conditions import device_conditions
from jittersim.engine.eligibility import device_protection_states
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.service import compile_stored_plan
from jittersim.networking.renderer import render_command_plan
from jittersim.networking.topology import Topology

AEROMAC = "d6:ac:49:a3:ee:6a"
OTHER = "ba:bb:00:00:00:02"
ADDRESS = "192.168.50.178"


def _snapshot(*clients):
    return {
        "association_status_available": True,
        "devices": [
            {"mac_address": mac, "ipv4_address": address, "association_verified": True}
            for mac, address in clients
        ],
    }


def _rendered(plan) -> str:
    commands = render_command_plan(
        plan=plan, topology=Topology(uplink_interface="eth0", test_interface="wlan0")
    )
    return "\n".join(
        " ".join(command.arguments) + "\n" + (command.stdin or "") for command in commands
    )


class SavedDeviceWithoutMacTests(unittest.TestCase):
    def _prepare(self, snapshot, *, protect: bool = True):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "settings.db"
        repository = ConfigRepository(path)
        self.addCleanup(repository.close)
        repository.save_profile(
            Profile(id="bad", name="Bad test", upload=Impairment(delay_ms=1000, jitter_ms=200, loss_percent=20))
        )
        repository.save_device(Device("aeromac", "Aeromac", ADDRESS, "bad"))
        if protect:
            repository.save_unaffected_device(UnaffectedDevice(AEROMAC, "Aeromac", 1))
        scheduler = RuntimeScheduler(database_path=path, helper_client=None)
        prepared = scheduler.prepare_plan(
            repository=repository, now=1000.0, connected_snapshot=snapshot
        )
        return prepared, repository

    def test_no_impairment_is_made_for_the_address_of_a_protected_client(self) -> None:
        prepared, _ = self._prepare(_snapshot((AEROMAC, ADDRESS)))
        self.assertEqual(prepared.plan.mark_rules, ())
        self.assertEqual(prepared.plan.queues, ())
        self.assertEqual(prepared.plan.unverified_device_ids, ("aeromac",))
        self.assertNotIn("aeromac", prepared.states)
        rendered = _rendered(prepared.plan)
        self.assertNotIn(ADDRESS, rendered)
        self.assertNotIn("netem", rendered)
        self.assertIn(AEROMAC, rendered)

    def test_no_impairment_when_the_live_snapshot_is_unavailable(self) -> None:
        prepared, _ = self._prepare({"devices": [], "association_status_available": False})
        self.assertEqual(prepared.plan.mark_rules, ())
        self.assertNotIn("netem", _rendered(prepared.plan))

    def test_no_impairment_when_the_address_belongs_to_another_client(self) -> None:
        prepared, _ = self._prepare(_snapshot((OTHER, ADDRESS), (AEROMAC, "192.168.50.20")))
        self.assertEqual(prepared.plan.mark_rules, ())
        self.assertNotIn(ADDRESS, _rendered(prepared.plan))

    def test_without_protected_clients_the_saved_device_keeps_its_profile(self) -> None:
        prepared, _ = self._prepare(_snapshot((OTHER, ADDRESS)), protect=False)
        self.assertEqual([rule.device_id for rule in prepared.plan.mark_rules], ["aeromac"])


class StoredPlanAndStateTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = ConfigRepository(Path(directory.name) / "settings.db")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(
            Profile(id="bad", name="Bad test", upload=Impairment(delay_ms=1000))
        )
        self.repository.save_device(Device("aeromac", "Aeromac", ADDRESS, "bad"))
        self.repository.save_unaffected_device(UnaffectedDevice(AEROMAC, "Aeromac", 1))

    def test_the_saved_settings_plan_without_a_snapshot_also_leaves_it_out(self) -> None:
        plan = compile_stored_plan(repository=self.repository)
        self.assertEqual(plan.mark_rules, ())
        self.assertEqual(device_conditions(plan), {})
        self.assertEqual(plan.unverified_device_ids, ("aeromac",))

    def test_state_names_the_protected_client_that_holds_the_address(self) -> None:
        state = device_protection_states(
            self.repository, _snapshot((AEROMAC, ADDRESS))
        )["aeromac"]
        self.assertEqual(state["effective_state"], "unverified")
        self.assertEqual(
            state["protected_match"], {"mac_address": AEROMAC, "name": "Aeromac"}
        )
        self.assertIn(
            "This saved device has no MAC address so JitterSim cannot tell it is the protected Aeromac",
            state["effective_note"],
        )

    def test_state_without_a_match_still_pauses_and_explains(self) -> None:
        state = device_protection_states(self.repository, _snapshot((OTHER, ADDRESS)))["aeromac"]
        self.assertEqual(state["effective_state"], "unverified")
        self.assertIsNone(state["protected_match"])
        self.assertIn("no MAC address", state["effective_note"])

    def test_a_saved_device_with_the_protected_mac_is_protected(self) -> None:
        self.repository.save_device(Device("aeromac", "Aeromac", ADDRESS, "bad", AEROMAC))
        state = device_protection_states(self.repository, _snapshot((AEROMAC, ADDRESS)))
        self.assertEqual(state["aeromac"]["effective_state"], "protected")

    def test_a_verified_device_with_a_mac_is_active(self) -> None:
        self.repository.save_device(Device("aeromac", "Aeromac", ADDRESS, "bad", OTHER))
        state = device_protection_states(self.repository, _snapshot((OTHER, ADDRESS), (AEROMAC, "192.168.50.20")))
        self.assertEqual(state["aeromac"]["effective_state"], "active")
        self.assertIsNone(state["aeromac"]["effective_note"])

    def test_without_protected_clients_every_device_is_active(self) -> None:
        self.repository.delete_unaffected_device(AEROMAC)
        state = device_protection_states(self.repository, None)
        self.assertEqual(state["aeromac"]["effective_state"], "active")


if __name__ == "__main__":
    unittest.main()

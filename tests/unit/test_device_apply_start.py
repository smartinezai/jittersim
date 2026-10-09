"""Applying a profile to one connected device starts it for that device only."""

import json
import unittest

from jittersim.api.server import ControlServer
from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository
from tests.unit.test_api import FakeHelper

AIR = {"ipv4_address": "192.168.50.178", "mac_address": "dc:1e:d5:3e:97:4c"}


class DeviceApplyStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.repository.save_profile(Profile(id="low", name="daily climb manual",
                                             upload=Impairment(delay_ms=50)))
        self.repository.save_profile(Profile(id="slow-test", name="Slow test",
                                             upload=Impairment(delay_ms=3_000)))
        self.repository.save_device(Device(id="tv", name="TV", ipv4_address="192.168.50.20",
                                           profile_id="low"))
        self.helper = FakeHelper()
        self.server = ControlServer(("127.0.0.1", 0), self.repository, helper_client=self.helper)

    def tearDown(self) -> None:
        self.server.server_close()
        self.repository.close()

    def write(self, path, payload):
        status, _, body = self.server.write_response_for(path, payload)
        return status, json.loads(body)

    def apply_to_air(self, profile_id="slow-test"):
        return self.write("/api/connected-devices/apply-profile", {**AIR, "profile_id": profile_id})

    def test_reset_with_everyone_saved_then_device_apply_starts_only_that_device(self) -> None:
        self.repository.save_general_profile("low")
        self.assertEqual(self.write("/api/reset", {})[0], 200)
        self.helper.applied_plan = None

        status, result = self.apply_to_air()

        self.assertEqual(status, 200, result)
        self.assertTrue(result["live_applied"])
        plan = self.helper.applied_plan
        self.assertIsNotNone(plan)
        self.assertIsNone(plan.general_profile)
        self.assertIsNone(plan.everyone_else)
        self.assertEqual([rule.device_id for rule in plan.mark_rules], ["client-dc1ed53e974c"])
        upload = [queue for queue in plan.queues if queue.device_id == "client-dc1ed53e974c"]
        self.assertEqual({queue.impairment.delay_ms for queue in upload}, {3_000, 0})
        # Everyone stays saved and paused. The Reset still describes the rest.
        self.assertEqual(self.repository.get_general_profile().id, "low")
        state = self.repository.get_runtime_state()
        self.assertEqual((state.last_action, state.observed_state), ("reset", "applied"))

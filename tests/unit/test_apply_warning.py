import json
import threading
import unittest
from ipaddress import IPv4Address
from unittest.mock import patch

from jittersim.api.server import ControlServer, _apply_warning
from jittersim.config.models import Device, Profile
from jittersim.config.storage import ConfigRepository
from jittersim.engine import website
from jittersim.engine.runtime import RuntimeScheduler


class ApplyWarningTextTests(unittest.TestCase):
    def test_nothing_left_out_means_no_warning(self) -> None:
        self.assertIsNone(_apply_warning((), ()))

    def test_unverified_devices_keep_their_existing_sentence(self) -> None:
        self.assertEqual(
            _apply_warning(("tv",), ()),
            "Profiles were skipped for devices whose current Wi-Fi identity could not be verified: tv",
        )

    def test_slow_website_names_are_listed_in_plain_words(self) -> None:
        warning = _apply_warning((), ("a.example", "b.example"))

        self.assertEqual(
            warning,
            "These website names could not be looked up in time and are left out of the rules "
            "until their lookup works: a.example, b.example",
        )

    def test_both_kinds_of_warning_can_be_shown_together(self) -> None:
        warning = _apply_warning(("tv",), ("a.example",))

        self.assertIn("could not be verified: tv. These website names", warning)
        self.assertTrue(warning.endswith("a.example"))


class Helper:
    def __init__(self) -> None:
        self.applied_plan = None

    def gateway_status(self):
        return {"outage_stop_latched": False, "state_error": False}

    def ping(self):
        return {"ok": True}

    def apply(self, *, plan, explicit_apply=False):
        self.applied_plan = plan
        return {"ok": True, "action": "apply", "commands_run": 3}

    def connected_device_snapshot(self):
        return {"devices": [], "association_status_available": False}


class ApplyLookupWarningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(
            Profile(
                id="strict",
                name="Strict",
                website_mode="allow_only",
                website_domains=("allowed.example", "slow.example"),
            )
        )
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "strict"))
        self.server = ControlServer(("127.0.0.1", 0), self.repository)
        self.addCleanup(self.server.server_close)
        self.helper = Helper()
        self.server.helper_client = self.helper
        self.server.scheduler = RuntimeScheduler(
            database_path=":memory:", helper_client=self.helper
        )
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        with website._resolution_cache_lock:
            website._resolution_cache.clear()
            website._pending.clear()
            website._recent_timeouts.clear()
            website._reported.clear()

    def lookup(self, *, domain):
        if domain == "slow.example":
            self.release.wait(timeout=10)
        return (IPv4Address("203.0.113.40" if domain == "slow.example" else "203.0.113.20"),)

    def test_a_manual_apply_succeeds_and_names_the_websites_that_were_too_slow(self) -> None:
        with patch.object(website, "RESOLVE_WAIT_SECONDS", 0.3), patch(
            "jittersim.engine.website._cached_addresses", side_effect=self.lookup
        ), patch.object(self.server.scheduler, "start"):
            status, _, body = self.server.write_response_for("/api/apply", {})

        self.assertEqual(status, 200)
        warning = json.loads(body)["warning"]
        self.assertIn("slow.example", warning)
        self.assertIn("could not be looked up in time", warning)
        self.assertNotIn("allowed.example", warning)
        # The names that answered are still in the plan that was applied.
        self.assertEqual(
            self.helper.applied_plan.website_filters[0].allowed_addresses,
            (IPv4Address("203.0.113.20"),),
        )

    def test_a_manual_apply_without_slow_names_has_no_warning(self) -> None:
        self.release.set()
        with patch(
            "jittersim.engine.website._cached_addresses", side_effect=self.lookup
        ), patch.object(self.server.scheduler, "start"):
            status, _, body = self.server.write_response_for("/api/apply", {})

        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(body)["warning"])


if __name__ == "__main__":
    unittest.main()

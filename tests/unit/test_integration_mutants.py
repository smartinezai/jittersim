"""Pins for behaviour that a review mutation showed no test held."""

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jittersim.config.models import Device, MacPolicy, Profile, SiteBlock, WebsitePolicy
from jittersim.config.storage import ConfigRepository
from jittersim.engine.service import compile_stored_plan
from jittersim.networking.helper import HelperService

ROOT = Path(__file__).resolve().parents[2]


class GeneralProfileKeepsAccessRulesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = ConfigRepository(Path(directory.name) / "settings.db")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(Profile("slow", "Slow"))
        self.repository.save_device(
            Device("child", "Child", "192.168.50.10", "slow", "02:00:00:00:50:0a")
        )
        self.block = SiteBlock("video", "Video", ("video.example.com",), device_id="child")
        self.repository.save_access_policies(
            MacPolicy(),
            WebsitePolicy(blocked_domains=("ads.example.com",)),
            blocks=(self.block,),
        )

    def test_site_rules_still_apply_while_the_general_profile_is_on(self):
        self.repository.save_general_profile("slow")
        with patch("jittersim.engine.general.resolve_domains", return_value=()):
            plan = compile_stored_plan(repository=self.repository)

        self.assertIsNotNone(plan.general_profile)
        # Household blocking is separate from profiles and must not stop when one
        # profile covers everyone.
        self.assertEqual(plan.access.blocks, (self.block,))
        self.assertEqual(
            plan.access.blocked_domains("child"), ("ads.example.com", "video.example.com")
        )
        self.assertEqual(plan.access.blocked_domains(), ("ads.example.com",))


class CapabilityFlagTests(unittest.TestCase):
    def test_every_capability_the_helper_reports_is_read_by_the_dashboard_client(self):
        reported = HelperService.__new__(HelperService)
        reported._access_manager = None
        flags = set(reported.handle_payload({"action": "capabilities"})) - {"ok"}
        source = (ROOT / "src/jittersim/networking/helper.py").read_text()
        read = set(re.findall(r'capabilities\.get\("([a-z_]+)"\)', source))
        # A flag nothing reads promises a check that does not exist.
        self.assertEqual(flags, read)


if __name__ == "__main__":
    unittest.main()

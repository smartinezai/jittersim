"""The page says what the global block list really does.

The Pi line matched exact names. After the main merge the same list also feeds
the filtered resolver and an Unbound local zone covers every name below it.
The kernel test test_the_global_block_list_covers_subdomains_and_unblock_brings_both_back
shows the real resolver doing that and resolving again after Unblock.
"""

import json
import tempfile
import unittest
from pathlib import Path

from jittersim.api.server import DASHBOARD_HTML, ControlServer
from jittersim.config.models import AccessOptions, WebsitePolicy
from jittersim.config.storage import ConfigRepository
from jittersim.engine.access import AccessPlan
from jittersim.networking.access import render_dns

from tests.unit.test_api import FakeHelper

ROOT = Path(__file__).resolve().parents[2]
ADD = "/api/observations/add-global-block"
REMOVE = "/api/observations/remove-global-block"
BLOCKED = WebsitePolicy(blocked_domains=("video.example.com",))


def test_the_resolver_refuses_the_listed_zone_only_while_access_rules_are_on():
    config = render_dns(AccessPlan(websites=BLOCKED))
    # A local zone answers for the name and for every name under it.
    assert 'local-zone: "video.example.com." always_nxdomain' in config
    off = render_dns(AccessPlan(options=AccessOptions(enabled=False), websites=BLOCKED))
    assert "video.example.com" not in off


def test_traffic_and_access_texts_say_that_subdomains_are_blocked_too():
    assert (
        "Block globally also blocks every name under a name (example.com also blocks"
        " www.example.com) while household access rules are on." in DASHBOARD_HTML
    )
    assert "Blocked and Unblock show on the exact name that was blocked." in DASHBOARD_HTML
    access = (ROOT / "src/jittersim/api/access.js").read_text()
    assert "global blocked domains include subdomains while household access rules are on" in access
    # A subdomain listed on its own stays blocked after its parent is unblocked.
    assert (
        "Unblock frees the exact name and the names under it unless one is listed on its own."
        in access
    )
    # The household toggle says what it does in one line.
    assert "Household rules filter test Wi-Fi DNS through the Pi" in access
    assert "does not match subdomains" not in (ROOT / "src/jittersim/api/server.py").read_text()


class QuickBlockAppliesTheResolverTests(unittest.TestCase):
    """Block globally and Unblock reach the filtered resolver at once.

    Main's resolver only changes when the access rules are applied. Without that an
    Unblock left the name and its subdomains refused until someone chose Apply access
    rules, so the name did not resolve again.
    """

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = ConfigRepository(Path(directory.name) / "settings.db")
        self.addCleanup(self.repository.close)
        self.server = ControlServer(("127.0.0.1", 0), self.repository, admin_token="t")
        self.addCleanup(self.server.server_close)
        self.helper = FakeHelper()
        self.server.helper_client = self.helper

    def post(self, path, domain):
        status, _, body = self.server.write_response_for(path, {"domain": domain})
        return status, json.loads(body)

    def test_block_and_unblock_apply_the_household_rules_right_away(self):
        status, body = self.post(ADD, "video.example.com")
        self.assertEqual(status, 200, body)
        self.assertEqual(self.helper.access_plan.blocked_domains(), ("video.example.com",))

        status, body = self.post(REMOVE, "video.example.com")
        self.assertEqual(status, 200, body)
        self.assertEqual(self.helper.access_plan.blocked_domains(), ())

    def test_nothing_reaches_the_resolver_while_household_rules_are_off(self):
        settings = self.repository.access_settings()
        self.repository.save_access_policies(
            settings.mac_policy,
            settings.website_policy,
            options=AccessOptions(enabled=False),
        )

        status, body = self.post(ADD, "video.example.com")

        self.assertEqual(status, 200, body)
        self.assertIsNone(self.helper.access_plan)
        self.assertEqual(self.repository.get_website_policy().blocked_domains, ("video.example.com",))

    def test_a_failed_resolver_update_keeps_the_saved_list_and_says_what_to_do(self):
        self.helper.fail_action = "access"

        status, body = self.post(ADD, "video.example.com")

        self.assertEqual(status, 503)
        self.assertIn("Apply access rules", body["error"])
        self.assertEqual(self.repository.get_website_policy().blocked_domains, ("video.example.com",))

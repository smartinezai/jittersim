"""The page says what the global block list really does.

The Pi line matched exact names. After the main merge the same list also feeds
the filtered resolver and an Unbound local zone covers every name below it.
The kernel test test_the_global_block_list_covers_subdomains_and_unblock_brings_both_back
shows the real resolver doing that and resolving again after Unblock.
"""

from pathlib import Path

from jittersim.api.server import DASHBOARD_HTML
from jittersim.config.models import AccessOptions, WebsitePolicy
from jittersim.engine.access import AccessPlan
from jittersim.networking.access import render_dns

ROOT = Path(__file__).resolve().parents[2]
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
    assert "Unblock removes the exact name and its subdomains resolve again." in access
    assert "does not match subdomains" not in (ROOT / "src/jittersim/api/server.py").read_text()

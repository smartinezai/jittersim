"""Producer wiring keeps household replies private and ad DNS logging disabled."""

import re
from dataclasses import replace
from pathlib import Path

from jittersim.config.models import AccessOptions, WebsitePolicy
from jittersim.engine.access import AccessPlan
from jittersim.networking.access import render_dns
from jittersim.networking.adblocking import filtering_config


ROOT = Path(__file__).resolve().parents[2]


def test_household_reply_log_identity_tracks_exact_plan_without_query_or_syslog_output():
    original = AccessPlan(websites=WebsitePolicy(blocked_domains=("blocked.test",)))
    changed = replace(original, options=AccessOptions(enabled=False))
    identities = []
    for plan in (original, changed):
        config = render_dns(plan, "testbox.local")
        assert "  use-syslog: no\n" in config
        assert '  logfile: ""\n' in config
        assert "  verbosity: 0\n" in config
        assert "  log-queries: no\n" in config
        assert "  log-replies: yes\n" in config
        assert "  log-tag-queryreply: yes\n" in config
        assert "  log-local-actions: yes\n" in config
        identity = re.findall(r'  log-identity: "jittersim-dns-([0-9a-f]{64})"', config)
        assert identity == [plan.digest()]
        identities.extend(identity)
    assert identities[0] != identities[1]


def test_ad_dns_never_inherits_household_reply_stream():
    config = filtering_config(("ad.test",), (), AccessPlan()).decode()
    assert '  logfile: "/dev/null"\n' in config
    assert "  use-syslog: no\n" in config
    assert "  log-queries: no\n" in config
    assert "  log-replies: no\n" in config
    assert "  log-local-actions: no\n" in config
    assert '  logfile: ""' not in config


def test_dns_unit_runs_private_producer_with_existing_restrictions():
    unit = (ROOT / "deploy/raspberry-pi/jittersim-dns.service").read_text()
    assert (
        "ExecStart=/opt/jittersim/.venv/bin/python -m jittersim.networking.dns_decisions "
        "--config /var/lib/jittersim-access/unbound.conf "
        "--socket /run/jittersim/dns-observations/events.sock "
        "--pid-file /run/jittersim/dns-producer.json\n"
    ) in unit
    for restriction in (
        "ProtectHome=true", "PrivateTmp=true", "ProtectSystem=strict",
        "RestrictAddressFamilies=AF_INET AF_UNIX AF_NETLINK",
    ):
        assert restriction in unit.splitlines()
    assert "AF_PACKET" not in unit
    assert "StandardError=journal" not in unit

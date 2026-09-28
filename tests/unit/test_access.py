"""Access policy isolation, durable recovery and storage boundaries."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jittersim.config.models import (
    AccessOptions,
    Device,
    DeviceGroup,
    MacPolicy,
    Profile,
    SavedSettings,
    SiteBlock,
    ValidationError,
    WebsitePolicy,
)
from jittersim.config.storage import ConfigRepository, DeviceConflictError, StorageError
from jittersim.config.transfer import settings_to_payload
from jittersim.engine.access import AccessClient, AccessPlan, access_from_payload, build_access_plan
from jittersim.networking.access import (
    AccessManager,
    read_access,
    render_access_firewall,
    render_dns,
)
from jittersim.networking.executor import CommandExecutionError
from jittersim.networking.topology import Topology
from tests.access_support import AccessExecutor


class AccessPolicyTests(unittest.TestCase):
    def test_dns_failure_rules_are_random_and_limited_to_test_wifi(self):
        plan = AccessPlan(options=AccessOptions(dns_failure_percent=35))
        rules = render_access_firewall(plan, Topology("eth0", "wlan0"))

        self.assertEqual(rules.count("numgen random mod 100 < 35"), 2)
        self.assertIn(
            'iifname "wlan0" ip saddr 192.168.50.0/24 udp dport 5353 '
            "numgen random mod 100 < 35 counter reject with icmpx type port-unreachable",
            rules,
        )
        self.assertIn(
            'iifname "wlan0" ip saddr 192.168.50.0/24 tcp dport 5353 '
            "numgen random mod 100 < 35 counter reject with tcp reset",
            rules,
        )

        every_request = render_access_firewall(
            AccessPlan(options=AccessOptions(dns_failure_percent=100)),
            Topology("eth0", "wlan0"),
        )
        self.assertNotIn("numgen random", every_request)
        self.assertEqual(every_request.count("ip saddr 192.168.50.0/24"), 2)
        self.assertIn("udp dport 5353 counter reject", every_request)
        self.assertIn("tcp dport 5353 counter reject", every_request)

        disabled = render_access_firewall(
            AccessPlan(options=AccessOptions(enabled=False, dns_failure_percent=35)),
            Topology("eth0", "wlan0"),
        )
        self.assertNotIn("numgen random", disabled)

    def test_deleting_a_missing_target_does_not_leave_access_edits_locked(self):
        with ConfigRepository(':memory:') as repository:
            for delete in (repository.delete_device, repository.delete_device_group):
                self.assertFalse(delete('already-removed'))
                repository.save_access_policies(MacPolicy(), WebsitePolicy())

    def settings(self):
        return SavedSettings(
            profiles=(Profile("normal", "Normal"),),
            devices=(
                Device("child", "Child", "192.168.50.10", "normal", "02:00:00:00:00:01"),
                Device("adult", "Adult", "192.168.50.20", "normal", "02:00:00:00:00:02"),
            ),
            groups=(DeviceGroup("kids", "Kids", ("child",)),),
            site_blocks=(
                SiteBlock("video", "Video", ("VIDEO.Example.com.",), group_id="kids"),
                SiteBlock("global", "Global", ("blocked.example.com",)),
                SiteBlock("inactive", "Inactive", ("news.example.com",), enabled=False),
            ),
        )

    def test_scopes_suffix_boundaries_allow_only_and_disable(self):
        plan = build_access_plan(self.settings())
        for host in ("video.example.com", "cdn.video.example.com", "blocked.example.com"):
            self.assertFalse(plan.permits_host("192.168.50.10", host))
        for host in ("notvideo.example.com", "news.example.com"):
            self.assertTrue(plan.permits_host("192.168.50.10", host))
        self.assertTrue(plan.permits_host("192.168.50.20", "video.example.com"))
        self.assertFalse(plan.permits_host("192.168.50.222", "blocked.example.com"))
        strict = replace(plan, websites=WebsitePolicy("allow_only", ("example.com",), ()))
        self.assertFalse(strict.permits_host("192.168.50.10", "outside.org"))
        self.assertFalse(strict.permits_host("192.168.50.10", "video.example.com"))
        self.assertTrue(
            replace(strict, options=AccessOptions(False)).permits_host(
                "192.168.50.10", "video.example.com"
            )
        )

    def test_untrusted_helper_payload_rejects_bad_targets_and_injection(self):
        original = json.loads(json.dumps(build_access_plan(self.settings()).payload()))
        changes = (
            lambda p: p.update(version=True),
            lambda p: p["clients"][0].update(address="192.168.0.1"),
            lambda p: p["clients"][0].update(mac="02:00:00:00:00:01; reboot"),
            lambda p: p["clients"][0].update(block_ids=["missing"]),
            lambda p: p["clients"].append(p["clients"][0]),
            lambda p: p["blocks"][0].update(domains=["evil.com\nserver:"]),
            lambda p: p["blocks"][0].update(device_id="missing", group_id=None),
            lambda p: p.update(options={"enabled": 1}),
        )
        for change in changes:
            with self.subTest(change=change):
                payload = json.loads(json.dumps(original))
                change(payload)
                with self.assertRaises(ValueError):
                    access_from_payload(payload)
        with self.assertRaises(ValidationError):
            SiteBlock("ip", "IP", ("1.1.1.1",))
        with self.assertRaises(ValidationError):
            SiteBlock("large", "Large", tuple(f"n{i}.example.com" for i in range(65)))

    def test_equivalent_clients_share_views_and_reject_excessive_expansion(self):
        block = SiteBlock("global", "Global", ("example.com",))
        clients = tuple(AccessClient(f"c{i}", f"192.168.50.{i + 2}", None) for i in range(200))
        plan = access_from_payload(AccessPlan(blocks=(block,), clients=clients).payload())
        self.assertEqual(render_dns(plan).count("\nview:\n"), 1)
        blocks = tuple(
            SiteBlock(
                f"b{i}", f"B{i}", tuple(f"n{j}.b{i}.com" for j in range(64)), group_id=f"g{i}"
            )
            for i in range(8)
        )
        clients = tuple(
            replace(c, block_ids=tuple(f"b{i}" for i in range(8) if n & (1 << i)))
            for n, c in enumerate(clients)
        )
        with self.assertRaisesRegex(ValidationError, "combinations"):
            access_from_payload(AccessPlan(blocks=blocks, clients=clients).payload())

    def test_settings_roundtrip_and_deletion_preserve_referenced_rules(self):
        from jittersim.api.server import _settings_from_payload

        settings = self.settings()
        with ConfigRepository(":memory:") as repository:
            repository.replace_settings(settings)
            encoded = settings_to_payload(repository.saved_settings(), exported_at=1)
            restored = _settings_from_payload(encoded)
            self.assertCountEqual(restored.site_blocks, settings.site_blocks)
            self.assertEqual(restored.access_options, settings.access_options)
            before = repository.get_runtime_state().configuration_version
            for remove in (
                lambda: repository.delete_device("child"),
                lambda: repository.delete_device_group("kids"),
            ):
                with self.assertRaises(StorageError):
                    remove()
                self.assertEqual(repository.get_runtime_state().configuration_version, before)
                self.assertIsNotNone(repository.get_device("child"))
                self.assertEqual(repository.list_device_groups()[0].device_ids, ("child",))
            # Moving a group changes its scope without deleting the rule.
            repository.save_device_group(DeviceGroup("kids", "Kids", ("adult",)))
            plan = build_access_plan(repository.access_settings())
            self.assertTrue(plan.permits_host("192.168.50.10", "video.example.com"))
            self.assertFalse(plan.permits_host("192.168.50.20", "video.example.com"))

    def test_failed_save_rolls_back_all_policy_fields_and_stale_write_is_rejected(self):
        with ConfigRepository(":memory:") as repository:
            version = repository.get_runtime_state().configuration_version
            with self.assertRaises(ValidationError):
                repository.save_access_policies(
                    MacPolicy("denylist", (), ("02:00:00:00:00:01",)),
                    WebsitePolicy(),
                    blocks=[SiteBlock("bad", "Bad", ("example.com",), device_id="missing")],
                    expected_version=version,
                )
            self.assertEqual(repository.get_mac_policy(), MacPolicy())
            self.assertEqual(repository.get_runtime_state().configuration_version, version)
            repository.save_access_policies(MacPolicy(), WebsitePolicy(), expected_version=version)
            with self.assertRaises(DeviceConflictError):
                repository.save_access_policies(
                    MacPolicy(), WebsitePolicy(), expected_version=version
                )


class AccessRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.executor = AccessExecutor(self.directory)
        self.manager = AccessManager(
            executor=self.executor,
            topology=Topology("eth0", "wlan0"),
            directory=self.directory,
            runtime_path=self.directory / "runtime.json",
        )
        self.plan = AccessPlan(blocks=(SiteBlock("global", "Global", ("example.com",)),))

    def test_failure_preserves_previous_rules_and_double_failure_keeps_guard(self):
        self.manager.apply(self.plan)
        self.executor.fail_restarts = 1
        with self.assertRaises(CommandExecutionError):
            self.manager.apply(AccessPlan())
        self.assertEqual(read_access(self.directory / "policy.json"), self.plan)
        self.assertIn("restored", self.manager.status()["error"])
        self.executor.fail_restarts = 2
        with self.assertLogs(level="ERROR"), self.assertRaises(CommandExecutionError):
            self.manager.apply(AccessPlan())
        nft = [c for c in self.executor.commands if c.executable == "nft"]
        self.assertIn('iifname "wlan0" drop', nft[-1].stdin)
        self.assertIn("closed", self.manager.status()["error"])
        self.manager.restore()
        self.assertEqual(self.manager.applied, self.plan)

    def test_corrupt_saved_policy_closes_test_network_before_reading(self):
        (self.directory / "policy.json").write_text("{")
        with self.assertRaises(ValueError):
            self.manager.restore()
        self.assertIn('iifname "wlan0" drop', self.executor.commands[0].stdin)

    def test_unsupported_admission_leaves_working_policy_unchanged(self):
        self.manager.apply(self.plan)
        count = len(self.executor.commands)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.manager.apply(replace(self.plan, options=AccessOptions(reject_wifi_joins=True)))
        self.assertEqual(len(self.executor.commands), count)
        self.assertEqual(read_access(self.directory / "policy.json"), self.plan)

    def test_supported_admission_loads_lists_and_disconnects_blocked_station(self):
        with patch.object(self.manager, "admission_supported", return_value=True):
            plan = replace(
                self.plan,
                options=AccessOptions(reject_wifi_joins=True),
                mac=MacPolicy("denylist", (), ("02:00:00:00:00:01",)),
            )
            self.manager.apply(plan)
            self.assertEqual(
                (self.directory / "deny.macs").read_text().strip(), "02:00:00:00:00:01"
            )
            self.manager.apply(replace(plan, options=AccessOptions(enabled=False)))
            self.assertEqual((self.directory / "deny.macs").read_text().strip(), "")

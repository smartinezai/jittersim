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
    DNS_CLEANUP_ATTEMPTS,
    DNS_CLEANUP_BUDGET_SECONDS,
    AccessManager,
    read_access,
    render_access_firewall,
    render_dns,
)
from jittersim.networking.commands import CommandResult
from jittersim.networking.executor import CommandExecutionError
from jittersim.networking.topology import Topology
from tests.access_support import AccessExecutor


class AccessPolicyTests(unittest.TestCase):
    def test_dns_failure_rules_are_random_and_limited_to_test_wifi(self):
        plan = AccessPlan(options=AccessOptions(dns_failure_percent=35))
        rules = render_access_firewall(plan, Topology("eth0", "wlan0"))

        self.assertEqual(rules.count("numgen random mod 100 < 35"), 4)
        self.assertIn(
            'iifname "wlan0" ip saddr 192.168.50.0/24 udp dport 5335 '
            "numgen random mod 100 < 35 counter reject with icmpx type port-unreachable",
            rules,
        )
        self.assertIn(
            'iifname "wlan0" ip saddr 192.168.50.0/24 tcp dport 5335 ct state new '
            "numgen random mod 100 < 35 counter reject with tcp reset",
            rules,
        )

        every_request = render_access_firewall(
            AccessPlan(options=AccessOptions(dns_failure_percent=100)),
            Topology("eth0", "wlan0"),
        )
        self.assertNotIn("numgen random", every_request)
        self.assertEqual(every_request.count("ip saddr 192.168.50.0/24"), 4)
        self.assertIn("udp dport 5354 counter reject", every_request)
        self.assertIn("tcp dport 5354 tcp flags & (syn | ack) == syn counter reject", every_request)
        self.assertIn("udp dport 5335 counter reject", every_request)
        self.assertIn("tcp dport 5335 ct state new counter reject", every_request)

        disabled = render_access_firewall(
            AccessPlan(options=AccessOptions(enabled=False, dns_failure_percent=35)),
            Topology("eth0", "wlan0"),
        )
        self.assertNotIn("numgen random", disabled)

    def test_dashboard_sign_in_exception_comes_before_every_drop_that_could_hit_it(self):
        blocked = "02:00:00:00:00:01"
        plan = AccessPlan(
            mac=MacPolicy("denylist", (), (blocked,)),
            blocks=(SiteBlock("video", "Video", ("video.example.com",), device_id="child"),),
            clients=(AccessClient("child", "192.168.50.10", blocked, ("video",)),),
        )
        lines = render_access_firewall(plan, Topology("eth0", "wlan0")).splitlines()

        def first(text, *, after=0):
            return next(i for i, line in enumerate(lines) if i >= after and text in line)

        accept = first("input iifname \"wlan0\" tcp dport 8080 accept")
        for drop in (
            f"input iifname \"wlan0\" ether saddr {{ {blocked} }} counter drop",
            f"input iifname \"wlan0\" ip saddr 192.168.50.10 ether saddr != {blocked} counter drop",
        ):
            self.assertLess(accept, first(drop), drop)
        # Replies go out through the egress chain that drops traffic to the blocked
        # device. Only the Pi's own port 8080 replies carry the mark that passes it.
        self.assertIn("priority -1;", lines[first("add chain netdev jittersim_access outgoing")])
        mark = first("add rule inet jittersim_access output oifname \"wlan0\" tcp sport 8080 meta mark set")
        self.assertIn("meta mark set 2147483648", lines[mark])
        passing = first("add rule netdev jittersim_access outgoing meta mark 2147483648 accept")
        dropping = first("add rule netdev jittersim_access outgoing ether daddr")
        arp = first("add rule netdev jittersim_access outgoing ether type arp accept")
        self.assertLess(passing, dropping)
        self.assertLess(arp, dropping)

        disabled = render_access_firewall(
            replace(plan, options=AccessOptions(enabled=False)), Topology("eth0", "wlan0")
        )
        self.assertNotIn("8080", disabled)

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
        other = AccessPlan(blocks=(SiteBlock("other", "Other", ("example.org",)),))
        self.manager.apply(self.plan)
        self.executor.fail_restarts = 1
        with self.assertRaises(CommandExecutionError):
            self.manager.apply(other)
        self.assertEqual(read_access(self.directory / "policy.json"), self.plan)
        self.assertIn("restored", self.manager.status()["error"])
        self.executor.fail_restarts = 2
        with self.assertLogs(level="ERROR"), self.assertRaises(CommandExecutionError):
            self.manager.apply(other)
        nft = [c for c in self.executor.commands if c.executable == "nft"]
        self.assertIn('iifname "wlan0" drop', nft[-1].stdin)
        self.assertIn("closed", self.manager.status()["error"])
        self.manager.restore()
        self.assertEqual(self.manager.applied, self.plan)

    def commands_named(self, executable, first_argument=None):
        return [
            c for c in self.executor.commands
            if c.executable == executable
            and (first_argument is None or c.arguments[0] == first_argument)
        ]

    def test_a_plan_without_dns_rules_never_needs_the_resolver(self):
        # MAC lists and an enabled policy with no site rules use no DNS at all. A
        # broken or missing Unbound must not refuse them.
        self.executor.fail_restarts = 100
        for plan in (
            AccessPlan(),
            AccessPlan(mac=MacPolicy("denylist", (), ("02:00:00:00:00:01",))),
        ):
            self.manager.apply(plan)
            self.assertEqual(self.manager.applied, plan)
            self.assertIsNone(self.manager.error)
        self.assertEqual(self.commands_named("systemctl", "restart"), [])
        self.assertEqual(self.commands_named("dig"), [])
        self.assertEqual(self.commands_named("unbound-checkconf"), [])
        self.assertTrue(self.commands_named("systemctl", "stop"))
        self.assertTrue(all(c.ignore_failure for c in self.commands_named("systemctl", "stop")))

    def test_the_resolver_restarts_only_when_dns_rules_exist(self):
        self.manager.apply(self.plan)
        self.assertEqual(len(self.commands_named("systemctl", "restart")), 1)
        self.assertEqual(len(self.commands_named("unbound-checkconf")), 1)
        self.manager.apply(AccessPlan())
        self.assertEqual(len(self.commands_named("systemctl", "restart")), 1)
        # No resolver runs while nothing is redirected to it.
        self.assertEqual(len(self.commands_named("systemctl", "stop")), 1)
        before = len(self.commands_named("dig"))
        self.manager.check_health()
        self.assertEqual(len(self.commands_named("dig")), before)
        self.assertIsNone(self.manager.status()["error"])

    def test_a_failing_resolver_refuses_dns_rules_but_never_blocks_the_release(self):
        self.executor.fail_restarts = 100
        with self.assertRaises(CommandExecutionError):
            self.manager.apply(self.plan)
        self.assertTrue(self.manager.dns_failed)
        self.assertIsNotNone(self.manager.status()["error"])
        # Panic and Normal access need no resolver.
        self.manager.disable()
        self.assertFalse(self.manager.dns_failed)
        self.assertFalse(self.manager.applied.options.enabled)
        self.assertIsNone(self.manager.error)
        guard = [c for c in self.commands_named("nft") if "access_transition" in c.stdin]
        self.assertIn("delete table inet jittersim_access_transition", guard[-1].stdin)

    # Pi-only mode: every household device is on the test Wi-Fi, so a release
    # that falls back to the old rules, or leaves the transition guard, takes
    # the whole household offline (docs/pi-only-datapath-audit-2026-10-09.md).

    def restrictive_plan(self):
        return replace(
            self.plan,
            mac=MacPolicy("denylist", (), ("02:00:00:00:00:01",)),
            options=AccessOptions(reject_wifi_joins=True),
        )

    def last_access_rules(self):
        return [c.stdin for c in self.commands_named("nft")
                if "jittersim_access " in c.stdin and "add chain inet jittersim_access" in c.stdin][-1]

    def assert_released_and_open(self):
        self.assertFalse(self.manager.applied.options.enabled)
        self.assertFalse(read_access(self.directory / "policy.json").options.enabled)
        guard = [c.stdin for c in self.commands_named("nft") if "access_transition" in c.stdin]
        self.assertIn("delete table inet jittersim_access_transition", guard[-1])
        self.assertNotIn("ether saddr", self.last_access_rules())
        self.assertNotIn("redirect", self.last_access_rules())

    def test_a_release_opens_forwarding_even_when_the_access_point_refuses_admission(self):
        with patch.object(self.manager, "admission_supported", return_value=True):
            self.manager.apply(self.restrictive_plan())
            original = self.executor.run

            def run(command):
                if command.executable == "hostapd_cli":
                    original(command)
                    return CommandResult(return_code=0, stdout="FAIL\n")
                return original(command)

            # A refused admission change never stops the release, but excluded
            # devices still cannot join, so the release is incomplete (G1).
            with patch.object(self.executor, "run", run), self.assertLogs(level="WARNING"):
                self.manager.disable()
            # Internet is open again. The join limit is reported as not cleared.
            self.assert_released_and_open()
            self.assertIn("join", self.manager.status()["error"])
            self.assertTrue(self.manager.release_incomplete)
            # A retried Panic that clears the join limits completes the recovery.
            self.manager.disable()
        self.assertFalse(self.manager.release_incomplete)
        self.assertIsNone(self.manager.status()["error"])

    def failing_conntrack(self, failures):
        """Fail the next `failures` conntrack calls, like a missing kernel module."""

        original = self.executor.run
        remaining = [failures]

        def run(command):
            result = original(command)
            if command.executable == "conntrack" and remaining[0] > 0:
                remaining[0] -= 1
                return CommandResult(return_code=1, stderr="conntrack v1.4.7: module missing\n")
            return result

        return patch.object(self.executor, "run", run)

    def resolver_commands(self):
        return [(index, c.arguments[0]) for index, c in enumerate(self.executor.commands)
                if c.executable == "systemctl" and "jittersim-dns.service" in c.arguments]

    def test_a_release_keeps_a_permissive_resolver_while_old_dns_flows_remain(self):
        # G3: old DNS flows still point at the resolver port. Stopping the
        # resolver before they are cleared would leave those clients without DNS.
        self.manager.apply(self.plan)
        before = len(self.executor.commands)
        with self.failing_conntrack(100), patch("jittersim.networking.access.time.sleep"), \
                self.assertLogs(level="WARNING"):
            self.manager.disable()
        self.assert_released_and_open()
        self.assertTrue(self.manager.release_incomplete)
        self.assertIn("DNS", self.manager.status()["error"])
        # The cleanup was retried a bounded number of times.
        attempts = [c for c in self.executor.commands[before:] if c.executable == "conntrack"]
        self.assertEqual(len(attempts), 2 * DNS_CLEANUP_ATTEMPTS)
        # The resolver was restarted with the permissive configuration, never stopped.
        actions = [action for index, action in self.resolver_commands() if index >= before]
        self.assertEqual(actions, ["restart"])
        disabled = replace(self.plan, options=replace(self.plan.options, enabled=False))
        self.assertEqual((self.directory / "unbound.conf").read_text(), render_dns(disabled))
        # A retried Panic still has the old flows to clear, even though the
        # saved rules are already off.
        with self.failing_conntrack(100), patch("jittersim.networking.access.time.sleep"), \
                self.assertLogs(level="WARNING"):
            self.manager.disable()
        self.assertTrue(self.manager.release_incomplete)
        # Once the old flows are cleared the resolver stops and recovery is complete.
        before = len(self.executor.commands)
        self.manager.disable()
        self.assertFalse(self.manager.release_incomplete)
        self.assertIsNone(self.manager.status()["error"])
        self.assertEqual([action for index, action in self.resolver_commands() if index >= before],
                         ["stop"])

    def test_a_release_retries_the_dns_cleanup_before_stopping_the_resolver(self):
        self.manager.apply(self.plan)
        before = len(self.executor.commands)
        with self.failing_conntrack(1), patch("jittersim.networking.access.time.sleep"):
            self.manager.disable()
        self.assert_released_and_open()
        self.assertFalse(self.manager.release_incomplete)
        self.assertIsNone(self.manager.status()["error"])
        cleared = max(index for index, c in enumerate(self.executor.commands)
                      if c.executable == "conntrack")
        stops = [index for index, action in self.resolver_commands()
                 if index >= before and action == "stop"]
        self.assertEqual(len(stops), 1)
        self.assertGreater(stops[0], cleared)

    def test_a_failed_dns_cleanup_without_earlier_dns_rules_is_only_logged(self):
        # Nothing was redirected to the resolver, so no DNS flow depends on it.
        mac_only = AccessPlan(mac=MacPolicy("denylist", (), ("02:00:00:00:00:02",)))
        self.manager.apply(mac_only)
        with self.failing_conntrack(100), patch("jittersim.networking.access.time.sleep"), \
                self.assertLogs(level="WARNING"):
            self.manager.disable()
        self.assert_released_and_open()
        self.assertFalse(self.manager.release_incomplete)
        self.assertIsNone(self.manager.status()["error"])

    def test_the_dns_cleanup_retries_stay_within_their_budget(self):
        self.assertLessEqual(DNS_CLEANUP_ATTEMPTS, 3)
        self.assertLess(DNS_CLEANUP_BUDGET_SECONDS, 5)

    def test_a_release_never_falls_back_to_the_previous_restrictive_rules(self):
        self.manager.apply(self.plan)
        original = self.executor.run

        def run(command):
            # Replacing the access table fails, removing it still works.
            if command.executable == "nft" and "add chain inet jittersim_access dns" in (command.stdin or ""):
                original(command)
                raise CommandExecutionError("simulated nft failure")
            return original(command)

        with patch.object(self.executor, "run", run), self.assertLogs(level="WARNING"):
            self.manager.disable()
        removal = self.commands_named("nft")[-1].stdin
        self.assertIn("delete table inet jittersim_access\n", removal)
        self.assertIn("delete table netdev jittersim_access\n", removal)
        self.assertIn("delete table inet jittersim_access_transition", removal)
        self.assertFalse(self.manager.applied.options.enabled)
        self.assertFalse(read_access(self.directory / "policy.json").options.enabled)
        self.assertEqual(self.commands_named("systemctl", "restart")[1:], [])

    def test_an_address_refresh_never_closes_the_whole_test_network(self):
        client = AccessClient("child", "192.168.50.10", "02:00:00:00:00:02", ("global",))
        plan = AccessPlan(blocks=(SiteBlock("global", "Global", ("example.com",), device_id="child"),),
                          clients=(client,))
        self.manager.apply(plan)
        before = len(self.executor.commands)

        self.manager.refresh_addresses(
            [{"mac_address": "02:00:00:00:00:02", "ipv4_address": "192.168.50.11"}])

        self.assertEqual(self.manager.applied.clients[0].address, "192.168.50.11")
        guards = [c.stdin for c in self.executor.commands[before:]
                  if c.executable == "nft" and "jittersim_access_transition" in c.stdin]
        self.assertFalse(any('iifname "wlan0" drop' in stdin for stdin in guards))

    def test_a_failed_stop_of_the_unused_resolver_is_ignored(self):
        self.manager.apply(self.plan)
        stops = []
        original = self.executor.run

        def run(command):
            if command.executable == "systemctl" and command.arguments[0] == "stop":
                stops.append(command)
                raise CommandExecutionError("simulated stop failure")
            return original(command)

        with patch.object(self.executor, "run", run):
            self.manager.apply(AccessPlan())
        self.assertTrue(stops)
        self.assertEqual(self.manager.applied, AccessPlan())

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


class HouseholdReleaseTests(unittest.TestCase):
    """Panic's household release must never leave the test Wi-Fi closed."""

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
        admission = patch.object(self.manager, "admission_supported", return_value=True)
        admission.start()
        self.addCleanup(admission.stop)
        self.restrictive = AccessPlan(
            blocks=(SiteBlock("global", "Global", ("example.com",)),),
            options=AccessOptions(reject_wifi_joins=True),
            mac=MacPolicy("denylist", (), ("02:00:00:00:00:01",)),
        )
        self.manager.apply(self.restrictive)
        self.mark = len(self.executor.commands)

    def failing(self, predicate, result=None):
        original = self.executor.run

        def run(command):
            if predicate(command):
                self.executor.commands.append(command)
                if result is not None:
                    return result
                raise CommandExecutionError("simulated timeout")
            return original(command)

        return patch.object(self.executor, "run", run)

    def release_nft(self):
        return [c.stdin for c in self.executor.commands[self.mark:] if c.executable == "nft"]

    def assert_open(self):
        nft = self.release_nft()
        access = [s for s in nft if "jittersim_access " in s or "inet jittersim_access\n" in s]
        self.assertTrue(access, "the permissive access table was never installed")
        self.assertNotIn("ether saddr", access[-1])
        self.assertNotIn("redirect", access[-1])
        guard = [s for s in nft if "jittersim_access_transition" in s]
        self.assertTrue(guard, "the transition guard was never cleared")
        self.assertIn("delete table inet jittersim_access_transition", guard[-1])
        # The restrictive rules are never reinstalled after the release started.
        self.assertFalse(any("ether saddr" in s for s in nft))

    def test_a_timing_out_hostapd_still_opens_the_test_network(self):
        with self.failing(lambda c: c.executable == "hostapd_cli"), \
                self.assertLogs("jittersim.networking.access", level="WARNING"):
            status = self.manager.disable()
        self.assert_open()
        self.assertFalse(status["enabled"])
        self.assertFalse(self.manager.applied.options.enabled)
        self.assertFalse(read_access(self.directory / "policy.json").options.enabled)
        self.assertIn("join limits", status["error"])

    def test_a_failing_conntrack_is_only_a_warning(self):
        from jittersim.networking.commands import CommandResult

        with self.failing(lambda c: c.executable == "conntrack",
                          CommandResult(return_code=1, stderr="conntrack v1.4: module missing")), \
                self.assertLogs("jittersim.networking.access", level="WARNING"):
            self.manager.disable()
        self.assert_open()
        self.assertFalse(self.manager.applied.options.enabled)

    def test_a_failing_open_firewall_falls_back_to_removing_the_tables(self):
        def permissive(command):
            return (command.executable == "nft" and "jittersim_access_transition" not in command.stdin
                    and "--check" not in command.arguments)

        with self.failing(permissive), self.assertLogs("jittersim.networking.access", level="WARNING"):
            self.manager.disable()
        removal = self.release_nft()[-1]
        self.assertIn("delete table inet jittersim_access\n", removal)
        self.assertIn("delete table netdev jittersim_access\n", removal)
        self.assertIn("delete table inet jittersim_access_transition", removal)
        self.assertFalse(self.manager.applied.options.enabled)
        self.assertFalse(read_access(self.directory / "policy.json").options.enabled)

    def test_when_nothing_loads_the_guard_clear_is_still_tried_and_nothing_rolls_back(self):
        def access_tables(command):
            return (command.executable == "nft" and "--check" not in command.arguments
                    and "add chain inet jittersim_access_transition" not in command.stdin
                    and not command.stdin.startswith("add table inet jittersim_access_transition"))

        with self.failing(access_tables), self.assertLogs("jittersim.networking.access", level="ERROR"), \
                self.assertRaises(CommandExecutionError):
            self.manager.disable()
        nft = self.release_nft()
        guard = [s for s in nft if "jittersim_access_transition" in s]
        self.assertIn("delete table inet jittersim_access_transition", guard[-1])
        self.assertFalse(any("ether saddr" in s for s in nft))
        self.assertEqual(self.commands_after("hostapd_cli")[0].arguments[-2:], ("macaddr_acl", "0"))

    def commands_after(self, executable):
        return [c for c in self.executor.commands[self.mark:] if c.executable == executable]

    def test_a_busy_ad_blocking_lock_does_not_block_the_release(self):
        class BusyAds:
            def access_transition(self, **_):
                raise ValueError("Ad-blocking settings are busy. Try again.")

        self.manager.ad_blocking_store = BusyAds()
        with self.assertLogs("jittersim.networking.access", level="WARNING"):
            self.manager.disable()
        self.assert_open()

    def test_panic_never_waits_unbounded_for_the_access_lock(self):
        import threading
        import time

        from jittersim.networking import access as access_module

        held, release = threading.Event(), threading.Event()

        def holder():
            with self.manager.lock:
                held.set()
                release.wait(10)

        thread = threading.Thread(target=holder)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(release.set)
        held.wait(5)
        started = time.monotonic()
        with patch.object(access_module, "RELEASE_LOCK_WAIT_SECONDS", 0.3, create=True), \
                self.assertRaisesRegex(ValueError, "busy"):
            self.manager.disable()
        self.assertLess(time.monotonic() - started, 3)

    def test_panic_never_waits_unbounded_for_the_boot_file_lock(self):
        import fcntl
        import time

        from jittersim.networking import access as access_module

        import threading

        outcome = []

        def release():
            try:
                self.manager.disable()
                outcome.append(None)
            except ValueError as error:
                outcome.append(error)

        with (self.directory / "lock").open("a") as other:
            fcntl.flock(other, fcntl.LOCK_EX)
            started = time.monotonic()
            with patch.object(access_module, "RELEASE_LOCK_WAIT_SECONDS", 0.3, create=True):
                thread = threading.Thread(target=release, daemon=True)
                thread.start()
                thread.join(5)
                stuck = thread.is_alive()
            if stuck:
                fcntl.flock(other, fcntl.LOCK_UN)
                thread.join(10)
            self.assertFalse(stuck, "Panic waited unbounded on the boot restoration lock")
            self.assertLess(time.monotonic() - started, 3)
            self.assertRegex(str(outcome[0]), "busy")
        # Once the other writer is gone the release works.
        self.manager.disable()
        self.assert_open()

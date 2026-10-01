import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import GeneralProfileRule, ServiceListRule, WebsiteFilter
from jittersim.networking.renderer import (
    SERVICE_DOWNLOAD_MARK_BASE,
    render_command_plan,
    render_reset_commands,
)
from jittersim.networking.topology import Topology

TOPOLOGY = Topology(uplink_interface="eth0", test_interface="wlan0")
SLOW = Profile(
    id="slow",
    name="Slow",
    upload=Impairment(delay_ms=300),
    download=Impairment(delay_ms=500, bandwidth_kbit=2_000),
)


def list_rule(list_id: str, *domains: str) -> ServiceListRule:
    return ServiceListRule(
        list_id=list_id,
        domains=domains,
        mark=0,
        upload_impairment=SLOW.upload,
        download_impairment=SLOW.download,
    )


def render(plan, service_addresses=None):
    commands = render_command_plan(plan=plan, topology=TOPOLOGY, service_addresses=service_addresses)
    return commands[0].stdin, commands[1:]


def only(script: str, needle: str) -> list:
    return [line for line in script.splitlines() if needle in line]


class ServiceListRenderTests(unittest.TestCase):
    def device_plan(self, *rules, **kwargs):
        return compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "slow"),),
            profiles={"slow": SLOW},
            service_list_rules=rules or (list_rule("streaming", "netflix.com"),),
            **kwargs,
        )

    def test_plans_without_lists_render_no_sets(self) -> None:
        plan = compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "slow"),),
            profiles={"slow": SLOW},
        )

        script, _ = render(plan)

        self.assertNotIn("svc_", script)
        self.assertNotIn("service-list", script)

    def test_each_list_gets_a_set_and_marks_both_directions(self) -> None:
        script, _ = render(self.device_plan())

        self.assertIn("add set inet jittersim svc_2 { type ipv4_addr; size 8192; }", script)
        download_mark = SERVICE_DOWNLOAD_MARK_BASE + 2
        self.assertIn(
            'add rule inet jittersim forward iifname "wlan0" ip daddr @svc_2 '
            'meta mark set 2 counter comment "service-list streaming upload"',
            script,
        )
        self.assertIn(
            'add rule inet jittersim forward oifname "wlan0" ip saddr @svc_2 '
            f'meta mark set {download_mark} counter comment "service-list streaming download"',
            script,
        )
        self.assertIn(
            f"add rule netdev jittersim_download download meta mark {download_mark} "
            "meta mark set 2",
            script,
        )

    def test_the_set_exists_before_any_rule_uses_it_and_rules_follow_the_device_marks(self) -> None:
        script = render(self.device_plan())[0].splitlines()

        set_line = next(i for i, line in enumerate(script) if line.startswith("add set"))
        first_use = next(i for i, line in enumerate(script) if "@svc_2" in line)
        device_mark = next(
            i for i, line in enumerate(script) if "ip saddr 192.168.50.10 meta mark set 1" in line
        )
        self.assertLess(set_line, first_use)
        self.assertLess(device_mark, first_use)

    def test_service_downloads_survive_the_download_hook_device_rule(self) -> None:
        script = render(self.device_plan())[0].splitlines()

        device_rule = next(
            i for i, line in enumerate(script)
            if "ip daddr 192.168.50.10 meta mark 0 meta mark set 1" in line
        )
        rewrite = next(i for i, line in enumerate(script) if "meta mark set 2" in line
                       and line.startswith("add rule netdev"))
        self.assertLess(device_rule, rewrite)

    def test_known_addresses_are_added_in_the_same_batch(self) -> None:
        plan = self.device_plan(list_rule("streaming", "netflix.com"), list_rule("work", "a.example"))

        script, _ = render(
            plan,
            {
                "streaming": (IPv4Address("203.0.113.9"), IPv4Address("203.0.113.5")),
                "unknown": (IPv4Address("198.51.100.1"),),
            },
        )

        self.assertEqual(
            only(script, "add element"),
            ["add element inet jittersim svc_2 { 203.0.113.5, 203.0.113.9 }"],
        )
        self.assertNotIn("198.51.100.1", script)

    def test_every_list_has_traffic_control_queues_in_both_directions(self) -> None:
        _, commands = render(self.device_plan())

        replacements = [command for command in commands if command.arguments[:2] == ("qdisc", "replace")
                        and "netem" in command.arguments]
        self.assertEqual(len(replacements), 4)
        filters = [command.arguments for command in commands if command.arguments[0] == "filter"
                   and "handle" in command.arguments and "2" in command.arguments]
        self.assertTrue(any("eth0" in arguments for arguments in filters))
        self.assertTrue(any("wlan0" in arguments for arguments in filters))

    def test_protected_clients_keep_their_exemption_ahead_of_the_list_rules(self) -> None:
        plan = compile_plan(
            devices=(),
            profiles={"slow": SLOW},
            protected_mac_addresses=("aa:bb:cc:dd:ee:ff",),
            service_list_rules=(list_rule("streaming", "netflix.com"),),
        )

        script = render(plan)[0].splitlines()

        exempt = [i for i, line in enumerate(script) if "unaffected by profiles" in line]
        use = next(i for i, line in enumerate(script) if "@svc_1" in line and "iifname" in line)
        self.assertEqual(len(exempt), 3)
        self.assertTrue(all(index < use for index in exempt))

    def test_service_traffic_is_not_cut_by_the_devices_own_profile_behaviour(self) -> None:
        plan = self.device_plan(
            website_filters=(
                WebsiteFilter(
                    device_ip=IPv4Address("192.168.50.10"),
                    allowed_addresses=(IPv4Address("203.0.113.20"),),
                    blocked_addresses=(IPv4Address("203.0.113.30"),),
                ),
            ),
            blocked_device_addresses=(IPv4Address("192.168.50.10"),),
        )

        script, _ = render(plan)

        for comment in (
            "enforce profile website allowlist",
            "block progression website destinations",
        ):
            (line,) = only(script, comment)
            self.assertIn("ip daddr != @svc_2", line)
        forward_outage = only(script, "temporarily disconnect randomized clients")
        guarded = [line for line in forward_outage if " forward " in line]
        unguarded = [line for line in forward_outage if " input " in line]
        self.assertIn("ip daddr != @svc_2", guarded[0])
        self.assertNotIn("@svc_2", unguarded[0])

    def test_service_traffic_is_not_cut_by_a_general_profile_outage_or_allowlist(self) -> None:
        plan = compile_plan(
            devices=(),
            profiles={"slow": SLOW},
            general_profile=GeneralProfileRule(
                profile_id="slow",
                allowed_addresses=(IPv4Address("203.0.113.20"),),
                outage_active=True,
            ),
            service_list_rules=(list_rule("streaming", "netflix.com"),),
        )

        script, _ = render(plan)

        for comment in ("general profile website allowlist", "general profile outage"):
            upload = [line for line in only(script, comment) if "inet jittersim forward" in line]
            self.assertEqual(len(upload), 1, comment)
            self.assertIn("ip daddr != @svc_2", upload[0])
        self.assertIn("@svc_2", only(script, "service-list streaming download")[0])

    def test_global_access_rules_still_apply_to_service_traffic(self) -> None:
        plan = compile_plan(
            devices=(),
            profiles={"slow": SLOW},
            global_blocked_addresses=(IPv4Address("203.0.113.66"),),
            service_list_rules=(list_rule("streaming", "netflix.com"),),
        )

        script, _ = render(plan)

        (line,) = only(script, "block global website destinations")
        self.assertNotIn("@svc", line)

    def test_reset_still_removes_both_tables_so_sets_never_outlive_a_reset(self) -> None:
        commands = render_reset_commands(topology=TOPOLOGY)

        deleted = [command.arguments for command in commands if command.executable == "nft"]
        self.assertIn(("delete", "table", "inet", "jittersim"), deleted)
        self.assertIn(("delete", "table", "netdev", "jittersim_download"), deleted)


if __name__ == "__main__":
    unittest.main()

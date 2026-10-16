"""A device with its own profile beside a running Everyone profile: plan, codec and rules.

The shared Everyone queue keeps mark 1. A device that opts out of Everyone gets
the next mark and its own queues. Every other client keeps the exact Everyone
rules, counters and queues it had.
"""

import re
import unittest
from dataclasses import replace
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.codec import PlanCodecError, plan_from_payload, plan_to_payload
from jittersim.engine.compiler import PlanError, compile_plan
from jittersim.engine.plan import (
    GENERAL_QUEUE_ID,
    Direction,
    EveryoneElseRule,
    GeneralProfileRule,
    ServiceAssignmentRule,
    ServiceListRule,
    ServiceQuotaRule,
    WebsiteFilter,
)
from jittersim.networking.renderer import (
    GENERAL_DOWNLOAD_MARK,
    render_command_plan,
)
from jittersim.networking.topology import Topology

TOPOLOGY = Topology(uplink_interface="eth0", test_interface="wlan0")
EVERYONE = Profile(
    id="everyone", name="Everyone", upload=Impairment(delay_ms=40),
    download=Impairment(delay_ms=60, bandwidth_kbit=4_000),
)
OWN = Profile(
    id="own", name="Own", upload=Impairment(delay_ms=3_000),
    download=Impairment(delay_ms=2_000),
)
PROFILES = {"everyone": EVERYONE, "own": OWN}
AIR = Device("air", "Air", "192.168.50.178", "own")
PHONE = Device("phone", "Phone", "192.168.50.179", "own")


def mixed(devices=(AIR,), **kwargs):
    return compile_plan(
        devices=devices,
        profiles=PROFILES,
        general_profile=kwargs.pop("general_profile", GeneralProfileRule("everyone")),
        **kwargs,
    )


def render(plan):
    commands = render_command_plan(plan=plan, topology=TOPOLOGY)
    return commands[0].stdin, commands[1:]


def only(script, needle):
    return [line for line in script.splitlines() if needle in line]


class MixedPlanCompilationTests(unittest.TestCase):
    def test_everyone_keeps_mark_one_and_the_device_gets_the_next_mark(self) -> None:
        plan = mixed()

        self.assertEqual([(rule.device_id, rule.mark) for rule in plan.mark_rules], [("air", 2)])
        queues = {(queue.device_id, queue.direction): queue for queue in plan.queues}
        self.assertEqual(queues[(GENERAL_QUEUE_ID, Direction.UPLOAD)].mark, 1)
        self.assertEqual(queues[(GENERAL_QUEUE_ID, Direction.DOWNLOAD)].mark, 1)
        self.assertEqual(queues[("air", Direction.UPLOAD)].mark, 2)
        self.assertEqual(queues[("air", Direction.UPLOAD)].impairment, OWN.upload)
        self.assertEqual(queues[("air", Direction.DOWNLOAD)].impairment, OWN.download)
        self.assertEqual(
            queues[(GENERAL_QUEUE_ID, Direction.UPLOAD)].impairment, EVERYONE.upload)
        self.assertEqual(plan.general_profile.profile_id, "everyone")

    def test_a_plan_without_exceptions_is_exactly_what_it_was(self) -> None:
        plain = compile_plan(
            devices=(), profiles=PROFILES, general_profile=GeneralProfileRule("everyone"))
        self.assertEqual(plain.mark_rules, ())
        self.assertEqual([queue.mark for queue in plain.queues], [1, 1])
        # Devices without Everyone still start at mark 1.
        device_only = compile_plan(devices=(AIR,), profiles=PROFILES)
        self.assertEqual([rule.mark for rule in device_only.mark_rules], [1])

    def test_devices_quotas_assignments_and_lists_never_share_a_mark(self) -> None:
        quota = ServiceQuotaRule(
            "video", "air", AIR.ipv4_address, (IPv4Address("203.0.113.5"),), 0,
            OWN.upload, OWN.download, True)
        assignment = ServiceAssignmentRule(
            "docs", "air", AIR.ipv4_address, ("docs.example",), 0, OWN.upload, OWN.download)
        service_list = ServiceListRule("lists", ("list.example",), 0, OWN.upload, OWN.download)

        plan = mixed(
            (AIR, PHONE), service_quota_rules=(quota,),
            service_assignment_rules=(assignment,), service_list_rules=(service_list,))

        marks = [rule.mark for rule in plan.mark_rules]
        marks += [rule.mark for rule in plan.service_quota_rules]
        marks += [rule.mark for rule in plan.service_assignment_rules]
        marks += [rule.mark for rule in plan.service_list_rules]
        self.assertEqual(marks, [2, 3, 4, 5, 6])
        queue_marks = sorted({queue.mark for queue in plan.queues})
        self.assertEqual(queue_marks, [1, 2, 3, 4, 5, 6])

    def test_website_rules_and_outages_of_the_device_are_kept(self) -> None:
        website = WebsiteFilter(AIR.ipv4_address, (IPv4Address("203.0.113.9"),))

        plan = mixed(website_filters=(website,), blocked_device_addresses=(AIR.ipv4_address,))

        self.assertEqual(plan.website_filters, (website,))
        self.assertEqual(plan.blocked_device_addresses, (AIR.ipv4_address,))

    def test_everyone_and_everyone_else_stay_exclusive(self) -> None:
        with self.assertRaises(PlanError):
            compile_plan(
                devices=(AIR,), profiles=PROFILES,
                general_profile=GeneralProfileRule("everyone"),
                everyone_else=EveryoneElseRule(0, EVERYONE.upload, EVERYONE.download))

    def test_everyone_plus_fifteen_devices_is_too_many_classes(self) -> None:
        devices = tuple(
            Device(f"d{index:02d}", f"D{index}", f"192.168.50.{index + 10}", "own")
            for index in range(15))
        with self.assertRaises(PlanError):
            mixed(devices)
        self.assertEqual(len(mixed(devices[:14]).mark_rules), 14)

    def test_a_filter_for_a_device_outside_the_plan_is_still_refused(self) -> None:
        stranger = WebsiteFilter(IPv4Address("192.168.50.99"), ())
        with self.assertRaises(PlanError):
            mixed(website_filters=(stranger,))

    def test_a_protected_client_still_cannot_have_its_own_rules(self) -> None:
        protected = replace(AIR, mac_address="dc:1e:d5:3e:97:4c")
        with self.assertRaises(PlanError):
            mixed((protected,), protected_mac_addresses=("dc:1e:d5:3e:97:4c",))


class MixedPlanCodecTests(unittest.TestCase):
    def test_a_mixed_plan_survives_the_wire_unchanged(self) -> None:
        website = WebsiteFilter(AIR.ipv4_address, (IPv4Address("203.0.113.9"),))
        plan = mixed(
            (AIR, PHONE), website_filters=(website,),
            blocked_device_addresses=(PHONE.ipv4_address,))

        decoded = plan_from_payload(plan_to_payload(plan))

        self.assertEqual(decoded.general_profile.profile_id, "everyone")
        self.assertEqual([(rule.device_id, rule.mark) for rule in decoded.mark_rules],
                         [("air", 2), ("phone", 3)])
        self.assertEqual(decoded.queues, plan.queues)
        self.assertEqual(decoded.website_filters, (website,))
        self.assertEqual(decoded.blocked_device_addresses, (PHONE.ipv4_address,))

    def test_a_device_cannot_take_the_everyone_mark(self) -> None:
        payload = plan_to_payload(mixed())
        payload["mark_rules"][0]["mark"] = 1
        for queue in payload["queues"]:
            if queue["device_id"] == "air":
                queue["mark"] = 1

        with self.assertRaises(PlanCodecError):
            plan_from_payload(payload)

    def test_a_service_rule_cannot_take_the_everyone_mark(self) -> None:
        quota = ServiceQuotaRule(
            "video", "air", AIR.ipv4_address, (IPv4Address("203.0.113.5"),), 0,
            OWN.upload, OWN.download, True)
        payload = plan_to_payload(mixed(service_quota_rules=(quota,)))
        payload["service_quota_rules"][0]["mark"] = 1
        for queue in payload["queues"]:
            if queue["device_id"] == "quota:video":
                queue["mark"] = 1

        with self.assertRaises(PlanCodecError):
            plan_from_payload(payload)

    def test_everyone_else_still_cannot_join_everyone_on_the_wire(self) -> None:
        payload = plan_to_payload(mixed())
        payload["everyone_else"] = {
            "mark": 3, "upload_impairment": payload["queues"][0]["impairment"],
            "download_impairment": payload["queues"][0]["impairment"],
            "unaffected_macs": [], "blocked": False,
        }
        with self.assertRaises(PlanCodecError):
            plan_from_payload(payload)


class MixedPlanRuleTests(unittest.TestCase):
    """The generated nftables and tc move the device out of Everyone and leave the rest alone."""

    def test_everyone_rules_skip_the_device_in_both_directions(self) -> None:
        script, _ = render(mixed())
        saved = "{ 192.168.50.178 }"

        upload = [line for line in only(script, "usage-device general-profile upload")
                  if "forward" in line]
        self.assertEqual(len(upload), 1)
        self.assertIn(f"meta nfproto ipv4 ip saddr != {saved} meta mark set 1 counter", upload[0])
        (identify,) = only(script, "identify forwarded test downloads")
        self.assertIn(f"ip daddr != {saved} meta mark set {GENERAL_DOWNLOAD_MARK}", identify)
        # IPv6 downloads are identified exactly as before.
        (identify_v6,) = only(script, "identify forwarded IPv6 test downloads")
        self.assertIn(f"meta nfproto ipv6 meta mark set {GENERAL_DOWNLOAD_MARK}", identify_v6)

    def test_the_device_gets_its_own_marks_counters_and_download_rule(self) -> None:
        script, commands = render(mixed())

        (upload,) = only(script, 'iifname "wlan0" ip saddr 192.168.50.178 meta mark set 2 counter')
        self.assertIn('comment "usage-device air upload"', upload)
        self.assertEqual(len(only(script, 'comment "usage-device air download"')), 1)
        (download,) = only(script, "ip daddr 192.168.50.178 meta mark 0 meta mark set 2")
        self.assertIn("netdev jittersim_download download", download)
        # Everyone's mark 1 keeps its download counter and is never set for the device.
        self.assertEqual(len(only(script, 'comment "usage-device general-profile download"')), 1)
        # Both classes exist on both interfaces.
        handles = [command.arguments for command in commands
                   if command.arguments[:2] == ("filter", "replace")]
        marks = {arguments[arguments.index("handle") + 1] for arguments in handles}
        self.assertEqual(marks, {"1", "2"})

    def test_the_priority_bands_cover_everyone_and_the_device(self) -> None:
        _, commands = render(mixed((AIR, PHONE)))
        roots = [command.arguments for command in commands
                 if command.arguments[:3] == ("qdisc", "replace", "dev")
                 and "root" in command.arguments]
        self.assertTrue(roots)
        for arguments in roots:
            self.assertEqual(arguments[arguments.index("bands") + 1], "4")

    def test_everyones_website_allowlist_does_not_cut_the_device(self) -> None:
        general = GeneralProfileRule(
            "everyone", allowed_addresses=(IPv4Address("203.0.113.20"),))
        script, _ = render(mixed(general_profile=general))

        (allowlist,) = only(script, 'comment "general profile website allowlist"')
        self.assertIn("ip saddr != { 192.168.50.178 } ", allowlist)
        self.assertIn("ip daddr != { 203.0.113.20 }", allowlist)

    def test_an_everyone_outage_leaves_the_device_connected_but_cuts_everyone_else(self) -> None:
        general = GeneralProfileRule("everyone", outage_active=True)
        script, _ = render(mixed(general_profile=general))

        upload = [line for line in only(script, 'comment "general profile outage"')
                  if "inet jittersim forward" in line]
        self.assertEqual(len(upload), 1)
        self.assertIn("ip saddr != { 192.168.50.178 }", upload[0])
        # The device's address cannot be matched in IPv6, so IPv6 uploads stay cut.
        (ipv6,) = only(script, "general profile outage over IPv6")
        self.assertIn("meta nfproto ipv6 drop", ipv6)
        # Downloads are cut by the mark only Everyone's clients carry.
        (download,) = [line for line in only(script, 'comment "general profile outage"')
                       if "netdev" in line]
        self.assertIn(f"meta mark {{ 1, {GENERAL_DOWNLOAD_MARK} }}", download)

    def test_the_device_gets_its_own_outage_and_website_rules(self) -> None:
        website = WebsiteFilter(AIR.ipv4_address, (IPv4Address("203.0.113.9"),))
        script, _ = render(mixed(
            website_filters=(website,), blocked_device_addresses=(AIR.ipv4_address,)))

        (filter_rule,) = only(script, 'comment "enforce profile website allowlist"')
        self.assertIn("ip saddr 192.168.50.178", filter_rule)
        self.assertEqual(len(only(script, "temporarily disconnect randomized clients")), 2)

    def test_service_lists_still_win_over_the_device_profile(self) -> None:
        service_list = ServiceListRule("lists", ("list.example",), 0, OWN.upload, OWN.download)
        script, _ = render(mixed(service_list_rules=(service_list,)))

        (listed,) = only(script, "service-list lists upload")
        self.assertIn("ip daddr @svc_3 meta mark set 3", listed)
        # Mark 2 is the device. Mark 3 is the list.
        (device,) = only(script, 'ip saddr 192.168.50.178 meta mark set 2 counter')
        self.assertIn('comment "usage-device air upload"', device)

    def test_without_exceptions_the_everyone_rules_are_the_rules_they_always_were(self) -> None:
        script, _ = render(compile_plan(
            devices=(), profiles=PROFILES, general_profile=GeneralProfileRule("everyone")))

        for line in script.splitlines():
            self.assertNotIn("ip saddr !=", line)
            self.assertNotIn("ip daddr !=", line)
        self.assertEqual(only(script, "IPv6"), [])
        (upload,) = [line for line in only(script, "usage-device general-profile upload")
                     if "forward" in line]
        self.assertTrue(re.search(r"meta nfproto ipv4 meta mark set 1 counter", upload))

    def test_non_exceptions_rules_do_not_depend_on_which_device_is_the_exception(self) -> None:
        first, _ = render(mixed((AIR,)))
        second, _ = render(mixed((PHONE,)))

        def everyone_lines(script):
            return [re.sub(r"192\.168\.50\.17\d", "ADDR", line)
                    for line in script.splitlines()
                    if "general profile" in line or "general-profile" in line
                    or "test downloads" in line]

        self.assertEqual(everyone_lines(first), everyone_lines(second))


if __name__ == "__main__":
    unittest.main()

import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.codec import PlanCodecError, plan_from_payload, plan_to_payload
from jittersim.engine.compiler import PlanError, compile_plan
from jittersim.engine.plan import (
    GENERAL_QUEUE_ID,
    Direction,
    GeneralProfileRule,
    ServiceAssignmentRule,
    ServiceListRule,
    service_list_set_name,
)

SLOW = Profile(
    id="slow",
    name="Slow",
    upload=Impairment(delay_ms=300),
    download=Impairment(loss_percent=10, bandwidth_kbit=2_000),
)


def rule(list_id: str, *domains: str) -> ServiceListRule:
    return ServiceListRule(
        list_id=list_id,
        domains=domains,
        mark=0,
        upload_impairment=SLOW.upload,
        download_impairment=SLOW.download,
    )


class CompileServiceListTests(unittest.TestCase):
    def test_lists_need_no_devices_and_get_their_own_queues(self) -> None:
        plan = compile_plan(
            devices=(),
            profiles={"slow": SLOW},
            service_list_rules=(rule("streaming", "netflix.com"),),
        )

        (compiled,) = plan.service_list_rules
        self.assertEqual(compiled.mark, 1)
        self.assertEqual(compiled.domains, ("netflix.com",))
        self.assertEqual(
            [(queue.device_id, queue.direction, queue.mark) for queue in plan.queues],
            [
                ("servicelist:streaming", Direction.UPLOAD, 1),
                ("servicelist:streaming", Direction.DOWNLOAD, 1),
            ],
        )
        self.assertEqual(plan.queues[1].impairment, SLOW.download)

    def test_marks_follow_devices_quotas_and_assignments_in_list_id_order(self) -> None:
        plan = compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "slow"),),
            profiles={"slow": SLOW},
            service_assignment_rules=(
                ServiceAssignmentRule(
                    assignment_id="video",
                    device_id="alice",
                    device_ip=IPv4Address("192.168.50.10"),
                    domains=("youtube.com",),
                    mark=0,
                    upload_impairment=SLOW.upload,
                    download_impairment=SLOW.download,
                ),
            ),
            service_list_rules=(rule("zeta", "b.example"), rule("alpha", "a.example")),
        )

        self.assertEqual(
            [(item.list_id, item.mark) for item in plan.service_list_rules],
            [("alpha", 3), ("zeta", 4)],
        )

    def test_lists_work_beside_the_general_profile(self) -> None:
        plan = compile_plan(
            devices=(),
            profiles={"slow": SLOW},
            general_profile=GeneralProfileRule(profile_id="slow"),
            service_list_rules=(rule("streaming", "netflix.com"),),
        )

        self.assertEqual(plan.service_list_rules[0].mark, 2)
        self.assertEqual(
            {queue.device_id for queue in plan.queues},
            {GENERAL_QUEUE_ID, "servicelist:streaming"},
        )

    def test_hostnames_are_normalised_and_deduplicated(self) -> None:
        plan = compile_plan(
            devices=(),
            profiles={"slow": SLOW},
            service_list_rules=(rule("a", "Netflix.COM.", "netflix.com"),),
        )

        self.assertEqual(plan.service_list_rules[0].domains, ("netflix.com",))

    def test_rejects_a_hostname_claimed_by_two_lists_or_nested_inside_another(self) -> None:
        for first, second in (
            ("netflix.com", "netflix.com"),
            ("microsoft.com", "teams.microsoft.com"),
            ("teams.microsoft.com", "microsoft.com"),
        ):
            with self.subTest(first=first, second=second):
                with self.assertRaisesRegex(PlanError, "overlap"):
                    compile_plan(
                        devices=(),
                        profiles={"slow": SLOW},
                        service_list_rules=(rule("one", first), rule("two", second)),
                    )

    def test_rejects_empty_duplicate_and_address_only_rules(self) -> None:
        for rules, message in (
            ((rule("empty"),), "no hostnames"),
            ((rule("dup", "a.example"), rule("dup", "b.example")), "duplicate"),
            ((rule("ip", "203.0.113.4"),), "hostnames"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(PlanError, message):
                compile_plan(devices=(), profiles={"slow": SLOW}, service_list_rules=rules)

    def test_rejects_too_many_traffic_classes_and_hostnames(self) -> None:
        many_lists = tuple(rule(f"list-{index}", f"h{index}.example") for index in range(16))
        with self.assertRaisesRegex(PlanError, "15"):
            compile_plan(devices=(), profiles={"slow": SLOW}, service_list_rules=many_lists)

        too_many_names = tuple(
            rule(f"big-{index}", *(f"h{index}-{number}.example" for number in range(100)))
            for index in range(5)
        )
        with self.assertRaisesRegex(PlanError, "hostnames"):
            compile_plan(devices=(), profiles={"slow": SLOW}, service_list_rules=too_many_names)

    def test_devices_and_lists_share_the_fifteen_class_limit(self) -> None:
        devices = tuple(
            Device(f"d{index:02d}", f"D{index}", f"192.168.50.{10 + index}", "slow")
            for index in range(15)
        )
        with self.assertRaisesRegex(PlanError, "15"):
            compile_plan(
                devices=devices,
                profiles={"slow": SLOW},
                service_list_rules=(rule("one", "a.example"),),
            )


class ServiceListCodecTests(unittest.TestCase):
    def build(self, *rules, general=False):
        return compile_plan(
            devices=() if general else (Device("alice", "Alice", "192.168.50.10", "slow"),),
            profiles={"slow": SLOW},
            general_profile=GeneralProfileRule(profile_id="slow") if general else None,
            service_list_rules=rules or (
                rule("streaming", "netflix.com", "nflxvideo.net"),
            ),
        )

    def test_round_trips_beside_devices_and_beside_the_general_profile(self) -> None:
        for plan in (self.build(), self.build(general=True)):
            self.assertEqual(plan_from_payload(plan_to_payload(plan)), plan)

    def test_a_payload_without_lists_still_decodes(self) -> None:
        payload = plan_to_payload(self.build())
        payload.pop("service_list_rules")
        payload["queues"] = payload["queues"][:2]

        self.assertEqual(plan_from_payload(payload).service_list_rules, ())

    def test_rejects_a_rule_that_disagrees_with_its_queue(self) -> None:
        payload = plan_to_payload(self.build())
        payload["service_list_rules"][0]["upload_impairment"]["delay_ms"] = 999

        with self.assertRaisesRegex(PlanCodecError, "does not match"):
            plan_from_payload(payload)

    def test_rejects_unsafe_list_ids_and_hostnames(self) -> None:
        for field, value in (
            ("list_id", 'bad"; drop table inet jittersim; #'),
            ("list_id", "UPPER"),
            ("domains", ["bad host.example"]),
            ("domains", ["203.0.113.9"]),
            ("domains", []),
            ("domains", ["a.example", "a.example"]),
        ):
            payload = plan_to_payload(self.build())
            payload["service_list_rules"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(PlanCodecError):
                plan_from_payload(payload)

    def test_rejects_bad_marks_and_clashes_with_other_classes(self) -> None:
        for mark in (0, 16, -1, 1, True, "2"):
            payload = plan_to_payload(self.build())
            payload["service_list_rules"][0]["mark"] = mark
            with self.subTest(mark=mark), self.assertRaises(PlanCodecError):
                plan_from_payload(payload)

    def test_rejects_overlapping_lists_and_missing_queues(self) -> None:
        payload = plan_to_payload(self.build(rule("one", "a.example"), rule("two", "b.example")))
        payload["service_list_rules"][1]["domains"] = ["x.a.example"]
        with self.assertRaisesRegex(PlanCodecError, "overlap"):
            plan_from_payload(payload)

        payload = plan_to_payload(self.build())
        payload["queues"] = payload["queues"][:2]
        with self.assertRaises(PlanCodecError):
            plan_from_payload(payload)

    def test_set_names_are_fixed_by_mark(self) -> None:
        plan = self.build(rule("a", "a.example"), rule("b", "b.example"))

        self.assertEqual(
            [service_list_set_name(item) for item in plan.service_list_rules],
            ["svc_2", "svc_3"],
        )

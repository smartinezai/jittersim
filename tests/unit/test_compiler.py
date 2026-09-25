import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import PlanError, compile_plan
from jittersim.engine.plan import (
    EVERYONE_ELSE_QUEUE_ID,
    Direction,
    EveryoneElseRule,
    ServiceQuotaRule,
)


def make_device(device_id: str, address: str, profile_id: str = "normal") -> Device:
    return Device(
        id=device_id,
        name=device_id,
        ipv4_address=address,
        profile_id=profile_id,
    )


class CompilePlanTests(unittest.TestCase):
    def test_compiles_two_directions_for_each_device(self) -> None:
        profile = Profile(
            id="slow",
            name="Slow",
            upload=Impairment(bandwidth_kbit=1_000),
            download=Impairment(delay_ms=500),
        )

        plan = compile_plan(
            devices=(make_device("alice", "192.168.50.10", "slow"),),
            profiles={"slow": profile},
        )

        self.assertEqual(len(plan.mark_rules), 1)
        self.assertEqual(len(plan.queues), 2)
        self.assertEqual(plan.queues[0].direction, Direction.UPLOAD)
        self.assertEqual(plan.queues[0].impairment.bandwidth_kbit, 1_000)
        self.assertEqual(plan.queues[1].direction, Direction.DOWNLOAD)
        self.assertEqual(plan.queues[1].impairment.delay_ms, 500)

    def test_assigns_marks_in_stable_device_id_order(self) -> None:
        devices = (
            make_device("zara", "192.168.50.12"),
            make_device("alice", "192.168.50.10"),
            make_device("bob", "192.168.50.11"),
        )
        normal = {"normal": Profile(id="normal", name="Normal")}

        plan = compile_plan(devices=devices, profiles=normal)

        self.assertEqual(
            [(rule.device_id, rule.mark) for rule in plan.mark_rules],
            [("alice", 1), ("bob", 2), ("zara", 3)],
        )

    def test_rejects_duplicate_device_ids(self) -> None:
        devices = (
            make_device("alice", "192.168.50.10"),
            make_device("alice", "192.168.50.11"),
        )

        with self.assertRaisesRegex(PlanError, "duplicate device id"):
            compile_plan(
                devices=devices,
                profiles={"normal": Profile(id="normal", name="Normal")},
            )

    def test_rejects_duplicate_addresses(self) -> None:
        devices = (
            make_device("alice", "192.168.50.10"),
            make_device("bob", "192.168.50.10"),
        )

        with self.assertRaisesRegex(PlanError, "assigned to both"):
            compile_plan(
                devices=devices,
                profiles={"normal": Profile(id="normal", name="Normal")},
            )

    def test_rejects_missing_profile_reference(self) -> None:
        with self.assertRaisesRegex(PlanError, "missing profile"):
            compile_plan(
                devices=(make_device("alice", "192.168.50.10", "missing"),),
                profiles={},
            )

    def test_rejects_more_devices_than_the_prio_layout_supports(self) -> None:
        devices = tuple(
            make_device(f"device-{index}", f"192.168.50.{index + 10}")
            for index in range(16)
        )

        with self.assertRaisesRegex(PlanError, "more than 15 devices"):
            compile_plan(
                devices=devices,
                profiles={"normal": Profile(id="normal", name="Normal")},
            )

    def test_rejects_a_combined_device_and_service_queue_overflow(self) -> None:
        devices = tuple(
            make_device(f"device-{index}", f"192.168.50.{index + 10}")
            for index in range(15)
        )
        quota = ServiceQuotaRule(
            quota_id="video",
            device_id="device-0",
            device_ip=IPv4Address("192.168.50.10"),
            destination_addresses=(IPv4Address("203.0.113.10"),),
            mark=0,
            upload_impairment=Impairment(delay_ms=100),
            download_impairment=Impairment(),
        )

        with self.assertRaisesRegex(PlanError, "combined devices and service rules"):
            compile_plan(
                devices=devices,
                profiles={"normal": Profile(id="normal", name="Normal")},
                service_quota_rules=(quota,),
            )


class EveryoneElseCompileTests(unittest.TestCase):
    slow = Profile(id="slow", name="Slow")

    def rule(self, **changes):
        values = {"mark": 0, "upload_impairment": Impairment(), "download_impairment": Impairment()}
        values.update(changes)
        return EveryoneElseRule(**values)

    def test_everyone_else_gets_the_mark_after_the_other_rules(self) -> None:
        plan = compile_plan(
            devices=(make_device("alice", "192.168.50.10", "slow"),),
            profiles={"slow": self.slow},
            everyone_else=self.rule(
                upload_impairment=Impairment(delay_ms=100),
                unaffected_macs=("BB:BB:BB:BB:BB:BB", "aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"),
            ),
        )

        self.assertEqual(
            plan.everyone_else,
            self.rule(
                mark=2,
                upload_impairment=Impairment(delay_ms=100),
                unaffected_macs=("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"),
            ),
        )
        self.assertEqual(
            [(queue.device_id, queue.direction, queue.mark) for queue in plan.queues[2:]],
            [
                (EVERYONE_ELSE_QUEUE_ID, Direction.UPLOAD, 2),
                (EVERYONE_ELSE_QUEUE_ID, Direction.DOWNLOAD, 2),
            ],
        )

    def test_everyone_else_counts_toward_the_fifteen_marks(self) -> None:
        devices = tuple(
            make_device(f"d{index}", f"192.168.50.{10 + index}", "slow") for index in range(15)
        )

        with self.assertRaises(PlanError):
            compile_plan(devices=devices, profiles={"slow": self.slow}, everyone_else=self.rule())

if __name__ == "__main__":
    unittest.main()

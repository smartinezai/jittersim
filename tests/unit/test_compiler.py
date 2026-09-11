import unittest

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import PlanError, compile_plan
from jittersim.engine.plan import Direction


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


if __name__ == "__main__":
    unittest.main()

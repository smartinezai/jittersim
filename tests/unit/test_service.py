import unittest

from jittersim.config.models import Device, Impairment, Profile, UnaffectedDevice
from jittersim.config.storage import ConfigRepository
from jittersim.engine.service import compile_stored_plan


class StoredPlanServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")

    def tearDown(self) -> None:
        self.repository.close()

    def test_compiles_all_persisted_assignments(self) -> None:
        self.repository.save_profile(
            Profile(
                id="slow",
                name="Slow",
                upload=Impairment(delay_ms=3_000),
                download=Impairment(loss_percent=10),
            )
        )
        self.repository.save_device(
            Device(
                id="alice",
                name="Alice",
                ipv4_address="192.168.50.10",
                profile_id="slow",
            )
        )

        plan = compile_stored_plan(repository=self.repository)

        self.assertEqual([rule.device_id for rule in plan.mark_rules], ["alice"])
        self.assertEqual(plan.queues[0].impairment.delay_ms, 3_000)
        self.assertEqual(plan.queues[1].impairment.loss_percent, 10)

    def test_empty_repository_compiles_to_an_empty_plan(self) -> None:
        plan = compile_stored_plan(repository=self.repository)

        self.assertEqual(plan.mark_rules, ())
        self.assertEqual(plan.queues, ())


    def test_the_stored_plan_includes_everyone_else(self) -> None:
        self.repository.save_profile(
            Profile(id="slow", name="Slow", download=Impairment(delay_ms=100))
        )
        self.repository.save_everyone_else_profile_id("slow")
        self.repository.save_unaffected_device(UnaffectedDevice("aa:aa:aa:aa:aa:aa"))

        plan = compile_stored_plan(repository=self.repository)

        self.assertEqual(plan.everyone_else.download_impairment, Impairment(delay_ms=100))
        self.assertEqual(plan.everyone_else.unaffected_macs, ("aa:aa:aa:aa:aa:aa",))

if __name__ == "__main__":
    unittest.main()

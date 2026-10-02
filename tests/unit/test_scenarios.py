import unittest

from jittersim.config.models import Scenario, ScenarioStep
from jittersim.engine.scenarios import ScenarioPosition, scenario_position

STEPS = (
    ScenarioStep(profile_id="3g", duration_seconds=60),
    ScenarioStep(profile_id="offline", duration_seconds=30),
    ScenarioStep(profile_id="normal", duration_seconds=120),
)


class ScenarioPositionTests(unittest.TestCase):
    def test_steps_follow_each_other_and_the_scenario_ends(self) -> None:
        scenario = Scenario(id="commute", name="Commute", steps=STEPS)

        def at(seconds: float):
            return scenario_position(scenario, started_at=1_000, now=1_000 + seconds)

        self.assertEqual(at(0), ScenarioPosition(0, "3g", 60, 210))
        # Time left rounds up, so it reaches 0 only when the step is over.
        self.assertEqual(at(59.5), ScenarioPosition(0, "3g", 1, 151))
        self.assertEqual(at(60), ScenarioPosition(1, "offline", 30, 150))
        self.assertEqual(at(90), ScenarioPosition(2, "normal", 120, 120))
        self.assertIsNone(at(210))

    def test_a_repeating_scenario_starts_over(self) -> None:
        scenario = Scenario(id="commute", name="Commute", steps=STEPS, loop=True)

        def at(seconds: float):
            return scenario_position(scenario, started_at=1_000, now=1_000 + seconds)

        self.assertEqual(at(210), ScenarioPosition(0, "3g", 60, None))
        self.assertEqual(at(275), ScenarioPosition(1, "offline", 25, None))
        self.assertEqual(at(10 * 210 + 95), ScenarioPosition(2, "normal", 115, None))

    def test_a_clock_before_the_start_counts_as_the_start(self) -> None:
        scenario = Scenario(id="commute", name="Commute", steps=STEPS)

        self.assertEqual(
            scenario_position(scenario, started_at=1_000, now=900),
            ScenarioPosition(0, "3g", 60, 210),
        )


if __name__ == "__main__":
    unittest.main()

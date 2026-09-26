import unittest

from jittersim.engine.activity import Source, TargetState, activity_events

OWN = Source("profile", profile="Normal")
SCHEDULE = Source("schedule", label="work-hours", profile="Slow")
TIMER = Source("timer", profile="Offline")
STEP_ONE = Source("scenario", label="Commute", step="step 1 of 2", profile="3G")
STEP_TWO = Source("scenario", label="Commute", step="step 2 of 2", profile="Edge")
LIMIT = Source("data-limit", profile="Trickle")
GROUP_LIMIT = Source("group-data-limit", label="Family", profile="Trickle")
PROGRESSION = Source("progression", label="from Normal to Slow")


def change(before: Source, after: Source, **changes) -> list:
    old = {"alice": TargetState("Alice", before)}
    new = {"alice": TargetState("Alice", after, **changes)}
    return activity_events(old, new)


class ActivityEventTests(unittest.TestCase):
    def test_a_source_that_takes_over_is_described_with_its_profile(self) -> None:
        self.assertEqual(
            [change(OWN, source) for source in (SCHEDULE, TIMER, STEP_ONE, LIMIT, PROGRESSION)],
            [
                [("schedule", "Alice: schedule work-hours started, now Slow")],
                [("timer", "Alice: timer started, now Offline")],
                [("scenario", "Alice: scenario Commute, step 1 of 2, now 3G")],
                [("limit", "Alice reached its data limit, now Trickle")],
                [("progression", "Alice: progression started, from Normal to Slow")],
            ],
        )
        self.assertEqual(
            change(OWN, GROUP_LIMIT),
            [("limit", "Alice: group Family reached its shared data limit, now Trickle")],
        )

    def test_a_scenario_step_is_an_event(self) -> None:
        self.assertEqual(
            change(STEP_ONE, STEP_TWO),
            [("scenario", "Alice: scenario Commute, step 2 of 2, now Edge")],
        )

    def test_an_ending_says_what_applies_again(self) -> None:
        self.assertEqual(
            [change(source, OWN) for source in (SCHEDULE, TIMER, STEP_TWO, LIMIT, PROGRESSION)],
            [
                [("schedule", "Alice: schedule work-hours ended, back to Normal")],
                [("timer", "Alice: timer ended, back to Normal")],
                [("scenario", "Alice: scenario Commute ended, back to Normal")],
                [("limit", "Alice: data limit reset, back to Normal")],
                [("progression", "Alice: progression ended, back to Normal")],
            ],
        )
        # A timer that ends during a schedule hands back to the schedule.
        self.assertEqual(
            change(TIMER, SCHEDULE),
            [("timer", "Alice: timer ended, back to schedule work-hours, Slow")],
        )
        # A schedule that starts during a timer is hidden by it, so nothing shows.
        self.assertEqual(change(SCHEDULE, TIMER), [("timer", "Alice: timer started, now Offline")])

    def test_everyone_else_goes_back_to_a_normal_connection(self) -> None:
        old = {"everyone": TargetState("Everyone else", SCHEDULE)}
        new = {"everyone": TargetState("Everyone else", Source("none"))}

        self.assertEqual(
            activity_events(old, new),
            [("schedule", "Everyone else: schedule work-hours ended, back to a normal connection")],
        )

    def test_usual_profile_changes_are_not_events(self) -> None:
        self.assertEqual(change(OWN, Source("group-profile", label="Family", profile="Slow")), [])
        self.assertEqual(change(OWN, Source("profile", profile="Fast")), [])
        self.assertEqual(change(SCHEDULE, SCHEDULE), [])

    def test_outages_and_service_limits(self) -> None:
        self.assertEqual(change(OWN, OWN, outage=True), [("outage", "Alice: outage started")])
        self.assertEqual(
            activity_events(
                {"alice": TargetState("Alice", OWN, outage=True, limits=("Video",))},
                {"alice": TargetState("Alice", OWN, limits=("Music",))},
            ),
            [
                ("outage", "Alice: outage ended"),
                ("limit", "Alice reached its service limit Music"),
                ("limit", "Alice: service limit Video reset"),
            ],
        )

    def test_devices_that_appear_or_go_are_not_events(self) -> None:
        self.assertEqual(
            activity_events(
                {"bob": TargetState("Bob", OWN)}, {"alice": TargetState("Alice", TIMER)}
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()

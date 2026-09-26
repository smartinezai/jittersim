import unittest

from jittersim.engine.activity import Source, TargetState, activity_events

OWN = Source("profile", "profile", profile="Normal")
SCHEDULE = Source("schedule", "schedule:work-hours", label="work-hours", profile="Slow")
TIMER = Source("timer", "timer:t1", profile="Offline")
STEP_ONE = Source("scenario", "scenario:run-1:0", "Commute", "step 1 of 2", "3G")
STEP_TWO = Source("scenario", "scenario:run-1:1", "Commute", "step 2 of 2", "Edge")
LIMIT = Source("data-limit", "data-limit", profile="Trickle")
PROGRESSION = Source("progression", "progression:p1", label="from Normal to Slow")


def change(before: Source, after: Source, **changes) -> list:
    old = {"alice": TargetState("Alice", before)}
    new = {"alice": TargetState("Alice", after, **changes)}
    return activity_events(old, new)


def group_member(name: str, source: Source) -> TargetState:
    return TargetState(name, source)


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

    def test_a_scenario_step_is_an_event(self) -> None:
        self.assertEqual(
            change(STEP_ONE, STEP_TWO),
            [("scenario", "Alice: scenario Commute, step 2 of 2, now Edge")],
        )

    def test_a_new_timer_or_run_is_an_event_even_with_the_same_names(self) -> None:
        # Starting a timer replaces the running one; the report must show it.
        self.assertEqual(
            change(TIMER, Source("timer", "timer:t2", profile="Offline")),
            [("timer", "Alice: timer started, now Offline")],
        )
        self.assertEqual(
            change(
                STEP_ONE, Source("scenario", "scenario:run-2:0", "Commute", "step 1 of 2", "3G")
            ),
            [("scenario", "Alice: scenario Commute, step 1 of 2, now 3G")],
        )

    def test_renaming_is_not_an_event(self) -> None:
        renamed = Source("scenario", "scenario:run-1:0", "Morning commute", "step 1 of 2", "3G")

        self.assertEqual(change(STEP_ONE, renamed), [])
        self.assertEqual(
            change(PROGRESSION, Source("progression", "progression:p1", label="from Fast to Slow")),
            [],
        )

    def test_an_ending_says_what_applies_again(self) -> None:
        self.assertEqual(
            [change(source, OWN) for source in (SCHEDULE, TIMER, STEP_TWO, LIMIT, PROGRESSION)],
            [
                [("schedule", "Alice: schedule work-hours ended, back to Normal")],
                [("timer", "Alice: timer ended, back to Normal")],
                [("scenario", "Alice: scenario Commute ended, back to Normal")],
                [("limit", "Alice: data limit lifted, back to Normal")],
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

    def test_a_group_change_is_one_event(self) -> None:
        family = Source(
            "scenario", "scenario:run-9:1", "Commute", "step 2 of 2", "Edge", group="Family"
        )
        before = Source(
            "scenario", "scenario:run-9:0", "Commute", "step 1 of 2", "3G", group="Family"
        )
        old = {name: group_member(name, before) for name in ("alice", "bob", "carol")}
        new = {name: group_member(name, family) for name in ("alice", "bob", "carol")}

        self.assertEqual(
            activity_events(old, new),
            [("scenario", "Group Family: scenario Commute, step 2 of 2, now Edge")],
        )

    def test_a_group_ending_says_back_to_what_its_members_share(self) -> None:
        schedule = Source(
            "schedule", "schedule:evening", label="evening", profile="Slow", group="Family"
        )
        shared = Source("group-profile", "group-profile:family", "Family", profile="Medium")
        old = {"alice": group_member("Alice", schedule), "bob": group_member("Bob", schedule)}

        together = {"alice": group_member("Alice", shared), "bob": group_member("Bob", shared)}
        apart = {
            "alice": group_member("Alice", OWN),
            "bob": group_member("Bob", Source("profile", "profile", profile="Fast")),
        }

        self.assertEqual(
            activity_events(old, together),
            [("schedule", "Group Family: schedule evening ended, back to Medium")],
        )
        self.assertEqual(
            activity_events(old, apart),
            [
                (
                    "schedule",
                    "Group Family: schedule evening ended, back to each device's own settings",
                )
            ],
        )

    def test_a_shared_data_limit_is_one_event(self) -> None:
        reached = Source(
            "group-data-limit", "group-data-limit:family", "Family", profile="Trickle",
            group="Family",
        )
        old = {"alice": group_member("Alice", OWN), "bob": group_member("Bob", OWN)}
        new = {"alice": group_member("Alice", reached), "bob": group_member("Bob", reached)}

        self.assertEqual(
            activity_events(old, new),
            [("limit", "Group Family reached its shared data limit, now Trickle")],
        )
        self.assertEqual(
            activity_events(new, old),
            [("limit", "Group Family: shared data limit lifted, back to Normal")],
        )

    def test_everyone_else_goes_back_to_a_normal_connection(self) -> None:
        old = {"everyone": TargetState("Everyone else", SCHEDULE)}
        new = {"everyone": TargetState("Everyone else", Source("none", "none"))}

        self.assertEqual(
            activity_events(old, new),
            [("schedule", "Everyone else: schedule work-hours ended, back to a normal connection")],
        )

    def test_usual_profile_changes_are_not_events(self) -> None:
        self.assertEqual(
            change(OWN, Source("group-profile", "group-profile:family", "Family", profile="Slow")),
            [],
        )
        self.assertEqual(change(OWN, Source("profile", "profile", profile="Fast")), [])
        self.assertEqual(change(SCHEDULE, SCHEDULE), [])

    def test_outages_and_limits_follow_their_identity(self) -> None:
        video = ("quota-video", "service limit Video")
        self.assertEqual(change(OWN, OWN, outage=True), [("outage", "Alice: outage started")])
        self.assertEqual(
            activity_events(
                {"alice": TargetState("Alice", OWN, outage=True, limits=(video,))},
                {"alice": TargetState("Alice", OWN, limits=(("time:music", "time limit Music"),))},
            ),
            [
                ("outage", "Alice: outage ended"),
                ("limit", "Alice reached its time limit Music"),
                ("limit", "Alice: service limit Video lifted"),
            ],
        )
        # A renamed limit is the same limit.
        self.assertEqual(
            activity_events(
                {"alice": TargetState("Alice", OWN, limits=(video,))},
                {"alice": TargetState("Alice", OWN, limits=(("quota-video", "service limit TV"),))},
            ),
            [],
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

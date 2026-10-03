"""Random variation and random outages of service lists.

The clock is the `now` that each tick receives and the random source is the
profile's seeded generator, as in the runtime tests for devices.
"""

import json
import tempfile
import unittest
from dataclasses import replace
from ipaddress import IPv4Address
from pathlib import Path
from unittest.mock import patch

from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile, Randomness, ServiceList
from jittersim.config.storage import ConfigRepository
from jittersim.config.transfer import settings_to_payload
from jittersim.engine.codec import PlanCodecError, plan_from_payload, plan_to_payload
from jittersim.engine.compiler import PlanError, compile_plan
from jittersim.engine.plan import GENERAL_QUEUE_ID, GeneralProfileRule, ServiceListRule
from jittersim.engine.randomizer import (
    outage_duration_seconds,
    outage_interval_seconds,
    outage_is_selected,
    reroll_interval_seconds,
    reroll_profile,
    seeded_generator,
)
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.service import compile_stored_plan
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.browser_proxy import ProxyError, destination_allowed, select_mark
from jittersim.networking.commands import CommandResult
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperClient, HelperClientError, HelperService
from jittersim.networking.renderer import (
    SERVICE_DOWNLOAD_MARK_BASE,
    queue_handle,
    render_command_plan,
)
from jittersim.networking.topology import Topology

LIST_KEY = "servicelist:streaming"
NORMAL = Profile(id="normal", name="Normal")
VARIABLE = Profile(
    id="variable",
    name="Variable",
    upload=Impairment(delay_ms=200, delay_randomness=Randomness(variation_percent=30)),
    download=Impairment(
        delay_ms=400,
        bandwidth_kbit=2_000,
        delay_randomness=Randomness(variation_percent=30),
        bandwidth_randomness=Randomness(variation_percent=50),
    ),
    reroll_interval_seconds=60,
    random_seed=42,
)
FLAKY = Profile(
    id="flaky",
    name="Flaky",
    upload=Impairment(delay_ms=50),
    outage_probability=100,
    outage_duration_seconds=5,
    outage_interval_seconds=10,
    random_seed=7,
)
TOPOLOGY = Topology(uplink_interface="eth0", test_interface="wlan0")
PROTECTED = "02:00:00:00:00:01"
ALICE = IPv4Address("192.168.50.10")


class RecordingHelper:
    def __init__(self) -> None:
        self.plans = []

    def apply(self, *, plan):
        self.plans.append(plan)
        return {"commands_run": 1}

    def reset(self):
        return {"commands_run": 1}

    def traffic_counters(self):
        return {"devices": {}, "services": {}}


def expected_roll(profile: Profile, roll_number: int, key: str = LIST_KEY):
    """Return the values and the next interval that roll `roll_number` gives a list."""

    generator = seeded_generator(profile=profile, device_id=key, roll_number=roll_number)
    rolled = reroll_profile(profile=profile, generator=generator)
    return rolled, reroll_interval_seconds(profile=profile, generator=generator)


def expected_outage(profile: Profile, check_number: int, key: str = LIST_KEY):
    """Return whether outage check `check_number` of a list starts an outage and its length."""

    generator = seeded_generator(
        profile=profile, device_id=key, roll_number=100_000 + check_number
    )
    if not outage_is_selected(profile=profile, generator=generator):
        return False, 0, outage_interval_seconds(profile=profile, generator=generator)
    duration = outage_duration_seconds(profile=profile, generator=generator)
    return True, duration, outage_interval_seconds(profile=profile, generator=generator)


class _SchedulerCase(unittest.TestCase):
    profiles = (NORMAL, VARIABLE, FLAKY)

    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        for profile in self.profiles:
            self.repository.save_profile(profile)
        self.helper = RecordingHelper()
        self.scheduler = self.new_scheduler(self.helper)

    @staticmethod
    def new_scheduler(helper) -> RuntimeScheduler:
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        # No background thread: each test drives the ticks with its own clock.
        with patch.object(scheduler, "start"):
            scheduler.activate(applied_payload={})
        return scheduler

    def save_list(self, profile_id: str, *, list_id="streaming", services=("netflix",), **kwargs):
        self.repository.save_service_list(
            ServiceList(
                id=list_id,
                name=list_id.title(),
                profile_id=profile_id,
                services=services,
                **kwargs,
            )
        )

    def tick(self, now: float) -> bool:
        return self.scheduler.tick(repository=self.repository, now=now)

    def rule(self, list_id: str = "streaming") -> ServiceListRule:
        return next(
            item for item in self.helper.plans[-1].service_list_rules if item.list_id == list_id
        )

    def preview_rule(self, now: float, list_id: str = "streaming") -> ServiceListRule:
        plan = self.scheduler.prepare_plan(repository=self.repository, now=now).plan
        return next(item for item in plan.service_list_rules if item.list_id == list_id)


class ServiceListRerollTests(_SchedulerCase):
    def setUp(self) -> None:
        super().setUp()
        self.save_list("variable")

    def test_the_first_apply_uses_the_saved_values_and_the_first_reroll_waits_one_interval(
        self,
    ) -> None:
        self.assertTrue(self.tick(0))
        self.assertEqual(self.rule().upload_impairment, VARIABLE.upload)
        self.assertEqual(self.rule().download_impairment, VARIABLE.download)

        self.assertFalse(self.tick(59))
        self.assertTrue(self.tick(60))

        rolled, _ = expected_roll(VARIABLE, 0)
        self.assertNotEqual(rolled.upload, VARIABLE.upload)
        self.assertEqual(self.rule().upload_impairment, rolled.upload)
        self.assertEqual(self.rule().download_impairment, rolled.download)

    def test_rerolls_follow_the_reroll_interval_and_its_variation(self) -> None:
        varied = replace(VARIABLE, reroll_interval_randomness=Randomness(variation_percent=50))
        self.repository.save_profile(varied)
        self.tick(0)
        self.tick(60)
        _, interval = expected_roll(varied, 0)
        self.assertNotEqual(interval, 60)

        self.assertFalse(self.tick(60 + interval - 1))
        self.assertTrue(self.tick(60 + interval))

        rolled, _ = expected_roll(varied, 1)
        self.assertEqual(self.rule().upload_impairment, rolled.upload)

    def test_the_same_seed_repeats_the_same_values_in_the_same_order(self) -> None:
        def delays(profile: Profile) -> list:
            repository = ConfigRepository(":memory:")
            self.addCleanup(repository.close)
            repository.save_profile(profile)
            repository.save_service_list(
                ServiceList(id="streaming", name="S", profile_id=profile.id, services=("netflix",))
            )
            helper = RecordingHelper()
            scheduler = self.new_scheduler(helper)
            for now in (0, 60, 120, 180, 240):
                scheduler.tick(repository=repository, now=now)
            return [
                plan.service_list_rules[0].upload_impairment.delay_ms for plan in helper.plans
            ]

        first = delays(VARIABLE)

        self.assertEqual(delays(VARIABLE), first)
        self.assertEqual(
            first[1:],
            [expected_roll(VARIABLE, number)[0].upload.delay_ms for number in range(4)],
        )
        self.assertNotEqual(delays(replace(VARIABLE, random_seed=43)), first)

    def test_every_affected_device_shares_one_set_of_list_values(self) -> None:
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_device(Device("bob", "Bob", "192.168.50.11", "variable"))
        self.repository.save_everyone_else_profile_id("variable")
        self.tick(0)
        self.tick(60)

        plan = self.helper.plans[-1]
        rolled, _ = expected_roll(VARIABLE, 0)
        self.assertEqual(len(plan.service_list_rules), 1)
        list_queues = [queue for queue in plan.queues if queue.device_id == LIST_KEY]
        self.assertEqual(
            {(queue.direction.value, queue.impairment) for queue in list_queues},
            {("upload", rolled.upload), ("download", rolled.download)},
        )
        # Bob and everyone else roll the same profile on their own sequences.
        bob = next(queue for queue in plan.queues if queue.device_id == "bob")
        self.assertNotEqual(bob.impairment, rolled.upload)

    def test_editing_the_profile_starts_a_fresh_roll(self) -> None:
        self.tick(0)
        self.tick(60)
        edited = replace(VARIABLE, upload=replace(VARIABLE.upload, delay_ms=500))
        self.repository.save_profile(edited)

        self.assertTrue(self.tick(61))
        self.assertEqual(self.rule().upload_impairment, edited.upload)
        self.assertFalse(self.tick(120))
        self.assertTrue(self.tick(121))
        self.assertEqual(self.rule().upload_impairment, expected_roll(edited, 0)[0].upload)

    def test_choosing_another_profile_starts_a_fresh_roll(self) -> None:
        self.tick(0)
        self.tick(60)
        self.save_list("normal")

        self.assertTrue(self.tick(61))
        self.assertEqual(self.rule().upload_impairment, NORMAL.upload)
        self.save_list("variable")
        self.tick(62)
        self.assertEqual(self.rule().upload_impairment, VARIABLE.upload)

    def test_a_disabled_list_is_not_rolled_and_starts_fresh_when_enabled(self) -> None:
        self.tick(0)
        self.tick(60)
        self.save_list("variable", enabled=False)

        self.tick(61)
        self.assertEqual(self.helper.plans[-1].service_list_rules, ())
        self.assertNotIn(LIST_KEY, self.scheduler._rolls)

        self.save_list("variable")
        self.tick(200)
        self.assertEqual(self.rule().upload_impairment, VARIABLE.upload)
        self.assertFalse(self.tick(259))
        self.assertTrue(self.tick(260))
        self.assertEqual(self.rule().upload_impairment, expected_roll(VARIABLE, 0)[0].upload)

    def test_a_preview_shows_the_next_values_without_advancing_the_roll(self) -> None:
        self.tick(0)

        preview = self.scheduler.build_plan(repository=self.repository, now=60)

        self.assertEqual(self.scheduler._rolls[LIST_KEY].roll_number, 0)
        self.assertTrue(self.tick(60))
        self.assertEqual(preview.service_list_rules[0], self.rule())
        self.assertEqual(self.scheduler._rolls[LIST_KEY].roll_number, 1)

    def test_reset_forgets_the_list_roll(self) -> None:
        self.tick(0)
        self.tick(60)

        self.scheduler.pause()
        with patch.object(self.scheduler, "start"):
            self.scheduler.activate(applied_payload={})
        self.tick(61)

        self.assertEqual(self.rule().upload_impairment, VARIABLE.upload)

    def test_lists_keep_rolling_while_the_general_profile_is_on(self) -> None:
        self.repository.save_general_profile("normal")

        self.assertTrue(self.tick(0))
        self.assertFalse(self.tick(30))
        self.assertTrue(self.tick(60))

        plan = self.helper.plans[-1]
        self.assertIsNotNone(plan.general_profile)
        self.assertIn(GENERAL_QUEUE_ID, {queue.device_id for queue in plan.queues})
        self.assertEqual(self.rule().upload_impairment, expected_roll(VARIABLE, 0)[0].upload)

    def test_two_lists_with_the_same_profile_follow_their_own_sequences(self) -> None:
        self.save_list("variable", list_id="work", services=("claude",))
        self.tick(0)
        self.tick(60)

        streaming, work = self.rule("streaming"), self.rule("work")
        self.assertEqual(streaming.upload_impairment, expected_roll(VARIABLE, 0)[0].upload)
        self.assertEqual(
            work.upload_impairment, expected_roll(VARIABLE, 0, key="servicelist:work")[0].upload
        )
        self.assertNotEqual(streaming.upload_impairment, work.upload_impairment)

    def test_the_traffic_chart_reads_the_rerolled_shared_limit(self) -> None:
        self.tick(0)
        self.assertEqual(
            self.scheduler.applied_queue_bandwidth_limits()[LIST_KEY]["download_kbit"], 2_000
        )

        self.tick(60)

        rolled, _ = expected_roll(VARIABLE, 0)
        self.assertNotEqual(rolled.download.bandwidth_kbit, 2_000)
        self.assertEqual(
            self.scheduler.applied_queue_bandwidth_limits()[LIST_KEY],
            {"upload_kbit": None, "download_kbit": rolled.download.bandwidth_kbit},
        )

    def test_configuration_only_plans_use_the_saved_values(self) -> None:
        self.save_list("flaky")

        (rule,) = compile_stored_plan(repository=self.repository).service_list_rules

        self.assertEqual(rule.upload_impairment, FLAKY.upload)
        self.assertIs(rule.outage_active, False)


class ServiceListOutageTests(_SchedulerCase):
    def setUp(self) -> None:
        super().setUp()
        self.save_list("flaky")

    def test_a_random_outage_cuts_the_list_for_its_length_then_ends(self) -> None:
        self.assertTrue(self.tick(0))
        self.assertIs(self.rule().outage_active, False)
        self.assertFalse(self.tick(9))

        self.assertTrue(self.tick(10))
        self.assertIs(self.rule().outage_active, True)
        self.assertFalse(self.tick(14))

        self.assertTrue(self.tick(15))
        self.assertIs(self.rule().outage_active, False)
        # The values of the list stay the profile's values during and after the outage.
        self.assertEqual(self.rule().upload_impairment, FLAKY.upload)

    def test_the_outage_length_follows_its_variation(self) -> None:
        varied = replace(
            FLAKY,
            random_seed=8,
            outage_duration_seconds=20,
            outage_interval_seconds=100,
            outage_duration_randomness=Randomness(variation_percent=50),
        )
        self.repository.save_profile(varied)
        self.tick(0)
        selected, duration, _ = expected_outage(varied, 0)
        self.assertTrue(selected)
        self.assertNotEqual(duration, 20)

        self.tick(100)
        self.assertIs(self.rule().outage_active, True)
        self.assertFalse(self.tick(100 + duration - 1))
        self.assertTrue(self.tick(100 + duration))
        self.assertIs(self.rule().outage_active, False)

    def test_the_outage_chance_decides_each_check(self) -> None:
        chancy = replace(
            FLAKY, outage_probability=50, outage_duration_seconds=1, outage_interval_seconds=2
        )
        self.repository.save_profile(chancy)
        self.preview_rule(0)

        # A selected one-second outage is followed by a two-second recovery interval.
        outcomes = [self.preview_rule(2 + 3 * check).outage_active for check in range(40)]

        self.assertEqual(outcomes, [expected_outage(chancy, check)[0] for check in range(40)])
        self.assertEqual(set(outcomes), {True, False})

    def test_without_a_check_interval_the_reroll_interval_is_used(self) -> None:
        self.repository.save_profile(
            replace(FLAKY, outage_interval_seconds=0, reroll_interval_seconds=30)
        )
        self.tick(0)

        self.assertFalse(self.tick(29))
        self.assertTrue(self.tick(30))
        self.assertIs(self.rule().outage_active, True)

    def test_a_profile_without_an_outage_chance_never_cuts_the_list(self) -> None:
        self.save_list("variable")

        self.assertEqual(
            {self.preview_rule(now).outage_active for now in range(0, 600, 10)}, {False}
        )

    def test_a_list_outage_leaves_devices_and_everyone_else_connected(self) -> None:
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_everyone_else_profile_id("normal")
        self.repository.save_unaffected_device(UnaffectedDevice(PROTECTED))
        self.tick(0)
        self.tick(10)

        plan = self.helper.plans[-1]
        self.assertIs(self.rule().outage_active, True)
        self.assertEqual(plan.blocked_device_addresses, ())
        self.assertIs(plan.everyone_else.blocked, False)
        self.assertIn(PROTECTED, plan.everyone_else.unaffected_macs)

    def test_a_disabled_list_never_has_an_outage(self) -> None:
        self.save_list("flaky", enabled=False)
        self.save_list("normal", list_id="work", services=("claude",))
        self.tick(0)
        self.tick(10)

        rules = self.helper.plans[-1].service_list_rules
        self.assertEqual([(rule.list_id, rule.outage_active) for rule in rules], [("work", False)])
        self.assertNotIn(LIST_KEY, self.scheduler._rolls)

    def test_each_list_rolls_its_own_outages(self) -> None:
        chancy = replace(
            FLAKY, outage_probability=50, outage_duration_seconds=1, outage_interval_seconds=2
        )
        self.repository.save_profile(chancy)
        self.save_list("flaky", list_id="work", services=("claude",))
        self.preview_rule(0)
        outcomes = {"streaming": [], "work": []}
        for check in range(40):
            plan = self.scheduler.prepare_plan(repository=self.repository, now=2 + 3 * check).plan
            for rule in plan.service_list_rules:
                outcomes[rule.list_id].append(rule.outage_active)

        self.assertNotEqual(outcomes["streaming"], outcomes["work"])
        self.assertEqual(
            outcomes["work"],
            [expected_outage(chancy, check, key="servicelist:work")[0] for check in range(40)],
        )

    def test_lists_keep_their_outages_while_the_general_profile_is_on(self) -> None:
        self.repository.save_general_profile("normal")
        self.tick(0)
        self.tick(10)

        self.assertIs(self.rule().outage_active, True)
        self.assertIs(self.helper.plans[-1].general_profile.outage_active, False)

    def test_list_outages_are_recorded_in_the_activity_log(self) -> None:
        for now in (0, 10, 15):
            self.tick(now)

        self.assertEqual(
            [event["message"] for event in reversed(self.repository.list_activity())],
            ["Service list Streaming: outage started", "Service list Streaming: outage ended"],
        )

    def test_a_list_with_the_longest_id_records_its_outage(self) -> None:
        long_id = "s" * 64
        self.save_list("flaky", enabled=False)
        self.save_list("flaky", list_id=long_id, services=("claude",))
        for now in (0, 10, 15):
            self.tick(now)

        self.assertEqual(len(self.repository.list_activity()), 2)
        self.assertIn(f"servicelist:{long_id}", self.scheduler.applied_target_states())


class ServiceListPlanTests(unittest.TestCase):
    def plan(self, outage_active=True):
        return compile_plan(
            devices=(Device("alice", "Alice", str(ALICE), "normal"),),
            profiles={"normal": NORMAL},
            service_list_rules=(
                ServiceListRule(
                    "streaming",
                    ("netflix.com",),
                    0,
                    FLAKY.upload,
                    FLAKY.download,
                    outage_active=outage_active,
                ),
            ),
        )

    def test_the_compiler_carries_the_outage_and_rejects_anything_but_true_or_false(self) -> None:
        self.assertIs(self.plan().service_list_rules[0].outage_active, True)
        self.assertIs(self.plan(False).service_list_rules[0].outage_active, False)
        for value in ("yes", 1, None):
            with self.subTest(value=value), self.assertRaisesRegex(PlanError, "outage"):
                self.plan(value)

    def test_the_outage_round_trips_through_the_wire_format(self) -> None:
        payload = plan_to_payload(self.plan())

        self.assertIs(payload["service_list_rules"][0]["outage_active"], True)
        self.assertEqual(plan_from_payload(payload), self.plan())
        self.assertIs(
            plan_to_payload(self.plan(False))["service_list_rules"][0]["outage_active"], False
        )

    def test_a_plan_from_an_older_dashboard_loads_without_an_outage(self) -> None:
        payload = plan_to_payload(self.plan())
        del payload["service_list_rules"][0]["outage_active"]

        self.assertIs(plan_from_payload(payload).service_list_rules[0].outage_active, False)

    def test_the_wire_format_rejects_anything_but_true_or_false(self) -> None:
        for value in ("true", 1, None, [], {}):
            payload = plan_to_payload(self.plan())
            payload["service_list_rules"][0]["outage_active"] = value
            with self.subTest(value=value), self.assertRaisesRegex(PlanCodecError, "outage"):
                plan_from_payload(payload)


def render(plan, addresses=None):
    commands = render_command_plan(plan=plan, topology=TOPOLOGY, service_addresses=addresses)
    return commands[0].stdin, commands[1:]


def line_index(lines, *needles) -> int:
    return next(i for i, line in enumerate(lines) if all(needle in line for needle in needles))


class ServiceListOutageRenderTests(unittest.TestCase):
    def plan(self, *outages, general=False, protected=(PROTECTED,), blocked=()):
        rules = tuple(
            ServiceListRule(
                list_id,
                (f"{list_id}.example",),
                0,
                VARIABLE.upload,
                VARIABLE.download,
                outage_active=outage,
            )
            for list_id, outage in (outages or (("streaming", True),))
        )
        return compile_plan(
            devices=() if general else (Device("alice", "Alice", str(ALICE), "normal"),),
            profiles={"normal": NORMAL},
            protected_mac_addresses=protected,
            general_profile=GeneralProfileRule("normal") if general else None,
            blocked_device_addresses=blocked,
            service_list_rules=rules,
        )

    def test_a_list_without_an_outage_renders_no_drop(self) -> None:
        script, _ = render(self.plan(("streaming", False)))

        self.assertNotIn("service list outage", script)

    def test_an_outage_drops_both_directions_and_the_proxy_mark(self) -> None:
        script, _ = render(self.plan())

        download_mark = SERVICE_DOWNLOAD_MARK_BASE + 2
        self.assertEqual(
            [line for line in script.splitlines() if "service list outage" in line],
            [
                'add rule inet jittersim forward iifname "wlan0" ip daddr @svc_2 drop '
                'comment "service list outage"',
                f"add rule netdev jittersim_download download meta mark {download_mark} drop "
                'comment "service list outage"',
                'add rule inet jittersim output oifname "eth0" meta mark 2 drop '
                'comment "service list outage"',
            ],
        )

    def test_protected_devices_return_before_every_outage_drop(self) -> None:
        lines = render(self.plan())[0].splitlines()

        forward_return = line_index(lines, "forward", "ether saddr", "unaffected by profiles")
        download_return = line_index(lines, "download ether daddr", "unaffected by profiles")
        upload_drop = line_index(lines, "forward", "@svc_2 drop")
        download_drop = line_index(lines, "download meta mark", "drop", "service list outage")
        self.assertLess(forward_return, upload_drop)
        self.assertLess(download_return, download_drop)

    def test_the_drops_come_before_the_rules_that_shape_the_list(self) -> None:
        lines = render(self.plan())[0].splitlines()
        download_mark = SERVICE_DOWNLOAD_MARK_BASE + 2

        self.assertLess(
            line_index(lines, "forward", "@svc_2 drop"),
            line_index(lines, "ip daddr @svc_2 meta mark set 2"),
        )
        self.assertLess(
            line_index(lines, f"download meta mark {download_mark} drop"),
            line_index(lines, f"download meta mark {download_mark} meta mark set 2"),
        )

    def test_the_pi_itself_stays_reachable_during_an_outage(self) -> None:
        script = render(self.plan())[0]
        quiet = render(self.plan(("streaming", False)))[0]

        input_rules = [line for line in script.splitlines() if " input " in line]
        self.assertEqual(input_rules, [line for line in quiet.splitlines() if " input " in line])
        self.assertIn("tcp dport 8080 return", script)
        output_drops = [
            line for line in script.splitlines() if " output " in line and "drop" in line
        ]
        self.assertEqual(len(output_drops), 1)
        self.assertIn('oifname "eth0" meta mark 2 drop', output_drops[0])

    def test_only_the_list_in_an_outage_is_cut(self) -> None:
        script = render(self.plan(("streaming", True), ("work", False)))[0]

        self.assertIn("@svc_2 drop", script)
        self.assertNotIn("@svc_3 drop", script)
        self.assertIn(f"meta mark {SERVICE_DOWNLOAD_MARK_BASE + 2} drop", script)
        self.assertNotIn(f"meta mark {SERVICE_DOWNLOAD_MARK_BASE + 3} drop", script)
        self.assertNotIn("meta mark 3 drop", script)

    def test_an_outage_beside_the_general_profile_cuts_the_list_and_not_the_profile(self) -> None:
        lines = render(self.plan(general=True))[0].splitlines()

        self.assertNotIn("general profile outage", "\n".join(lines))
        self.assertLess(
            line_index(lines, "forward", "ether saddr", "unaffected by profiles"),
            line_index(lines, "forward", "@svc_2 drop"),
        )

    def test_a_reroll_changes_the_list_queue_in_place(self) -> None:
        before = self.plan(("streaming", False))
        rolled, _ = expected_roll(VARIABLE, 0)
        after = compile_plan(
            devices=(Device("alice", "Alice", str(ALICE), "normal"),),
            profiles={"normal": NORMAL},
            protected_mac_addresses=(PROTECTED,),
            service_list_rules=(
                ServiceListRule(
                    "streaming", ("streaming.example",), 0, rolled.upload, rolled.download
                ),
            ),
        )

        def list_queue_commands(plan):
            return [
                command.arguments
                for command in render(plan)[1]
                if "parent" in command.arguments and "10:3" in command.arguments
            ]

        old, new = list_queue_commands(before), list_queue_commands(after)
        upload = next(
            arguments for arguments in new if "eth0" in arguments and "replace" in arguments
        )
        self.assertIn(f"{rolled.upload.delay_ms}ms", upload)
        self.assertNotIn(f"{VARIABLE.upload.delay_ms}ms", upload)
        # The same commands on the same handles: tc changes the netem in place.
        self.assertEqual(
            [arguments[:9] for arguments in old], [arguments[:9] for arguments in new]
        )
        self.assertNotIn("fq_codel", [arguments[8] for arguments in new if len(arguments) > 8])


class ServiceListOutageProxyTests(unittest.TestCase):
    def plan(self, outage=True, general=False, protected=(PROTECTED,)):
        return compile_plan(
            devices=() if general else (Device("alice", "Alice", str(ALICE), "normal"),),
            profiles={"normal": NORMAL},
            protected_mac_addresses=protected,
            general_profile=GeneralProfileRule("normal") if general else None,
            service_list_rules=(
                ServiceListRule(
                    "streaming",
                    ("netflix.com",),
                    0,
                    FLAKY.upload,
                    FLAKY.download,
                    outage_active=outage,
                ),
            ),
        )

    def test_a_listed_host_is_refused_during_an_outage(self) -> None:
        for host in ("netflix.com", "www.netflix.com"):
            with self.subTest(host=host), self.assertRaisesRegex(ProxyError, "outage"):
                select_mark(self.plan(), client_ip=ALICE, host=host, client_mac="02:00:00:00:00:02")

    def test_other_hosts_and_the_list_without_an_outage_keep_working(self) -> None:
        affected = "02:00:00:00:00:02"
        self.assertEqual(
            select_mark(self.plan(), client_ip=ALICE, host="example.com", client_mac=affected),
            (1, "device"),
        )
        self.assertEqual(
            select_mark(self.plan(False), client_ip=ALICE, host="netflix.com", client_mac=affected),
            (2, "service-list:streaming"),
        )

    def test_a_protected_client_keeps_reaching_a_listed_host(self) -> None:
        self.assertEqual(
            select_mark(self.plan(), client_ip=ALICE, host="netflix.com", client_mac=PROTECTED),
            (0, "unaffected"),
        )
        self.assertTrue(destination_allowed(self.plan(), ALICE, "203.0.113.5", PROTECTED))

    def test_the_general_profile_does_not_save_a_listed_host_during_an_outage(self) -> None:
        plan = self.plan(general=True)

        with self.assertRaisesRegex(ProxyError, "outage"):
            select_mark(plan, client_ip=ALICE, host="netflix.com", client_mac="02:00:00:00:00:02")
        self.assertEqual(
            select_mark(plan, client_ip=ALICE, host="example.com", client_mac="02:00:00:00:00:02"),
            (1, "general-profile"),
        )


def list_plan(*, outage=False, impairment=VARIABLE.upload):
    return compile_plan(
        devices=(Device("alice", "Alice", str(ALICE), "normal"),),
        profiles={"normal": NORMAL},
        service_list_rules=(
            ServiceListRule(
                "streaming",
                ("streaming.example",),
                0,
                impairment,
                VARIABLE.download,
                outage_active=outage,
            ),
        ),
    )


class ServiceListOutageHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = FakeExecutor()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.service = HelperService(
            NetworkApplier(executor=self.executor, topology=TOPOLOGY),
            active_plan_path=Path(directory.name) / "active-plan.json",
        )

    def apply(self, plan) -> None:
        response = self.service.handle_payload(
            {"action": "apply-experiment", "plan": plan_to_payload(plan)}
        )
        self.assertTrue(response["ok"], response)

    def test_the_helper_announces_list_outages(self) -> None:
        capabilities = self.service.handle_payload({"action": "capabilities"})

        self.assertIs(capabilities["service_list_outages"], True)

    def test_rerolls_and_outages_keep_the_learned_addresses(self) -> None:
        self.apply(list_plan())
        response = self.service.handle_payload(
            {"action": "service-addresses", "addresses": {"streaming": ["203.0.113.1"]}}
        )
        self.assertEqual(response["added"], 1)
        rolled = replace(VARIABLE.upload, delay_ms=233)

        for plan in (
            list_plan(impairment=rolled),
            list_plan(impairment=rolled, outage=True),
            list_plan(),
        ):
            self.executor.commands.clear()
            self.apply(plan)
            script = next(
                command.stdin for command in self.executor.commands if command.executable == "nft"
            )
            with self.subTest(outage=plan.service_list_rules[0].outage_active):
                self.assertIn("add element inet jittersim svc_2 { 203.0.113.1 }", script)
                self.assertEqual(
                    "@svc_2 drop" in script, plan.service_list_rules[0].outage_active
                )


class _QueueExecutor(FakeExecutor):
    """Answer queue reads with fixed counters like `tc -s -j qdisc show`."""

    def __init__(self, stats):
        super().__init__()
        self.stats = stats

    def run(self, command):
        if command.executable == "tc" and command.arguments[:3] == ("-s", "-j", "qdisc"):
            interface = command.arguments[-1]
            return CommandResult(return_code=0, stdout=json.dumps(self.stats[interface]))
        return super().run(command)


class ServiceListImpactTests(unittest.TestCase):
    def test_the_impact_check_shows_the_rerolled_values_during_an_outage(self) -> None:
        def counters(handle, packets):
            return {"kind": "netem", "handle": handle, "parent": "10:3", "packets": packets,
                    "bytes": packets * 100, "drops": 0, "overlimits": 0, "requeues": 0,
                    "backlog": 0, "qlen": 0}

        handle = queue_handle(2)
        service = HelperService(
            NetworkApplier(
                executor=_QueueExecutor(
                    {"eth0": [counters(handle, 7)], "wlan0": [counters(handle, 9)]}
                ),
                topology=TOPOLOGY,
            )
        )
        rolled = replace(VARIABLE.upload, delay_ms=233)
        plan = list_plan(impairment=rolled, outage=True)
        self.assertTrue(
            service.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})["ok"]
        )

        queues = service.handle_payload({"action": "queue-stats"})["queues"]

        self.assertEqual(
            [(item["device_id"], item["direction"], item["packets"]) for item in queues
             if item["device_id"] == LIST_KEY],
            [(LIST_KEY, "upload", 7), (LIST_KEY, "download", 9)],
        )
        upload = next(item for item in queues if item["device_id"] == LIST_KEY)
        self.assertEqual(upload["impairment"]["delay_ms"], 233)


class ServiceListOutageClientTests(unittest.TestCase):
    def client(self, capabilities):
        client = HelperClient(socket_path=Path("/nonexistent/helper.sock"))
        sent = []

        def answer(payload, **kwargs):
            sent.append(payload["action"])
            if payload["action"] == "capabilities":
                return {"ok": True, **capabilities}
            if payload["action"] == "control-revision":
                return {"ok": True, "control_revision": "a" * 32, "recovery_required": False}
            return {"ok": True, "action": payload["action"], "commands_run": 1}

        client._request = answer
        return client, sent

    def test_an_outage_needs_a_helper_that_supports_it(self) -> None:
        client, sent = self.client({"service_lists": True})

        for apply in (client.apply, client.apply_experiment):
            with self.subTest(apply=apply.__name__):
                with self.assertRaisesRegex(HelperClientError, "Update the Pi helper"):
                    apply(plan=list_plan(outage=True))
        self.assertNotIn("apply", sent)
        self.assertNotIn("apply-experiment", sent)

    def test_rerolled_values_work_with_a_fenced_helper_without_outage_support(self) -> None:
        client, sent = self.client({"service_lists": True})

        client.apply_experiment(plan=list_plan(impairment=replace(VARIABLE.upload, delay_ms=233)))

        self.assertEqual(sent[-1], "apply-experiment")

    def test_a_current_helper_applies_the_outage(self) -> None:
        client, sent = self.client({"service_lists": True, "service_list_outages": True})

        client.apply_experiment(plan=list_plan(outage=True))

        self.assertEqual(sent[-1], "apply-experiment")


class ServiceListVariationStorageTests(unittest.TestCase):
    def test_a_moved_installation_repeats_the_same_values_and_outages(self) -> None:
        from jittersim.api.server import _settings_from_payload

        source = ConfigRepository(":memory:")
        self.addCleanup(source.close)
        profile = replace(
            VARIABLE,
            outage_probability=50,
            outage_duration_seconds=3,
            outage_interval_seconds=7,
            outage_duration_randomness=Randomness(variation_percent=20),
        )
        source.save_profile(profile)
        source.save_service_list(
            ServiceList(
                id="streaming", name="Streaming", profile_id="variable", services=("netflix",)
            )
        )
        target = ConfigRepository(":memory:")
        self.addCleanup(target.close)
        target.replace_settings(
            _settings_from_payload(settings_to_payload(source.saved_settings(), exported_at=0))
        )
        self.assertEqual(target.get_profile("variable"), profile)

        def history(repository):
            scheduler = RuntimeScheduler(database_path=":memory:", helper_client=None)
            rules = []
            for now in range(0, 300, 5):
                plan = scheduler.prepare_plan(repository=repository, now=now).plan
                rules.append(plan.service_list_rules[0])
            return rules

        moved = history(target)
        self.assertEqual(moved, history(source))
        self.assertEqual({rule.outage_active for rule in moved}, {True, False})
        self.assertGreater(len({rule.upload_impairment for rule in moved}), 2)


if __name__ == "__main__":
    unittest.main()

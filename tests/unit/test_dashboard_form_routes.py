"""The routes behind the schedule, timer, progression and limit forms of the dashboard.

Each table lists the values the forms can send at the edges of what the Pi accepts, with the
answer the form shows. The happy paths are in test_api.py.
"""

import json
import os
import time
import unittest
from dataclasses import replace
from datetime import datetime

from jittersim.api.server import ControlServer
from jittersim.config.models import (
    DataLimit,
    Device,
    DeviceGroup,
    DomainObservation,
    GroupDataLimit,
    Impairment,
    Profile,
    Schedule,
    ServiceQuota,
    TimeQuota,
)
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.timeline import overlapping_schedules

MAX_INT = 2**63 - 1
SECONDS_PER_YEAR = 31_536_000
OBSERVED = "video.example.com"


class FormRouteCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        for identifier in ("slow", "normal"):
            self.repository.save_profile(Profile(identifier, identifier.title(), Impairment(delay_ms=50)))
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_device(Device("bob", "Bob", "192.168.50.11", "normal"))
        self.repository.save_device_group(DeviceGroup("family", "Family", ("alice", "bob")))
        self.repository.save_domain_observations(
            [DomainObservation("192.168.50.10", OBSERVED, 3, 100, 100)], now=100
        )
        self.server = ControlServer(("127.0.0.1", 0), self.repository)

    def tearDown(self) -> None:
        self.server.server_close()
        self.repository.close()

    def post(self, path, payload):
        status, _, body = self.server.write_response_for(path, payload)
        return status, json.loads(body)

    def get(self, path):
        status, _, body = self.server.response_for(path)
        self.assertEqual(status, 200, path)
        return json.loads(body)

    def refused(self, path, payload, reason, status=400):
        answer, body = self.post(path, payload)
        self.assertEqual(answer, status, (payload, body))
        self.assertIn(reason, body["error"], payload)
        return body


class ScheduleRouteTests(FormRouteCase):
    WORK = {"id": "work", "device_id": "alice", "profile_id": "slow", "days_of_week": [0, 1, 2, 3, 4],
            "start_minute": 540, "end_minute": 1020}

    def preview(self, **changes):
        status, body = self.post("/api/schedules/preview", {**self.WORK, "id": "other", **changes})
        self.assertEqual(status, 200, body)
        self.assertIn("first ID alphabetically wins", body["message"])
        return body["overlaps"]

    def test_the_preview_names_the_schedules_a_new_one_overlaps(self):
        self.assertEqual(self.post("/api/schedules", self.WORK)[0], 200)
        self.repository.save_schedule(
            Schedule("a-early", "alice", "slow", (0,), 480, 600)
        )

        self.assertEqual(self.preview(start_minute=600, end_minute=700), ["work"])
        self.assertEqual(self.preview(start_minute=500, end_minute=560), ["a-early", "work"])
        # A window that only touches another one does not overlap it.
        self.assertEqual(self.preview(days_of_week=[0], start_minute=1020, end_minute=1100), [])
        self.assertEqual(self.preview(start_minute=1020, end_minute=1100), [])
        self.assertEqual(self.preview(days_of_week=[5, 6]), [])
        # Other targets and a schedule that is turned off never overlap it.
        self.assertEqual(self.preview(device_id="bob"), [])
        self.assertEqual(self.preview(device_id=None, group_id="family"), [])
        self.assertEqual(self.preview(device_id=None, everyone_else=True), [])
        self.assertEqual(self.preview(enabled=False), [])
        # Saving again under the same id replaces it, so it is not its own overlap.
        self.assertEqual(self.preview(id="work", end_minute=1100), ["a-early"])
        # Previewing saves nothing.
        self.assertEqual({item["id"] for item in self.get("/api/schedules")["schedules"]},
                         {"work", "a-early"})

    def test_a_schedule_that_is_turned_off_does_not_count_as_an_overlap(self):
        self.repository.save_schedule(Schedule("off", "alice", "slow", (0,), 0, 1000, enabled=False))
        self.assertEqual(self.preview(days_of_week=[0]), [])

    def test_a_window_that_crosses_midnight_overlaps_the_next_morning_only(self):
        self.assertEqual(self.post("/api/schedules", {**self.WORK, "id": "night", "days_of_week": [0],
                                                      "start_minute": 1320, "end_minute": 360})[0], 200)
        # Monday 22:00 to Tuesday 06:00.
        self.assertEqual(self.preview(days_of_week=[1], start_minute=300, end_minute=420), ["night"])
        self.assertEqual(self.preview(days_of_week=[0], start_minute=300, end_minute=420), [])
        self.assertEqual(self.preview(days_of_week=[0], start_minute=1380, end_minute=1400), ["night"])
        self.assertEqual(self.preview(days_of_week=[1], start_minute=360, end_minute=420), [])

    def test_sunday_night_runs_into_monday_morning(self):
        self.assertEqual(self.post("/api/schedules", {**self.WORK, "id": "night", "days_of_week": [6],
                                                      "start_minute": 1320, "end_minute": 360})[0], 200)
        self.assertEqual(self.preview(days_of_week=[0], start_minute=300, end_minute=420), ["night"])
        self.assertEqual(self.preview(days_of_week=[6], start_minute=300, end_minute=420), [])

    def test_two_overnight_windows_always_overlap_on_the_same_night(self):
        self.assertEqual(self.post("/api/schedules", {**self.WORK, "id": "night", "days_of_week": [2],
                                                      "start_minute": 1380, "end_minute": 60})[0], 200)
        self.assertEqual(self.preview(days_of_week=[2], start_minute=1200, end_minute=1380), [])
        self.assertEqual(self.preview(days_of_week=[2], start_minute=1439, end_minute=30), ["night"])

    def test_the_engine_function_gives_the_same_answer_as_the_route(self):
        schedules = [Schedule("a", "alice", "slow", (0, 1), 600, 700),
                     Schedule("b", "alice", "slow", (2,), 600, 700)]
        candidate = Schedule("c", "alice", "slow", (1, 2), 650, 660)
        self.assertEqual(overlapping_schedules(candidate, schedules), ["a", "b"])
        self.assertEqual(overlapping_schedules(replace(candidate, id="a"), schedules), ["b"])

    def test_a_schedule_the_form_could_send_is_saved_with_sorted_days(self):
        status, body = self.post("/api/schedules", {**self.WORK, "days_of_week": [4, 0, 0, 2]})
        self.assertEqual(status, 200)
        self.assertEqual(body["schedule"]["days_of_week"], [0, 2, 4])
        for window in ((0, 1), (1439, 0), (0, 1439), (1380, 60)):
            with self.subTest(window=window):
                self.assertEqual(self.post("/api/schedules", {
                    **self.WORK, "start_minute": window[0], "end_minute": window[1]})[0], 200)
        self.assertEqual(len(self.get("/api/schedules")["schedules"]), 1)

    def test_a_schedule_with_values_outside_the_form_is_refused_with_the_reason(self):
        cases = [
            ({"start_minute": 600, "end_minute": 600}, "schedule start and end must be different"),
            ({"start_minute": -1}, "start_minute must be between 0 and 1439"),
            ({"end_minute": 1440}, "end_minute must be between 0 and 1439"),
            ({"start_minute": True}, "start_minute must be an integer"),
            ({"start_minute": "540"}, "start_minute must be an integer"),
            ({"end_minute": 540.5}, "end_minute must be an integer"),
            ({"days_of_week": []}, "days_of_week must contain at least one day"),
            ({"days_of_week": [7]}, "day_of_week must be between 0 and 6"),
            ({"days_of_week": [-1]}, "day_of_week must be between 0 and 6"),
            ({"days_of_week": "0,1"}, "days_of_week must be a list"),
            ({"enabled": "yes"}, "enabled must be a boolean"),
            ({"id": "Work"}, "id must contain 1 to 64 lowercase letters"),
            ({"id": "x" * 65}, "id must contain 1 to 64 lowercase letters"),
            ({"id": ""}, "id must contain 1 to 64 lowercase letters"),
            ({"group_id": "family"}, "exactly one of a device, a group or everyone else"),
            ({"device_id": None}, "exactly one of a device, a group or everyone else"),
            ({"everyone_else": True}, "exactly one of a device, a group or everyone else"),
            ({"device_id": "ghost"}, "device does not exist"),
            ({"device_id": None, "group_id": "ghost"}, "group does not exist"),
            ({"profile_id": "ghost"}, "profile does not exist"),
        ]
        for changes, reason in cases:
            with self.subTest(changes=changes):
                body = self.refused("/api/schedules", {**self.WORK, **changes}, reason)
                self.assertTrue(body["error"].startswith("invalid schedule configuration: "))
        for missing in ("id", "profile_id", "start_minute", "end_minute", "days_of_week"):
            with self.subTest(missing=missing):
                payload = {key: value for key, value in self.WORK.items() if key != missing}
                self.refused("/api/schedules", payload, f"missing {missing}")
        self.assertEqual(self.get("/api/schedules")["schedules"], [])

    def test_the_preview_refuses_what_a_save_refuses(self):
        for changes in ({"start_minute": 5, "end_minute": 5}, {"days_of_week": []}, {"id": "No"}):
            with self.subTest(changes=changes):
                self.refused("/api/schedules/preview", {**self.WORK, **changes},
                             "invalid schedule configuration")

    def test_the_schedule_list_names_the_pi_clock(self):
        listed = self.get("/api/schedules")
        self.assertEqual(listed["schedules"], [])
        moment = datetime.fromisoformat(listed["local_time"])
        self.assertLess(abs(moment.timestamp() - time.time()), 5)
        self.assertTrue(listed["time_zone"])

    def test_deleting_a_schedule_that_is_not_there_is_a_not_found(self):
        self.refused("/api/schedules/delete", {"id": "ghost"}, "schedule does not exist", status=404)
        self.post("/api/schedules", self.WORK)
        self.assertEqual(self.post("/api/schedules/delete", {"id": "work"})[0], 200)
        self.assertEqual(self.get("/api/schedules")["schedules"], [])


class TimerAndProgressionRouteTests(FormRouteCase):
    TIMER = {"device_id": "alice", "profile_id": "slow", "duration_minutes": 15}
    PROGRESSION = {"device_id": "alice", "start_profile_id": "normal", "end_profile_id": "slow",
                   "mode": "time", "duration_minutes": 30}

    def test_a_timer_lasts_from_one_minute_to_one_week(self):
        for minutes in (1, 15, 10_080):
            with self.subTest(minutes=minutes):
                status, body = self.post("/api/timers", {**self.TIMER, "duration_minutes": minutes})
                self.assertEqual(status, 200, body)
                timer = body["timer"]
                self.assertEqual(timer["expires_at"] - timer["started_at"], minutes * 60)
                self.assertFalse(body["live_applied"])
        # A device has one timer at a time: the newest replaces the others.
        listed = self.get("/api/timers")["timers"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["device_name"], "Alice")
        self.assertTrue(0 < listed[0]["remaining_seconds"] <= 604_800)
        self.assertEqual(listed[0]["profile_name"], "Slow")

    def test_a_timer_outside_the_form_is_refused_with_the_reason(self):
        cases = [
            ({"duration_minutes": 0}, "duration_minutes must be between 1 and 10080"),
            ({"duration_minutes": 10_081}, "duration_minutes must be between 1 and 10080"),
            ({"duration_minutes": -5}, "duration_minutes must be between 1 and 10080"),
            ({"duration_minutes": 1.5}, "duration_minutes must be an integer"),
            ({"duration_minutes": "10"}, "duration_minutes must be an integer"),
            ({"duration_minutes": True}, "duration_minutes must be an integer"),
            ({"device_id": "ghost"}, "device does not exist"),
            ({"profile_id": "ghost"}, "profile does not exist"),
            ({"profile_id": "Not Valid"}, "profile_id must contain 1 to 64 lowercase letters"),
        ]
        for changes, reason in cases:
            with self.subTest(changes=changes):
                body = self.refused("/api/timers", {**self.TIMER, **changes}, reason)
                self.assertTrue(body["error"].startswith("invalid timer configuration: "))
        for missing in ("device_id", "profile_id", "duration_minutes"):
            with self.subTest(missing=missing):
                self.refused("/api/timers", {k: v for k, v in self.TIMER.items() if k != missing},
                             f"missing {missing}")
        self.assertEqual(self.get("/api/timers")["timers"], [])

    def test_cancelling_a_timer_needs_a_timer_that_is_running(self):
        self.refused("/api/timers/cancel", {"id": "ghost"}, "does not exist", status=404)
        self.refused("/api/timers/cancel", {"id": ""}, "timer id is invalid")
        self.refused("/api/timers/cancel", {"id": 5}, "timer id is invalid")
        self.refused("/api/timers/cancel", {}, "missing id")
        timer = self.post("/api/timers", self.TIMER)[1]["timer"]
        status, body = self.post("/api/timers/cancel", {"id": timer["id"]})
        self.assertEqual((status, body["timer_id"], body["live_applied"]), (200, timer["id"], False))
        self.assertEqual(self.get("/api/timers")["timers"], [])
        self.refused("/api/timers/cancel", {"id": timer["id"]}, "already inactive", status=404)

    def test_a_progression_lasts_from_one_minute_to_one_week(self):
        for minutes in (1, 30, 10_080):
            with self.subTest(minutes=minutes):
                status, body = self.post(
                    "/api/progressions", {**self.PROGRESSION, "duration_minutes": minutes})
                self.assertEqual(status, 200, body)
                self.assertEqual(body["progression"]["duration_seconds"], minutes * 60)
        listed = self.get("/api/progressions")["progressions"]
        self.assertEqual([item["duration_seconds"] for item in listed], [604_800])
        self.assertEqual(listed[0]["device_name"], "Alice")
        self.assertEqual((listed[0]["start_profile_name"], listed[0]["end_profile_name"]),
                         ("Normal", "Slow"))

    def test_a_timer_and_a_progression_replace_each_other_for_one_device(self):
        self.post("/api/timers", self.TIMER)
        self.post("/api/progressions", self.PROGRESSION)
        self.assertEqual(self.get("/api/timers")["timers"], [])
        self.assertEqual(len(self.get("/api/progressions")["progressions"]), 1)
        self.post("/api/timers", self.TIMER)
        self.assertEqual(len(self.get("/api/timers")["timers"]), 1)
        self.assertEqual(self.get("/api/progressions")["progressions"], [])
        # Another device keeps its own.
        self.post("/api/progressions", {**self.PROGRESSION, "device_id": "bob"})
        self.assertEqual(len(self.get("/api/timers")["timers"]), 1)
        self.assertEqual(len(self.get("/api/progressions")["progressions"]), 1)

    def test_each_progression_mode_needs_its_own_target(self):
        status, body = self.post("/api/progressions", {
            **self.PROGRESSION, "mode": "traffic", "traffic_target_queries": 1_000_000_000})
        self.assertEqual(status, 200, body)
        status, body = self.post("/api/progressions", {
            **self.PROGRESSION, "mode": "bytes", "traffic_target_bytes": 50 * 1048576,
            "website_domains": ["Example.COM", "example.com", "video.example.org"]})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["progression"]["website_domains"], ["example.com", "video.example.org"])
        cases = [
            ({"mode": "traffic"}, "traffic mode requires a positive query target"),
            ({"mode": "traffic", "traffic_target_queries": 0}, "positive query target"),
            ({"mode": "traffic", "traffic_target_queries": 1_000_000_001}, "traffic_target_queries"),
            ({"mode": "traffic", "traffic_target_queries": -1}, "traffic_target_queries"),
            ({"mode": "bytes"}, "bytes mode requires a positive byte target"),
            ({"mode": "bytes", "traffic_target_bytes": -1}, "traffic_target_bytes"),
            ({"mode": "bytes", "traffic_target_bytes": MAX_INT + 1}, "traffic_target_bytes"),
            ({"mode": "speed"}, "progression mode is not supported"),
        ]
        for changes, reason in cases:
            with self.subTest(changes=changes):
                self.refused("/api/progressions", {**self.PROGRESSION, **changes}, reason)

    def test_a_progression_outside_the_form_is_refused_with_the_reason(self):
        cases = [
            ({"duration_minutes": 0}, "duration_minutes must be between 1 and 10080"),
            ({"duration_minutes": 10_081}, "duration_minutes must be between 1 and 10080"),
            ({"duration_minutes": 2.5}, "duration_minutes must be an integer"),
            ({"duration_minutes": False}, "duration_minutes must be an integer"),
            ({"device_id": "ghost"}, "device does not exist"),
            ({"start_profile_id": "ghost"}, "start profile does not exist"),
            ({"end_profile_id": "ghost"}, "end profile does not exist"),
            ({"website_domains": "example.com"}, "website_domains must be a list"),
            ({"website_domains": ["not a domain"]}, "invalid progression configuration"),
            ({"website_domains": [""]}, "invalid progression configuration"),
        ]
        for changes, reason in cases:
            with self.subTest(changes=changes):
                body = self.refused("/api/progressions", {**self.PROGRESSION, **changes}, reason)
                self.assertTrue(body["error"].startswith("invalid progression configuration"))
        self.assertEqual(self.get("/api/progressions")["progressions"], [])

    def test_stopping_a_progression_needs_one_that_is_running(self):
        self.refused("/api/progressions/stop", {"id": "ghost"}, "does not exist", status=404)
        self.refused("/api/progressions/stop", {"id": ""}, "progression id is invalid")
        self.refused("/api/progressions/stop", {}, "missing id")
        progression = self.post("/api/progressions", self.PROGRESSION)[1]["progression"]
        status, body = self.post("/api/progressions/stop", {"id": progression["id"]})
        self.assertEqual((status, body["progression_id"]), (200, progression["id"]))
        self.assertEqual(self.get("/api/progressions")["progressions"], [])


class QuotaRouteTests(FormRouteCase):
    DEVICE_LIMIT = {"device_id": "alice", "profile_id": "slow", "limit_bytes": 1048576}
    GROUP_LIMIT = {"group_id": "family", "profile_id": "slow", "limit_bytes": 1048576}
    SERVICE = {"id": "video", "name": "Video", "device_id": "alice", "profile_id": "slow",
               "domains": [OBSERVED], "limit_bytes": 1048576, "weight_percent": 100}
    TIME = {"id": "video-time", "name": "Video time", "device_id": "alice", "profile_id": "slow",
            "domains": [OBSERVED], "limit_seconds": 3600, "window_duration_seconds": 43_200}
    ROUTES = (
        ("/api/data-limits", DEVICE_LIMIT, "invalid data limit configuration"),
        ("/api/group-data-limits", GROUP_LIMIT, "invalid group data limit configuration"),
        ("/api/service-quotas", SERVICE, "invalid service quota configuration"),
        ("/api/time-quotas", TIME, "invalid time quota configuration"),
    )

    def test_every_limit_accepts_each_reset_mode_the_form_offers(self):
        for path, base, _ in self.ROUTES:
            for reset in (
                {"reset_mode": "manual"},
                {"reset_mode": "interval", "reset_interval_seconds": 60},
                {"reset_mode": "interval", "reset_interval_seconds": SECONDS_PER_YEAR},
                {"reset_mode": "daily", "reset_times": [480, 1230]},
                {"reset_mode": "daily", "reset_times": [1439, 0, 0]},
            ):
                with self.subTest(path=path, reset=reset):
                    status, body = self.post(path, {**base, **reset})
                    self.assertEqual(status, 200, body)
        saved = self.get("/api/data-limits")["data_limits"][0]
        self.assertEqual((saved["reset_mode"], saved["reset_times"], saved["reset_interval_seconds"]),
                         ("daily", [0, 1439], 0))

    def test_every_limit_refuses_the_resets_the_form_would_not_send(self):
        cases = [
            ({"reset_mode": "interval", "reset_interval_seconds": 59},
             "interval resets must be between 60 seconds and 365 days"),
            ({"reset_mode": "interval", "reset_interval_seconds": SECONDS_PER_YEAR + 1},
             "reset_interval_seconds must be between 0 and 31536000"),
            ({"reset_mode": "interval"}, "interval resets must be between 60 seconds and 365 days"),
            ({"reset_mode": "interval", "reset_interval_seconds": 60, "reset_times": [60]},
             "reset_times is only valid for daily resets"),
            ({"reset_mode": "manual", "reset_interval_seconds": 60},
             "reset_interval_seconds is only valid for interval resets"),
            ({"reset_mode": "manual", "reset_times": [60]}, "reset_times is only valid for daily resets"),
            ({"reset_mode": "daily"}, "daily resets require at least one reset time"),
            ({"reset_mode": "daily", "reset_times": [1440]}, "reset_time must be between 0 and 1439"),
            ({"reset_mode": "daily", "reset_times": [-1]}, "reset_time must be between 0 and 1439"),
            ({"reset_mode": "daily", "reset_times": list(range(25))},
             "reset_times must contain no more than 24 times"),
            ({"reset_mode": "daily", "reset_times": "480"}, "reset_times must be a list"),
            ({"reset_mode": "daily", "reset_times": [480], "reset_interval_seconds": 60},
             "reset_interval_seconds is only valid for interval resets"),
            ({"reset_mode": "weekly"}, "reset_mode is not supported"),
            ({"reset_mode": "interval", "reset_interval_seconds": 90.5}, "reset_interval_seconds must be an integer"),
        ]
        for path, base, prefix in self.ROUTES:
            for changes, reason in cases:
                with self.subTest(path=path, changes=changes):
                    body = self.refused(path, {**base, **changes}, reason)
                    self.assertTrue(body["error"].startswith(prefix), body)

    def test_twenty_four_reset_times_are_the_most_a_day_can_have(self):
        times = [index * 60 for index in range(24)]
        for path, base, _ in self.ROUTES:
            with self.subTest(path=path):
                self.assertEqual(self.post(path, {**base, "reset_mode": "daily", "reset_times": times})[0], 200)

    def test_a_byte_limit_is_a_whole_number_of_bytes_up_to_the_largest_the_database_holds(self):
        for path, base in (("/api/data-limits", self.DEVICE_LIMIT), ("/api/group-data-limits", self.GROUP_LIMIT),
                           ("/api/service-quotas", self.SERVICE)):
            for limit in (1, MAX_INT):
                with self.subTest(path=path, limit=limit):
                    self.assertEqual(self.post(path, {**base, "limit_bytes": limit})[0], 200)
            for limit, reason in ((0, "limit_bytes must be between 1 and 9223372036854775807"),
                                  (-1, "limit_bytes must be between 1 and 9223372036854775807"),
                                  (MAX_INT + 1, "limit_bytes must be between 1 and 9223372036854775807"),
                                  (1.5, "limit_bytes must be an integer"),
                                  (1e21, "limit_bytes must be an integer"),
                                  (True, "limit_bytes must be an integer"),
                                  ("1048576", "limit_bytes must be an integer"),
                                  (None, "limit_bytes must be an integer")):
                with self.subTest(path=path, limit=limit):
                    self.refused(path, {**base, "limit_bytes": limit}, reason)
            self.refused(path, {key: value for key, value in base.items() if key != "limit_bytes"},
                         "missing limit_bytes")

    def test_a_limit_names_a_device_or_group_and_a_profile_that_exist(self):
        for path, base, key, label in (
            ("/api/data-limits", self.DEVICE_LIMIT, "device_id", "device does not exist"),
            ("/api/group-data-limits", self.GROUP_LIMIT, "group_id", "device group does not exist"),
            ("/api/service-quotas", self.SERVICE, "device_id", "device does not exist"),
            ("/api/time-quotas", self.TIME, "device_id", "device does not exist"),
        ):
            with self.subTest(path=path):
                self.refused(path, {**base, key: "ghost"}, label)
                self.refused(path, {**base, "profile_id": "ghost"}, "profile does not exist")
                self.refused(path, {**base, key: "Not Valid"}, "must contain 1 to 64 lowercase letters")
                self.refused(path, {k: v for k, v in base.items() if k != key}, f"missing {key}")

    def test_editing_a_limit_keeps_the_usage_and_the_counter_it_started_from(self):
        self.post("/api/data-limits", self.DEVICE_LIMIT)
        self.post("/api/group-data-limits", self.GROUP_LIMIT)
        self.post("/api/service-quotas", self.SERVICE)
        self.post("/api/time-quotas", self.TIME)
        self.repository.save_data_limit(replace(
            self.repository.get_data_limit("alice"), used_bytes=700, counter_bytes=900))
        self.repository.save_group_data_limit(replace(
            self.repository.get_group_data_limit("family"), used_bytes=701, counter_bytes=901))
        self.repository.save_service_quota(replace(
            self.repository.get_service_quota("video"), used_bytes=702, counter_bytes=902))
        self.repository.save_time_quota(replace(
            self.repository.get_time_quota("video-time"), used_seconds=703, counter_bytes=903))

        self.post("/api/data-limits", {**self.DEVICE_LIMIT, "limit_bytes": 2097152})
        self.post("/api/group-data-limits", {**self.GROUP_LIMIT, "limit_bytes": 2097152})
        self.post("/api/service-quotas", {**self.SERVICE, "limit_bytes": 2097152})
        self.post("/api/time-quotas", {**self.TIME, "limit_seconds": 7200})

        device = self.repository.get_data_limit("alice")
        group = self.repository.get_group_data_limit("family")
        service = self.repository.get_service_quota("video")
        timed = self.repository.get_time_quota("video-time")
        self.assertEqual((device.limit_bytes, device.used_bytes, device.counter_bytes), (2097152, 700, 900))
        self.assertEqual((group.limit_bytes, group.used_bytes, group.counter_bytes), (2097152, 701, 901))
        self.assertEqual((service.limit_bytes, service.used_bytes, service.counter_bytes), (2097152, 702, 902))
        self.assertEqual((timed.limit_seconds, timed.used_seconds, timed.counter_bytes), (7200, 703, 903))

    def test_a_service_limit_weight_goes_from_a_tenth_to_ten_times(self):
        for weight in (10, 100, 110, 1000):
            with self.subTest(weight=weight):
                status, body = self.post("/api/service-quotas", {**self.SERVICE, "weight_percent": weight})
                self.assertEqual(status, 200, body)
                self.assertEqual(body["service_quota"]["weight_percent"], weight)
        for weight in (9, 0, -10, 1001, 100.5, True, "100", None):
            with self.subTest(weight=weight):
                self.refused("/api/service-quotas", {**self.SERVICE, "weight_percent": weight},
                             "weight_percent must be")
        # A save that leaves the weight out keeps the saved one. A new limit starts at 1.0.
        self.post("/api/service-quotas", {**self.SERVICE, "weight_percent": 250})
        without = {key: value for key, value in self.SERVICE.items() if key != "weight_percent"}
        self.assertEqual(self.post("/api/service-quotas", without)[1]["service_quota"]["weight_percent"], 250)
        self.assertEqual(self.post("/api/service-quotas", {**without, "id": "fresh"})[1]
                         ["service_quota"]["weight_percent"], 100)

    def test_service_and_time_limits_use_names_seen_for_that_device_only(self):
        for path, base in (("/api/service-quotas", self.SERVICE), ("/api/time-quotas", self.TIME)):
            with self.subTest(path=path):
                self.refused(path, {**base, "domains": ["unseen.example.com"]}, "must come from DNS observations")
                self.refused(path, {**base, "device_id": "bob"}, "must come from DNS observations")
                self.refused(path, {**base, "domains": []}, "requires at least one domain")
                self.refused(path, {**base, "domains": OBSERVED}, "domains must be a list")
                self.refused(path, {**base, "domains": ["not a domain"]}, "")
                self.refused(path, {k: v for k, v in base.items() if k != "domains"}, "missing domains")
                status, body = self.post(path, {**base, "domains": [OBSERVED.upper(), OBSERVED]})
                self.assertEqual(status, 200, body)
                saved = body["service_quota" if "service" in path else "time_quota"]
                self.assertEqual(saved["domains"], [OBSERVED])

    def test_a_saved_name_stays_when_it_is_no_longer_observed(self):
        for path, base in (("/api/service-quotas", self.SERVICE), ("/api/time-quotas", self.TIME)):
            self.assertEqual(self.post(path, base)[0], 200)
        # The observations are kept for 30 days. A limit saved before that must stay editable.
        self.repository.delete_domain_observations(("192.168.50.10",))
        for path, base in (("/api/service-quotas", self.SERVICE), ("/api/time-quotas", self.TIME)):
            with self.subTest(path=path):
                self.assertEqual(self.post(path, {**base, "name": "Renamed"})[0], 200)
                self.refused(path, {**base, "domains": [OBSERVED, "new.example.com"]},
                             "must come from DNS observations")

    def test_a_time_limit_is_from_a_minute_to_a_year_in_a_window_of_up_to_a_week(self):
        for limit, window in ((60, 60), (SECONDS_PER_YEAR, 604_800), (5400, 7200)):
            with self.subTest(limit=limit, window=window):
                self.assertEqual(self.post("/api/time-quotas", {
                    **self.TIME, "limit_seconds": limit, "window_duration_seconds": window})[0], 200)
        cases = [
            ({"limit_seconds": 59}, "limit_seconds must be between 60 and 31536000"),
            ({"limit_seconds": SECONDS_PER_YEAR + 1}, "limit_seconds must be between 60 and 31536000"),
            ({"limit_seconds": 90.5}, "limit_seconds must be an integer"),
            ({"limit_seconds": True}, "limit_seconds must be an integer"),
            ({"window_duration_seconds": 59}, "window_duration_seconds must be between 60 and 604800"),
            ({"window_duration_seconds": 604_801}, "window_duration_seconds must be between 60 and 604800"),
            ({"window_duration_seconds": "60"}, "window_duration_seconds must be an integer"),
            ({"name": ""}, "time quota name must contain between 1 and 100 characters"),
            ({"name": "x" * 101}, "time quota name must contain between 1 and 100 characters"),
        ]
        for changes, reason in cases:
            with self.subTest(changes=changes):
                self.refused("/api/time-quotas", {**self.TIME, **changes}, reason)
        for missing in ("limit_seconds", "window_duration_seconds", "name"):
            with self.subTest(missing=missing):
                payload = {k: v for k, v in self.TIME.items() if k != missing}
                self.refused("/api/time-quotas", payload, f"missing {missing}")
        # Without an id the Pi makes one up.
        without = {k: v for k, v in self.TIME.items() if k != "id"}
        made = self.post("/api/time-quotas", without)[1]["time_quota"]["id"]
        self.assertTrue(made.startswith("time-"))

    def test_a_time_limit_timer_starts_clears_usage_and_stops(self):
        self.post("/api/time-quotas", self.TIME)
        self.repository.save_time_quota(replace(self.repository.get_time_quota("video-time"), used_seconds=500))
        before = int(time.time())
        status, body = self.post("/api/time-quotas/start", {"id": "video-time"})
        self.assertEqual(status, 200, body)
        quota = body["time_quota"]
        self.assertTrue(quota["running"] and quota["window_active"])
        self.assertEqual(quota["used_seconds"], 0)
        self.assertGreaterEqual(quota["started_at"], before)
        self.assertEqual(quota["expires_at"] - quota["started_at"], 43_200)
        self.assertGreater(quota["window_remaining_seconds"], 43_200 - 10)

        # A longer window can be saved while it runs. A shorter one needs the timer stopped.
        self.assertEqual(self.post("/api/time-quotas", {**self.TIME, "window_duration_seconds": 86_400})[0], 200)
        self.refused("/api/time-quotas", {**self.TIME, "window_duration_seconds": 3_600},
                     "stop the timer before shortening its window")
        self.assertEqual(self.get("/api/time-quotas")["time_quotas"][0]["window_duration_seconds"], 86_400)
        status, body = self.post("/api/time-quotas/stop", {"id": "video-time"})
        self.assertEqual((status, body["time_quota"]["running"]), (200, False))
        self.assertEqual(self.post("/api/time-quotas", {**self.TIME, "window_duration_seconds": 3_600})[0], 200)

    def test_the_actions_of_a_limit_that_is_not_there_are_a_not_found(self):
        for path, payload in (
            ("/api/data-limits/reset", {"device_id": "alice"}),
            ("/api/data-limits/delete", {"device_id": "alice"}),
            ("/api/group-data-limits/reset", {"group_id": "family"}),
            ("/api/group-data-limits/delete", {"group_id": "family"}),
            ("/api/service-quotas/reset", {"id": "video"}),
            ("/api/service-quotas/delete", {"id": "video"}),
            ("/api/time-quotas/reset", {"id": "video-time"}),
            ("/api/time-quotas/start", {"id": "video-time"}),
            ("/api/time-quotas/stop", {"id": "video-time"}),
            ("/api/time-quotas/delete", {"id": "video-time"}),
        ):
            with self.subTest(path=path):
                status, body = self.post(path, payload)
                self.assertEqual(status, 404, body)
                self.assertIn("does not exist", body["error"])

    def test_the_actions_of_a_limit_need_its_name(self):
        for path, empty in (("/api/data-limits/reset", "device_id"), ("/api/group-data-limits/reset", "group_id"),
                            ("/api/service-quotas/reset", "id"), ("/api/time-quotas/reset", "id"),
                            ("/api/time-quotas/start", "id"), ("/api/time-quotas/stop", "id")):
            with self.subTest(path=path):
                self.refused(path, {}, f"missing {empty}")
                self.refused(path, {empty: ""}, "is invalid")
                self.refused(path, {empty: 7}, "is invalid")

    def test_resetting_usage_starts_the_counter_again_and_deleting_removes_the_limit(self):
        self.post("/api/data-limits", self.DEVICE_LIMIT)
        self.repository.save_data_limit(replace(self.repository.get_data_limit("alice"), used_bytes=900_000))
        before = int(time.time())
        status, body = self.post("/api/data-limits/reset", {"device_id": "alice"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data_limit"]["used_bytes"], 0)
        self.assertGreaterEqual(body["data_limit"]["last_reset_at"], before)
        self.assertFalse(body["data_limit"]["reached"])
        self.assertEqual(self.post("/api/data-limits/delete", {"device_id": "alice"})[0], 200)
        self.assertEqual(self.get("/api/data-limits")["data_limits"], [])

    def test_a_limit_that_is_used_up_is_reported_as_reached(self):
        self.post("/api/data-limits", self.DEVICE_LIMIT)
        self.post("/api/group-data-limits", self.GROUP_LIMIT)
        self.repository.save_data_limit(replace(self.repository.get_data_limit("alice"), used_bytes=1048576))
        self.repository.save_group_data_limit(
            replace(self.repository.get_group_data_limit("family"), used_bytes=1048575))
        device = self.get("/api/data-limits")["data_limits"][0]
        group = self.get("/api/group-data-limits")["group_data_limits"][0]
        self.assertEqual((device["reached"], device["remaining_bytes"]), (True, 0))
        self.assertEqual((group["reached"], group["remaining_bytes"]), (False, 1))
        self.assertEqual(group["group_name"], "Family")
        self.assertEqual(group["device_ids"], ["alice", "bob"])


@unittest.skipUnless(hasattr(time, "tzset"), "requires a process time zone")
class QuotaRolloverTests(unittest.TestCase):
    """When a limit counts from zero again, in the time zone of the Pi."""

    def setUp(self):
        self.original_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Berlin"
        time.tzset()

    def tearDown(self):
        if self.original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.original_tz
        time.tzset()

    def stamp(self, date, *, fold=0):
        return datetime.fromisoformat(date).replace(fold=fold).timestamp()

    def due(self, quota, now):
        return RuntimeScheduler._quota_reset_is_due(quota, now)

    def limit(self, **changes):
        return DataLimit("alice", "slow", 1000, **changes)

    def daily(self, last_reset, *times):
        return self.limit(reset_mode="daily", reset_times=tuple(times),
                          last_reset_at=int(self.stamp(last_reset)))

    def test_a_manual_limit_never_resets_by_itself(self):
        self.assertFalse(self.due(self.limit(last_reset_at=0), self.stamp("2026-10-09T12:00:00")))

    def test_an_interval_resets_when_the_whole_interval_has_passed(self):
        quota = self.limit(reset_mode="interval", reset_interval_seconds=3600,
                           last_reset_at=int(self.stamp("2026-10-09T10:00:00")))
        self.assertFalse(self.due(quota, self.stamp("2026-10-09T10:59:59")))
        self.assertTrue(self.due(quota, self.stamp("2026-10-09T11:00:00")))
        self.assertTrue(self.due(quota, self.stamp("2026-10-12T11:00:00")))

    def test_a_daily_limit_resets_once_each_time_of_day_has_passed(self):
        quota = self.daily("2026-10-09T08:30:00", 480, 1200)
        self.assertFalse(self.due(quota, self.stamp("2026-10-09T19:59:59")))
        self.assertTrue(self.due(quota, self.stamp("2026-10-09T20:00:00")))
        # The scheduler writes the reset time, and the next one is 08:00 the day after.
        reset = self.daily("2026-10-09T20:00:00", 480, 1200)
        self.assertFalse(self.due(reset, self.stamp("2026-10-10T07:59:59")))
        self.assertTrue(self.due(reset, self.stamp("2026-10-10T08:00:00")))

    def test_a_reset_that_happened_at_the_time_does_not_happen_again(self):
        quota = self.daily("2026-10-09T08:00:00", 480)
        self.assertFalse(self.due(quota, self.stamp("2026-10-09T08:00:30")))
        self.assertFalse(self.due(quota, self.stamp("2026-10-09T23:59:59")))
        self.assertTrue(self.due(self.daily("2026-10-09T07:59:59", 480), self.stamp("2026-10-09T08:00:00")))

    def test_a_daily_limit_that_missed_days_resets_on_the_next_check(self):
        quota = self.daily("2026-10-05T12:00:00", 1410)
        # The last time of day passed yesterday evening and has not come round yet today.
        self.assertTrue(self.due(quota, self.stamp("2026-10-09T00:10:00")))
        self.assertTrue(self.due(quota, self.stamp("2026-10-09T06:00:00")))

    def test_midnight_is_a_valid_reset_time(self):
        quota = self.daily("2026-10-08T23:59:00", 0)
        self.assertFalse(self.due(quota, self.stamp("2026-10-08T23:59:59")))
        self.assertTrue(self.due(quota, self.stamp("2026-10-09T00:00:00")))

    def test_a_reset_time_the_clock_skips_still_happens_once(self):
        # On 29 March the clocks go from 02:00 to 03:00, so 02:30 never shows on the wall clock.
        quota = self.daily("2026-03-28T12:00:00", 150)
        self.assertFalse(self.due(quota, self.stamp("2026-03-29T01:59:00")))
        moments = [self.stamp(f"2026-03-29T{hour:02d}:{minute:02d}:00")
                   for hour in (3, 4) for minute in (0, 30)]
        first = next(index for index, moment in enumerate(moments) if self.due(quota, moment))
        self.assertLess(first, 3)
        reset_at = int(moments[first])
        self.assertFalse(self.due(replace(quota, last_reset_at=reset_at), moments[-1]))
        self.assertFalse(self.due(replace(quota, last_reset_at=reset_at), self.stamp("2026-03-29T23:59:00")))
        self.assertTrue(self.due(replace(quota, last_reset_at=reset_at), self.stamp("2026-03-30T02:30:00")))

    def test_a_reset_time_the_clock_repeats_happens_once(self):
        # On 25 October the clocks go from 03:00 back to 02:00, so 02:30 shows twice.
        quota = self.daily("2026-10-24T12:00:00", 150)
        first = self.stamp("2026-10-25T02:30:00")
        second = self.stamp("2026-10-25T02:30:00", fold=1)
        self.assertEqual(second - first, 3600)
        self.assertFalse(self.due(quota, first - 1))
        self.assertTrue(self.due(quota, first))
        after_first = replace(quota, last_reset_at=int(first) + 5)
        self.assertFalse(self.due(after_first, second))
        self.assertFalse(self.due(after_first, self.stamp("2026-10-25T23:59:00")))
        self.assertTrue(self.due(after_first, self.stamp("2026-10-26T02:30:00")))

    def test_a_reset_runs_for_every_kind_of_limit(self):
        base = {"reset_mode": "interval", "reset_interval_seconds": 60, "last_reset_at": 0}
        quotas = (
            DataLimit("alice", "slow", 1000, **base),
            GroupDataLimit("family", "slow", 1000, **base),
            ServiceQuota("video", "alice", "Video", (OBSERVED,), "slow", 1000, **base),
            TimeQuota("video-time", "alice", "Video time", (OBSERVED,), "slow", 3600, **base),
        )
        for quota in quotas:
            with self.subTest(kind=type(quota).__name__):
                self.assertTrue(self.due(quota, 60))
                self.assertFalse(self.due(quota, 59))


if __name__ == "__main__":
    unittest.main()

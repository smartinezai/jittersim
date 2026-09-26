"""Settings recovery and test reports must keep their own history intact."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jittersim.api.server import ControlServer
from jittersim.config.models import DataLimit, Device, Profile, Schedule, ServiceQuota
from jittersim.config.storage import ConfigRepository
from jittersim.config.workflows import WorkflowStore
from jittersim.engine.timeline import device_timeline, overlapping_schedules


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repository = ConfigRepository(Path(self.directory.name) / "test.db")
        self.repository.save_profile(Profile("normal", "Normal"))
        self.repository.save_profile(Profile("slow", "Slow"))
        self.repository.save_device(Device("phone", "Phone", "192.168.50.10", "normal"))
        self.server = ControlServer(("127.0.0.1", 0), self.repository, admin_token="local-test")
        self.store = WorkflowStore(self.repository)

    def tearDown(self):
        self.server.server_close()
        self.repository.close()
        self.directory.cleanup()

    def post(self, path, payload):
        status, _, body = self.server.write_response_for(path, payload)
        return status, json.loads(body)

    def test_restore_rejects_a_preview_from_before_another_edit(self):
        identifier = self.store.history()[0]["id"]
        _, preview = self.post("/api/history/preview", {"id": identifier})
        self.repository.save_profile(Profile("normal", "Changed elsewhere"))
        status, _ = self.post(
            "/api/history/restore",
            {"id": identifier, "current_version": preview["current_version"]},
        )
        self.assertEqual(status, 409)
        self.assertEqual(self.repository.get_profile("normal").name, "Changed elsewhere")

    def test_restore_keeps_current_usage_and_activity(self):
        self.repository.save_data_limit(
            DataLimit("phone", "slow", 1000, used_bytes=100, counter_bytes=100)
        )
        self.store.checkpoint("Before edit")
        identifier = self.store.history()[0]["id"]
        self.repository.save_data_limit(
            DataLimit("phone", "slow", 2000, used_bytes=800, counter_bytes=800)
        )
        self.repository.record_activity(kind="apply", message="Earlier event", now=100)
        _, preview = self.post("/api/history/preview", {"id": identifier})
        status, body = self.post(
            "/api/history/restore",
            {"id": identifier, "current_version": preview["current_version"]},
        )
        self.assertEqual(status, 200, body)
        limit = self.repository.get_data_limit("phone")
        self.assertEqual(
            (limit.limit_bytes, limit.used_bytes, limit.counter_bytes), (1000, 800, 800)
        )
        self.assertIn(
            "Earlier event", [event["message"] for event in self.repository.list_activity()]
        )

    def test_history_retention_and_snapshot_immutability(self):
        for index in range(25):
            self.repository.save_profile(Profile("normal", "Normal " + str(index)))
            self.store.checkpoint("Edit")
        self.assertEqual(len(self.store.history()), 20)
        old = self.store.history_item(self.store.history()[-1]["id"])
        self.assertEqual(old["profiles"][0]["name"], "Normal 5")

    def test_restoring_a_reassigned_limit_does_not_copy_another_devices_usage(self):
        self.repository.save_device(Device("other", "Other phone", "192.168.50.11", "normal"))
        quota = ServiceQuota("video", "phone", "Video", ("example.com",), "slow", 1000)
        self.repository.save_service_quota(quota)
        self.store.checkpoint("Original owner")
        identifier = self.store.history()[0]["id"]
        self.repository.save_service_quota(replace(quota, device_id="other", used_bytes=900))
        _, preview = self.post("/api/history/preview", {"id": identifier})
        status, body = self.post(
            "/api/history/restore",
            {"id": identifier, "current_version": preview["current_version"]},
        )
        self.assertEqual(status, 200, body)
        restored = self.repository.get_service_quota("video")
        self.assertEqual((restored.device_id, restored.used_bytes), ("phone", 0))

    def test_oversized_history_does_not_break_saving_or_startup(self):
        from jittersim.config.workflows import encoded

        original = self.store.history()
        with patch(
            "jittersim.config.workflows.encoded", side_effect=lambda value: encoded(value, 10)
        ):
            status, body = self.post("/api/profiles", {"id": "large", "name": "Large settings"})
            self.assertEqual(status, 200, body)
            self.assertIsNone(self.store.checkpoint("Startup"))
        self.assertIsNotNone(self.repository.get_profile("large"))
        self.assertEqual(self.store.history(), original)
        self.assertIn("1 MB", self.store.history_warning())
        self.store.checkpoint("Smaller settings")
        self.assertIsNone(self.store.history_warning())

    def test_session_keeps_its_settings_and_events_after_log_clear(self):
        session = self.store.start_session({"name": "Commute", "target": "device:phone"})
        self.repository.save_profile(Profile("normal", "Changed"))
        self.repository.record_activities(
            tuple(("timer", "Event " + str(i)) for i in range(505)), now=123
        )
        self.repository.clear_activity()
        record = self.store.session(session["id"])
        self.assertEqual(record["settings"]["profiles"][0]["name"], "Normal")
        self.assertEqual(len(record["events"]), 500)
        self.assertTrue(record["events_truncated"])
        self.store.edit_session(
            {"id": session["id"], "revision": record["revision"], "finish": True}
        )
        self.repository.record_activity(kind="reset", message="After finish", now=125)
        self.assertNotIn(
            "After finish",
            [item["message"] for item in self.store.session(session["id"])["events"]],
        )

    def test_sessions_reject_parallel_recording_stale_edits_and_invalid_files(self):
        session = self.store.start_session({"name": "First", "target": "device:phone"})
        status, _ = self.post(
            "/api/test-sessions/start", {"name": "Second", "target": "device:phone"}
        )
        self.assertEqual(status, 400)
        status, _ = self.post(
            "/api/test-sessions/save",
            {"id": session["id"], "revision": 0, "measurements": [{"label": "fake"}]},
        )
        self.assertEqual(status, 400)
        self.store.edit_session({"id": session["id"], "revision": 0, "notes": "First editor"})
        status, _ = self.post(
            "/api/test-sessions/save",
            {"id": session["id"], "revision": 0, "notes": "Second editor"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(self.store.session(session["id"])["notes"], "First editor")

    def test_session_measurements_and_settings_captures_have_enforced_limits(self):
        session = self.store.start_session({"name": "Limits", "target": "device:phone"})
        measurement = {
            "format": "jittersim-measurement",
            "version": 1,
            "label": "Normal",
            "target": "1.1.1.1",
            "summary": {"sent": 2, "loss_percent": 0},
            "samples": [{"rtt_ms": 2}],
        }
        for bad in (
            {**measurement, "samples": [{"rtt_ms": 2}] * 2001},
            {**measurement, "summary": {"sent": 2, "loss_percent": 101}},
            {**measurement, "samples": [{"rtt_ms": -1}]},
        ):
            with self.assertRaises(ValueError):
                self.store.edit_session({"id": session["id"], "revision": 0, "measurements": [bad]})
        self.store.edit_session({"id": session["id"], "revision": 0, "measurements": [measurement]})
        for index in range(25):
            self.repository.save_profile(Profile("normal", "Normal " + str(index)))
            self.store.checkpoint("Profile edited")
        record = self.store.session(session["id"])
        self.assertEqual(len(record["captures"]), 20)
        self.assertTrue(record["captures_truncated"])
        self.assertEqual(record["measurements"], [measurement])

    def test_workspace_preview_immediately_notices_a_reached_data_limit(self):
        self.repository.save_data_limit(DataLimit("phone", "slow", 1000))
        initial = json.loads(self.server.response_for("/api/device-workspace/phone")[2])
        self.assertEqual(initial["timeline"]["changes"][0]["reason"], "Device profile")
        self.repository.record_data_usage("phone", 1200)
        reached = json.loads(self.server.response_for("/api/device-workspace/phone")[2])
        self.assertIn("Data limit reached", reached["timeline"]["changes"][0]["reason"])

    def test_activity_links_keep_device_identity_when_names_are_the_same(self):
        from jittersim.engine.activity import Source, TargetState, activity_records

        own = Source("profile", "normal", profile="Normal")
        slow = Source("timer", "timer", profile="Slow")
        records = activity_records(
            {"phone": TargetState("Phone", own)}, {"phone": TargetState("Phone", slow)}
        )
        self.repository.record_activities(
            [(kind, message) for kind, message, _ in records],
            now=1,
            device_ids=[ids for _, _, ids in records],
        )
        self.repository.save_device(Device("other", "Phone", "192.168.50.11", "normal"))
        self.assertEqual(self.repository.list_activity()[0]["device_ids"], ["phone"])

    def test_timeline_matches_schedule_order_and_is_read_only(self):
        from datetime import datetime

        now = datetime(2026, 9, 21, 10).timestamp()
        first = Schedule("a", "phone", "normal", (0,), 9 * 60, 11 * 60)
        later = Schedule("b", "phone", "slow", (0,), 9 * 60, 12 * 60)
        self.repository.save_schedule(first)
        self.repository.save_schedule(later)
        version = self.repository.get_runtime_state().configuration_version
        result = device_timeline(self.repository, self.repository.get_device("phone"), now=now)
        self.assertEqual(
            [item["reason"] for item in result["changes"]],
            ["Schedule a", "Schedule b", "Device profile"],
        )
        self.assertEqual(result["changes"][0]["overridden_schedules"], ["b"])
        self.assertEqual(self.repository.get_runtime_state().configuration_version, version)

    def test_overlap_includes_sunday_overnight_but_not_touching_boundaries(self):
        night = Schedule("night", "phone", "slow", (6,), 23 * 60, 60)
        monday = Schedule("monday", "phone", "normal", (0,), 30, 90)
        self.assertEqual(overlapping_schedules(night, [monday]), ["monday"])
        self.assertEqual(overlapping_schedules(night, [replace(monday, start_minute=60)]), [])

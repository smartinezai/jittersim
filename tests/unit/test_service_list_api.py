import json
import unittest

from jittersim.api.server import (
    DASHBOARD_HTML,
    PUBLIC_READ_PATHS,
    WRITE_PATHS,
    ControlServer,
)
from jittersim.config.models import Device, Impairment, Profile, ServiceList
from jittersim.config.storage import ConfigRepository
from jittersim.networking.helper import HelperClientError


class Helper:
    def __init__(self, fail=False) -> None:
        self.fail = fail
        self.applied_plan = None

    def apply(self, *, plan):
        if self.fail:
            raise HelperClientError("simulated helper failure")
        self.applied_plan = plan
        return {"ok": True, "action": "apply", "commands_run": 3}

    def connected_device_snapshot(self):
        return {"devices": [], "association_status_available": False}


class ServiceListApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(
            Profile(id="choppy", name="Choppy", upload=Impairment(delay_ms=300))
        )
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "choppy"))
        self.server = ControlServer(("127.0.0.1", 0), self.repository)
        self.addCleanup(self.server.server_close)
        self.server.helper_client = Helper()
        self.body = {
            "id": "streaming",
            "name": "Streaming",
            "profile_id": "choppy",
            "services": ["netflix"],
            "hostnames": ["extra.example"],
            "enabled": True,
        }

    def post(self, path="/api/service-lists", body=None):
        status, _, payload = self.server.write_response_for(
            path, self.body if body is None else body
        )
        return status, json.loads(payload)

    def test_the_routes_are_private(self) -> None:
        self.assertIn("/api/service-lists", WRITE_PATHS)
        self.assertIn("/api/service-lists/delete", WRITE_PATHS)
        self.assertNotIn("/api/service-lists", PUBLIC_READ_PATHS)

    def test_the_listing_offers_the_catalogue(self) -> None:
        status, _, body = self.server.response_for("/api/service-lists")

        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["service_lists"], [])
        netflix = next(item for item in payload["catalogue"] if item["key"] == "netflix")
        self.assertEqual(netflix["name"], "Netflix")
        self.assertIn("nflxvideo.net", netflix["domains"])

    def test_saving_a_list_applies_it_and_shows_it_in_the_listing(self) -> None:
        status, payload = self.post()

        self.assertEqual(status, 200)
        self.assertTrue(payload["live_applied"])
        self.assertEqual(payload["service_list"]["profile_name"], "Choppy")
        plan = self.server.helper_client.applied_plan
        (rule,) = plan.service_list_rules
        self.assertEqual((rule.list_id, rule.mark), ("streaming", 2))
        self.assertIn("nflxvideo.net", rule.domains)
        self.assertIn("extra.example", rule.domains)
        self.assertEqual(rule.upload_impairment, Impairment(delay_ms=300))
        _, _, body = self.server.response_for("/api/service-lists")
        (listed,) = json.loads(body)["service_lists"]
        self.assertEqual(listed["services"], ["netflix"])
        self.assertEqual(listed["hostnames"], ["extra.example"])
        self.assertEqual(listed["domains"][-1], "extra.example")

    def test_a_list_needs_no_saved_device_and_works_with_the_general_profile(self) -> None:
        repository = ConfigRepository(":memory:")
        self.addCleanup(repository.close)
        repository.save_profile(
            Profile(id="choppy", name="Choppy", upload=Impairment(delay_ms=300))
        )
        server = ControlServer(("127.0.0.1", 0), repository)
        self.addCleanup(server.server_close)
        server.helper_client = Helper()

        status, _, _ = server.write_response_for("/api/service-lists", self.body)
        self.assertEqual(status, 200)
        plan = server.helper_client.applied_plan
        self.assertEqual((plan.mark_rules, plan.service_list_rules[0].mark), ((), 1))

        repository.save_general_profile("choppy")
        status, _, _ = server.write_response_for(
            "/api/service-lists", {**self.body, "name": "Streaming again"}
        )
        self.assertEqual(status, 200)
        plan = server.helper_client.applied_plan
        self.assertIsNotNone(plan.general_profile)
        self.assertEqual(plan.service_list_rules[0].mark, 2)

    def test_invalid_lists_are_rejected_with_a_reason_and_nothing_is_saved(self) -> None:
        for change, text in (
            ({"services": ["not-a-service"]}, "catalogue"),
            ({"services": [], "hostnames": []}, "at least one"),
            ({"hostnames": ["203.0.113.9"]}, "hostnames"),
            ({"profile_id": "missing"}, "profile"),
            ({"id": "Bad Id"}, "id"),
            ({"enabled": "yes"}, "boolean"),
            ({"services": "netflix"}, "lists"),
        ):
            with self.subTest(change=change):
                status, payload = self.post(body={**self.body, **change})
                self.assertEqual(status, 400)
                self.assertIn(text, payload["error"])
        status, payload = self.post(body={"id": "x"})
        self.assertEqual(status, 400)
        self.assertEqual(self.repository.list_service_lists(), ())

    def test_a_hostname_cannot_belong_to_two_enabled_lists(self) -> None:
        self.post()

        status, payload = self.post(
            body={
                **self.body,
                "id": "work",
                "name": "Work",
                "services": [],
                "hostnames": ["www.netflix.com"],
            }
        )

        self.assertEqual(status, 409)
        self.assertIn("overlap", payload["error"])
        self.assertEqual([item.id for item in self.repository.list_service_lists()], ["streaming"])

    def test_a_disabled_list_may_overlap_until_it_is_enabled(self) -> None:
        self.post()

        status, _ = self.post(body={**self.body, "id": "spare", "name": "Spare", "enabled": False})
        self.assertEqual(status, 200)
        status, payload = self.post(body={**self.body, "id": "spare", "name": "Spare"})
        self.assertEqual(status, 409)
        self.assertFalse(self.repository.get_service_list("spare").enabled)

    def test_too_many_traffic_classes_are_reported_plainly(self) -> None:
        for number in range(14):
            self.repository.save_device(
                Device(f"d{number:02d}", f"D{number}", f"192.168.50.{20 + number}", "choppy")
            )

        status, payload = self.post()

        self.assertEqual(status, 409)
        self.assertIn("15", payload["error"])
        self.assertEqual(self.repository.list_service_lists(), ())

    def test_a_helper_failure_keeps_the_saved_list_and_says_it_was_not_applied(self) -> None:
        self.server.helper_client = Helper(fail=True)

        status, payload = self.post()

        self.assertNotEqual(status, 200)
        self.assertFalse(payload["live_applied"])
        self.assertEqual(len(self.repository.list_service_lists()), 1)

    def test_without_a_helper_the_list_is_saved_for_later(self) -> None:
        self.server.helper_client = None

        status, payload = self.post()

        self.assertEqual(status, 200)
        self.assertFalse(payload["live_applied"])

    def test_deleting_a_list_applies_the_removal(self) -> None:
        self.post()

        status, payload = self.post("/api/service-lists/delete", {"id": "streaming"})

        self.assertEqual(status, 200)
        self.assertEqual(payload["deleted_id"], "streaming")
        self.assertEqual(self.server.helper_client.applied_plan.service_list_rules, ())
        self.assertEqual(self.post("/api/service-lists/delete", {"id": "streaming"})[0], 404)
        self.assertEqual(self.post("/api/service-lists/delete", {"id": 5})[0], 400)

    def test_the_dashboard_has_a_service_list_panel_wired_to_the_routes(self) -> None:
        for fragment in (
            'id="service-list-form"',
            'id="service-list-catalogue"',
            'id="service-list-hostnames"',
            'id="service-list-profile"',
            "/api/service-lists/delete",
            "loadServiceLists()",
            '"service-list-profile"',
        ):
            self.assertIn(fragment, DASHBOARD_HTML)

    def test_existing_saved_lists_survive_a_reopened_listing(self) -> None:
        self.repository.save_service_list(
            ServiceList(id="saved", name="Saved", profile_id="choppy", services=("claude",))
        )

        _, _, body = self.server.response_for("/api/service-lists")

        self.assertEqual(json.loads(body)["service_lists"][0]["domains"][0], "claude.ai")


if __name__ == "__main__":
    unittest.main()

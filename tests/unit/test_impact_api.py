import json
import unittest
from email.message import Message

from jittersim.api.server import ControlRequestHandler, ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile, ServiceList
from jittersim.config.storage import ConfigRepository
from jittersim.networking.helper import HelperClientError
from tests.unit.test_api import FakeHelper

RESET_MESSAGE = "Network is reset. Apply the configuration to start impact checks."
NO_HELPER_MESSAGE = "Impact needs the Pi helper."
OUT_OF_DATE_MESSAGE = "The networking helper is out of date. Update it to see the impact check."
UNREADABLE_MESSAGE = "Impact could not be read right now."


def queue(device_id, direction="upload", **changes):
    entry = {
        "device_id": device_id,
        "direction": direction,
        "interface": "eth0" if direction == "upload" else "wlan0",
        "handle": "11:",
        "kind": "netem",
        "impairment": {
            "delay_ms": 0,
            "jitter_ms": 0,
            "loss_percent": 20,
            "bandwidth_kbit": None,
            "reorder_percent": 0,
            "duplicate_percent": 0,
            "corrupt_percent": 0,
        },
        "packets": 10,
        "bytes": 1500,
        "drops": 3,
        "overlimits": 0,
        "requeues": 0,
        "backlog_bytes": 0,
        "qlen": 0,
    }
    entry.update(changes)
    return entry


class ImpactHelper(FakeHelper):
    def __init__(self, queues):
        super().__init__()
        self.queues = queues

    def queue_stats(self):
        return self.queues


class ImpactApiTests(unittest.TestCase):
    PHONE = "dc:1e:d5:3e:97:4c"

    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(Profile(id="lossy", name="Lossy", upload=Impairment(loss_percent=20)))
        self.repository.save_device(
            Device("lab", "Lab phone", "192.168.50.10", "lossy", mac_address=self.PHONE)
        )
        self.repository.save_connected_device_label(
            mac_address=self.PHONE, display_name="Sam's phone", device_type="phone"
        )
        self.server = ControlServer(("127.0.0.1", 0), self.repository)
        self.addCleanup(self.server.server_close)
        self.repository.update_runtime_state(
            observed_state="applied", last_action="apply", last_result="success", last_error=None
        )

    def impact(self):
        status, content_type, body = self.server.response_for("/api/impact")
        self.assertEqual((status, content_type), (200, "application/json"))
        return json.loads(body)

    def test_names_every_queue_and_lists_protected_devices(self) -> None:
        self.repository.save_service_list(
            ServiceList(id="streaming", name="Streaming", profile_id="lossy", hostnames=("a.example",))
        )
        self.repository.save_unaffected_device(
            UnaffectedDevice("02:00:00:00:00:09", "Dad's laptop", 1)
        )
        self.repository.save_unaffected_device(UnaffectedDevice("02:00:00:00:00:08", "", 2))
        self.server.helper_client = ImpactHelper(
            [
                queue("lab", "upload"),
                queue("lab", "download"),
                queue("general-profile"),
                queue("quota:video-cap"),
                queue("assignment:browse-1"),
                queue("servicelist:streaming"),
                queue("servicelist:gone"),
                queue("mystery"),
            ]
        )

        response = self.impact()

        self.assertTrue(response["available"])
        self.assertIsNone(response["message"])
        self.assertIsInstance(response["sampled_at"], int)
        self.assertEqual(
            [(item["device_id"], item["name"], item["kind_label"]) for item in response["queues"]],
            [
                ("lab", "Sam's phone", "Device"),
                ("lab", "Sam's phone", "Device"),
                ("general-profile", "General profile", "General profile"),
                ("quota:video-cap", "Service quota video-cap", "Service quota"),
                ("assignment:browse-1", "Browser service profile browse-1", "Browser service profile"),
                ("servicelist:streaming", "Service list Streaming", "Service list"),
                ("servicelist:gone", "Service list gone", "Service list"),
                ("mystery", "mystery", "Queue"),
            ],
        )
        self.assertEqual(response["queues"][0]["direction"], "upload")
        self.assertEqual(response["queues"][1]["direction"], "download")
        self.assertEqual(response["queues"][0]["drops"], 3)
        self.assertEqual(response["protected"], ["02:00:00:00:00:08", "Dad's laptop"])

    def test_an_unnamed_device_shows_its_address_so_two_never_look_alike(self) -> None:
        self.repository.save_device(
            Device("a", "Client 192.168.50.20", "192.168.50.20", "lossy", mac_address="02:00:00:00:00:21")
        )
        self.repository.save_device(
            Device("b", "Client 192.168.50.21", "192.168.50.21", "lossy", mac_address="02:00:00:00:00:22")
        )
        self.server.helper_client = ImpactHelper([queue("a"), queue("b")])

        names = [item["name"] for item in self.impact()["queues"]]

        self.assertEqual(names, ["Unknown device (192.168.50.20)", "Unknown device (192.168.50.21)"])

    def test_a_protected_device_never_shows_a_queue_even_from_a_stale_plan(self) -> None:
        self.repository.save_unaffected_device(UnaffectedDevice(self.PHONE, "Sam's phone", 1))
        self.server.helper_client = ImpactHelper([queue("lab", "upload"), queue("general-profile")])

        response = self.impact()

        self.assertEqual([item["device_id"] for item in response["queues"]], ["general-profile"])
        self.assertEqual(response["protected"], ["Sam's phone"])

    def test_reset_state_answers_unavailable_without_reading_the_helper(self) -> None:
        class ResetHelper(FakeHelper):
            def queue_stats(self):
                raise AssertionError("A reset network must not be read")

        self.server.helper_client = ResetHelper()
        self.repository.update_runtime_state(
            observed_state="reset", last_action="reset", last_result="success", last_error=None
        )

        response = self.impact()

        self.assertFalse(response["available"])
        self.assertEqual(response["message"], RESET_MESSAGE)
        self.assertEqual((response["queues"], response["protected"]), ([], []))

    def test_no_helper_answers_unavailable(self) -> None:
        response = self.impact()

        self.assertFalse(response["available"])
        self.assertEqual(response["message"], NO_HELPER_MESSAGE)
        self.assertEqual(response["queues"], [])

    def test_an_old_helper_answers_that_it_needs_updating(self) -> None:
        without_method = FakeHelper()
        method_is_none = ImpactHelper([])
        method_is_none.queue_stats = None
        for name, helper in (("no method", without_method), ("method is None", method_is_none)):
            with self.subTest(name):
                self.server.helper_client = helper

                response = self.impact()

                self.assertFalse(response["available"])
                self.assertEqual(response["message"], OUT_OF_DATE_MESSAGE)
                self.assertEqual((response["queues"], response["protected"]), ([], []))

    def test_a_helper_that_does_not_know_the_action_answers_that_it_needs_updating(self) -> None:
        class UnknownActionHelper(FakeHelper):
            def queue_stats(self):
                raise HelperClientError("unknown action")

        self.server.helper_client = UnknownActionHelper()

        response = self.impact()

        self.assertFalse(response["available"])
        self.assertEqual(response["message"], OUT_OF_DATE_MESSAGE)

    def test_a_failing_helper_answers_could_not_read_and_leaks_nothing(self) -> None:
        class FailingHelper(FakeHelper):
            def queue_stats(self):
                raise HelperClientError("private socket detail /run/jittersim.sock")

        self.server.helper_client = FailingHelper()

        response = self.impact()

        self.assertFalse(response["available"])
        self.assertEqual(response["message"], UNREADABLE_MESSAGE)
        self.assertNotIn("socket", json.dumps(response))

    def test_the_endpoint_needs_the_admin_token_like_diagnostics(self) -> None:
        self.server.admin_token = "admin-token"
        self.server.helper_client = ImpactHelper([queue("lab")])

        def get(path, authorization):
            handler = object.__new__(ControlRequestHandler)
            handler.server = self.server
            handler.client_address = ("127.0.0.1", 45000)
            handler.path = path
            handler.headers = Message()
            if authorization:
                handler.headers["Authorization"] = authorization
            response = {}
            handler._send_response = lambda **values: response.update(values)
            handler.do_GET()
            return response

        for path in ("/api/impact", "/api/diagnostics"):
            with self.subTest(path=path):
                self.assertEqual(get(path, None)["status"], 401)
                self.assertEqual(get(path, "Bearer wrong-token")["status"], 401)
        authorized = get("/api/impact", "Bearer admin-token")
        self.assertEqual(authorized["status"], 200)
        self.assertEqual(json.loads(authorized["body"])["queues"][0]["device_id"], "lab")

    def test_the_name_helper_resolves_each_queue_kind(self) -> None:
        from jittersim.api.server import _impact_queue_name

        device = Device("lab", "Lab phone", "192.168.50.10", "lossy", mac_address=self.PHONE)
        labels = {self.PHONE: {"display_name": "Sam's phone", "device_type": "phone"}}

        def name(queue_id):
            return _impact_queue_name(
                queue_id,
                devices_by_id={"lab": device},
                labels_by_mac=labels,
                list_names={"streaming": "Streaming"},
            )

        self.assertEqual(name("lab"), ("Sam's phone", "Device"))
        self.assertEqual(name("general-profile"), ("General profile", "General profile"))
        self.assertEqual(name("quota:q1"), ("Service quota q1", "Service quota"))
        self.assertEqual(name("assignment:a1"), ("Browser service profile a1", "Browser service profile"))
        self.assertEqual(name("servicelist:streaming"), ("Service list Streaming", "Service list"))
        self.assertEqual(name("servicelist:other"), ("Service list other", "Service list"))
        self.assertEqual(name("something-else"), ("something-else", "Queue"))


if __name__ == "__main__":
    unittest.main()

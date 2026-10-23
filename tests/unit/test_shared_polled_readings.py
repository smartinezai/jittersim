"""Open dashboard tabs share one reading of traffic, uplink and health (soak audit 4, 5, 6)."""

import json
import threading
from unittest.mock import patch

import pytest

from jittersim.api import server as server_module
from jittersim.api import shared_reading
from jittersim.api.server import ControlServer
from jittersim.config.storage import ConfigRepository
from jittersim.networking.helper import HelperClientError
from tests.unit.test_api import FakeHelper
from tests.unit.test_shared_reading import Clock


class CountingHelper(FakeHelper):
    """The fixture helper that counts the slow reads and can fail or block them."""

    def __init__(self):
        super().__init__()
        self.reads = {"traffic": 0, "uplink": 0, "ping": 0}
        self.fail = set()
        self.uplink_name = "Wired connection 1"
        self.block = None  # an Event that traffic_counters waits for
        self.inside = threading.Event()

    def _read(self, name):
        self.reads[name] += 1
        if name in self.fail:
            raise HelperClientError("simulated " + name + " failure")

    def traffic_counters(self):
        self._read("traffic")
        self.inside.set()
        if self.block is not None:
            assert self.block.wait(10)
        return super().traffic_counters()

    def uplink_info(self):
        self._read("uplink")
        return {**super().uplink_info(), "connection_name": self.uplink_name}

    def ping(self):
        self._read("ping")
        return super().ping()


@pytest.fixture
def clock():
    fake = Clock()
    with patch.object(shared_reading, "time", fake):
        yield fake


@pytest.fixture
def served():
    helper = CountingHelper()
    with ConfigRepository(":memory:") as repository, ControlServer(
        ("127.0.0.1", 0), repository, admin_token="token", helper_client=helper
    ) as server:
        yield server, helper


def read(server, path):
    status, _, body = server.response_for(path)
    return status, json.loads(body)


def health(server, max_age):
    status, _, body = server.health_response(max_age=max_age)
    assert status == 200
    return json.loads(body)


# Traffic (audit 4)

def test_traffic_reads_the_helper_once_for_requests_within_a_second(served, clock):
    server, helper = served
    first = read(server, "/api/traffic")[1]
    clock.now += 0.9
    second = read(server, "/api/traffic")[1]
    assert helper.reads["traffic"] == 1
    # The time of the reading is when the helper was read, so a rate between two readings is right.
    assert second["sampled_at"] == first["sampled_at"]
    assert second["devices"] == first["devices"]


def test_traffic_reads_again_after_a_second(served, clock):
    server, helper = served
    read(server, "/api/traffic")
    clock.now += 1.0
    read(server, "/api/traffic")
    assert helper.reads["traffic"] == 2


def test_a_failed_traffic_reading_is_not_kept(served, clock):
    server, helper = served
    helper.fail.add("traffic")
    assert read(server, "/api/traffic")[0] == 502
    helper.fail.clear()
    status, answer = read(server, "/api/traffic")
    assert status == 200 and answer["available"] is True
    assert helper.reads["traffic"] == 2


def test_requests_that_arrive_during_a_traffic_reading_share_it(served):
    server, helper = served
    helper.block = threading.Event()
    answers = []
    threads = [threading.Thread(target=lambda: answers.append(read(server, "/api/traffic")))
               for _ in range(4)]
    threads[0].start()
    assert helper.inside.wait(10)
    for thread in threads[1:]:
        thread.start()
    threading.Event().wait(0.5)
    helper.block.set()
    for thread in threads:
        thread.join(10)
    assert helper.reads["traffic"] == 1
    assert [status for status, _ in answers] == [200] * 4


@pytest.mark.parametrize("path", ["/api/reset", "/api/normal-access"])
def test_after_reset_and_panic_traffic_never_shows_the_kept_counters(served, path):
    server, helper = served
    assert read(server, "/api/traffic")[1]["available"] is True
    server.write_response_for(path, {})
    status, answer = read(server, "/api/traffic")
    assert status == 200 and answer["available"] is False
    assert helper.reads["traffic"] == 1


# Uplink (audit 6)

def test_uplink_is_read_once_for_fifteen_seconds(served, clock):
    server, helper = served
    for _ in range(4):
        assert read(server, "/api/uplink")[0] == 200
        clock.now += 3.0
    assert helper.reads["uplink"] == 1
    clock.now += 3.1
    read(server, "/api/uplink")
    assert helper.reads["uplink"] == 2


def test_a_failed_uplink_reading_is_not_kept(served, clock):
    server, helper = served
    helper.fail.add("uplink")
    assert read(server, "/api/uplink")[0] == 502
    helper.fail.clear()
    status, answer = read(server, "/api/uplink")
    assert status == 200 and answer["available"] is True
    assert helper.reads["uplink"] == 2


# Every write drops the readings (audit 4, 5 and 6). One change of the answer proves it.
@pytest.mark.parametrize("path", [
    "/api/apply", "/api/reset", "/api/normal-access", "/api/profiles", "/api/devices",
    "/api/access-point/reset-name", "/api/router/wifi", "/api/router/sync-ap",
    "/api/gateway-persistence", "/api/general-profile", "/api/import",
])
def test_a_write_drops_the_uplink_reading(served, clock, path):
    server, helper = served
    assert read(server, "/api/uplink")[1]["uplink"]["connection_name"] == "Wired connection 1"
    helper.uplink_name = "Wired connection 2"
    # The write may be refused or fail. A refused write changes nothing but it still drops.
    server.write_response_for(path, {})
    assert read(server, "/api/uplink")[1]["uplink"]["connection_name"] == "Wired connection 2"


@pytest.mark.parametrize("path", ["/api/apply", "/api/profiles"])
def test_a_write_drops_the_traffic_reading(served, clock, path):
    server, helper = served
    read(server, "/api/traffic")
    server.write_response_for(path, {})
    read(server, "/api/traffic")
    assert helper.reads["traffic"] == 2


def test_a_write_drops_the_health_reading(served, clock):
    server, helper = served
    health(server, 10.0)
    server.write_response_for("/api/profiles", {})
    health(server, 10.0)
    assert helper.reads["ping"] == 2


def test_a_reading_taken_while_a_write_runs_is_not_kept(served, clock):
    server, helper = served
    during = {}

    def write_that_reads(self, path, payload, control_intent, before_replace):
        during["answer"] = read(server, "/api/uplink")[1]["uplink"]["connection_name"]
        helper.uplink_name = "Wired connection 2"
        return 200, "application/json", b"{}"

    with patch.dict(server_module.WRITE_ROUTES, {"/api/profiles": write_that_reads}):
        server.write_response_for("/api/profiles", {})
    assert during["answer"] == "Wired connection 1"
    assert read(server, "/api/uplink")[1]["uplink"]["connection_name"] == "Wired connection 2"


def test_dropping_never_waits_for_a_reading_in_progress(served):
    server, helper = served
    helper.block = threading.Event()
    reader = threading.Thread(target=lambda: read(server, "/api/traffic"), daemon=True)
    reader.start()
    assert helper.inside.wait(10)
    dropper = threading.Thread(target=server.drop_shared_readings, daemon=True)
    dropper.start()
    dropper.join(2)
    assert not dropper.is_alive()
    # Panic passes through the same drop and then goes on without the reading.
    panic = threading.Thread(target=server.write_response_for, args=("/api/normal-access", {}),
                             daemon=True)
    panic.start()
    panic.join(10)
    assert not panic.is_alive()
    helper.block.set()
    reader.join(10)
    # What the blocked read returned after the drop is not kept: the next request reads again.
    server.write_response_for("/api/apply", {})  # starts the network again after Panic
    server.drop_shared_readings()
    read(server, "/api/traffic")
    assert helper.reads["traffic"] == 2


# Health (audit 5)

def test_health_polls_with_the_token_share_one_ping_for_two_seconds(served, clock):
    server, helper = served
    for _ in range(3):
        assert health(server, server_module.DASHBOARD_HEALTH_REUSE_SECONDS)["helper_reachable"]
        clock.now += 0.6
    assert helper.reads["ping"] == 1
    clock.now += 0.3
    health(server, server_module.DASHBOARD_HEALTH_REUSE_SECONDS)
    assert helper.reads["ping"] == 2


def test_a_public_check_takes_only_an_answer_up_to_a_second_old(served, clock):
    server, helper = served
    health(server, server_module.DASHBOARD_HEALTH_REUSE_SECONDS)
    clock.now += 1.5
    health(server, server_module.DASHBOARD_HEALTH_REUSE_SECONDS)
    assert helper.reads["ping"] == 1
    health(server, server_module.PUBLIC_HEALTH_REUSE_SECONDS)
    assert helper.reads["ping"] == 2


def test_an_unreachable_helper_is_the_answer_and_is_kept_for_the_same_time(served, clock):
    # The ping never raises: no answer is the reading ("degraded"). Keeping it for the window
    # is what stops a device on the test Wi-Fi from turning an outage into a stream of calls.
    server, helper = served
    helper.fail.add("ping")
    assert health(server, 2.0)["status"] == "degraded"
    helper.fail.clear()
    assert health(server, 2.0)["status"] == "degraded"
    clock.now += 2.0
    assert health(server, 2.0)["status"] == "ok"
    assert helper.reads["ping"] == 2


def test_the_route_answers_with_the_dashboard_time(served, clock):
    server, _ = served
    with patch.object(server, "health_response", wraps=server.health_response) as wrapped:
        read(server, "/api/health")
    wrapped.assert_called_once_with(max_age=server_module.DASHBOARD_HEALTH_REUSE_SECONDS)

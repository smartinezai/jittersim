"""Workspace timeline selections must survive concurrent cache eviction."""

import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

from jittersim.api.workflows import read_workflow


class Repository:
    def get_device(self, device_id):
        return SimpleNamespace(id=device_id)

    def get_runtime_state(self):
        return SimpleNamespace(configuration_version=1)

    def list_data_limits(self):
        return []

    def list_group_data_limits(self):
        return []

    def list_device_groups(self):
        return []


def workspace_server(cache):
    server = SimpleNamespace(
        repository=Repository(), _workspace_timelines=cache
    )
    server.repository._connection = None
    payload = {
        field: []
        for field in (
            "timers",
            "progressions",
            "data_limits",
            "service_quotas",
            "time_quotas",
            "group_data_limits",
            "runs",
        )
    }
    server.response_for = lambda path: (200, "application/json", json.dumps(payload).encode())
    return server


def run_workspace(server, failures):
    try:
        status, _, body = read_workflow(server, "/api/device-workspace/device-a")
        assert status == 200
        return json.loads(body)["timeline"]
    except BaseException as error:
        failures.append(error)
        return None


def test_cached_timeline_selection_survives_another_request_clearing_cache():
    key = ("device-a", 4, 1, (), ())
    selected = {"cached": True}
    ready_to_clear = threading.Event()
    cleared = threading.Event()

    class PausingCache(dict):
        def get(self, requested_key, default=None):
            value = super().get(requested_key, default)
            if requested_key == key and threading.current_thread().name == "selected":
                ready_to_clear.set()
                assert cleared.wait(3)
            return value

    cache = PausingCache({key: selected})
    cache.update({("other", index): {} for index in range(15)})
    server = workspace_server(cache)
    failures = []
    results = {}

    with patch("jittersim.api.workflows.time.time", return_value=120), patch(
        "jittersim.api.workflows.device_timeline", return_value={"timeline": "computed"}
    ):
        first = threading.Thread(
            name="selected",
            target=lambda: results.setdefault("first", run_workspace(server, failures)),
        )
        first.start()
        assert ready_to_clear.wait(3)
        cache.clear()
        results["second"] = run_workspace(server, failures)
        cleared.set()
        first.join(3)

    assert not first.is_alive()
    assert failures == []
    assert results == {"first": selected, "second": {"timeline": "computed"}}


def test_computed_timeline_selection_survives_cache_eviction_and_is_reused():
    key = ("device-a", 4, 1, (), ())
    cache = {("other", index): {} for index in range(16)}
    selected = {"computed": True}
    ready_to_clear = threading.Event()
    cleared = threading.Event()
    failures = []
    results = {}

    class PausingCache(dict):
        def __setitem__(self, item_key, value):
            super().__setitem__(item_key, value)
            if item_key == key and threading.current_thread().name == "selected":
                ready_to_clear.set()
                assert cleared.wait(3)

    cache = PausingCache(cache)
    server = workspace_server(cache)

    with patch("jittersim.api.workflows.time.time", return_value=120), patch(
        "jittersim.api.workflows.device_timeline", return_value=selected
    ) as build_timeline:
        first = threading.Thread(
            name="selected",
            target=lambda: results.setdefault("first", run_workspace(server, failures)),
        )
        first.start()
        assert ready_to_clear.wait(3)
        cache.clear()
        cleared.set()
        first.join(3)
        assert not first.is_alive()

        assert run_workspace(server, failures) == selected
        assert run_workspace(server, failures) == selected

    assert failures == []
    assert results["first"] == selected
    assert build_timeline.call_count == 2

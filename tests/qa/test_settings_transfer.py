"""Settings export and import through the dashboard API: limits, duplicates, old files, round trips (QA, 2026-10-09)."""

import copy
import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.qa.api_support import NASTY_NUMBERS, NASTY_STRINGS, call, judge
from tests.qa.dashboard_support import TOKEN, running_dashboard


@pytest.fixture(scope="module")
def dashboard():
    with running_dashboard(observations=True) as running:
        yield running


def post(dashboard, route, body):
    status, headers, data, first = call(dashboard, "POST", route, body)
    assert not judge(dashboard, "POST", route, status, headers, data, first), (route, status, data[:200])
    return status, json.loads(data)


def export(dashboard):
    status, _, body = dashboard.request("GET", "/api/export")
    assert status == 200
    return json.loads(body)


def without_time(document):
    document = copy.deepcopy(document)
    document.pop("exported_at", None)
    return document


@pytest.fixture(scope="module")
def rich_settings(dashboard):
    """Every kind of saved setting the export can carry, built through the API."""

    slow = next(profile for profile in json.loads(dashboard.request("GET", "/api/profiles")[2])["profiles"]
                if profile["id"] == "slow")
    for route, body in (
        ("/api/profiles", {**slow, "id": "burst", "name": "Burst", "download": {**slow["download"], "loss_percent": 5}}),
        ("/api/schedules", {"id": "work", "device_id": "alice", "profile_id": "slow", "days_of_week": [0, 4],
                            "start_minute": 540, "end_minute": 1020}),
        ("/api/data-limits", {"device_id": "alice", "profile_id": "slow", "limit_bytes": 1000}),
        ("/api/groups", {"id": "family", "name": "Family", "device_ids": ["alice"]}),
        ("/api/group-data-limits", {"group_id": "family", "profile_id": "slow", "limit_bytes": 1000}),
        ("/api/service-assignments", {"id": "video", "device_id": "alice", "name": "Video",
                                      "domains": ["youtube.com"], "profile_id": "slow"}),
        ("/api/service-lists", {"id": "social", "name": "Social", "profile_id": "slow", "services": [],
                                "hostnames": ["example.org"]}),
        ("/api/policies", {"mac": {"mode": "denylist", "denylist": ["aa:bb:cc:dd:ee:ff"]}, "websites": {}}),
        ("/api/everyone-else", {"profile_id": "slow"}),
        ("/api/unaffected-devices", {"mac_address": "02:00:00:00:00:05", "display_name": "TV"}),
    ):
        status, data = post(dashboard, route, body)
        assert status == 200, (route, data)
    document = export(dashboard)
    for section in ("profiles", "devices", "groups", "schedules", "scenarios", "data_limits", "group_data_limits",
                    "service_assignments", "service_lists"):
        assert document[section], f"the fixture settings lack {section}"
    return document


def test_export_and_import_round_trip_without_change(dashboard, rich_settings):
    first = without_time(rich_settings)
    for _ in range(3):
        status, data = post(dashboard, "/api/import", rich_settings)
        assert status == 200, data
        assert without_time(export(dashboard)) == first


def test_the_export_holds_no_secret(dashboard, rich_settings):
    text = json.dumps(rich_settings).lower()
    assert TOKEN not in text
    for word in ("password", "passphrase", "secret", "psk", "bearer"):
        assert word not in text, word


def test_a_refused_import_changes_nothing(dashboard, rich_settings):
    before = without_time(export(dashboard))
    broken = copy.deepcopy(rich_settings)
    broken["devices"][0]["profile_id"] = "no-such-profile"
    broken["data_limits"][0]["limit_bytes"] = -1
    status, data = post(dashboard, "/api/import", broken)
    assert status == 400 and "error" in data
    assert without_time(export(dashboard)) == before


@pytest.mark.parametrize("version,stripped", [
    (1, ("scenarios", "everyone_else", "general_profile_id", "service_lists", "ad_blocking_allowances",
         "ad_blocking_devices", "group_data_limits", "service_quotas", "time_quotas", "service_assignments")),
    (2, ("service_lists", "general_profile_id", "ad_blocking_allowances")),
])
def test_a_file_from_an_older_version_is_read_and_keeps_what_it_does_not_carry(dashboard, rich_settings, version, stripped):
    post(dashboard, "/api/import", rich_settings)
    old = copy.deepcopy(rich_settings)
    for key in stripped:
        old.pop(key, None)
    old["version"] = version
    status, data = post(dashboard, "/api/import", old)
    assert status == 200, data
    after = export(dashboard)
    for key in ("service_lists", "general_profile_id", "ad_blocking_allowances"):
        assert after[key] == rich_settings[key], key
    assert after["profiles"] == rich_settings["profiles"]


def mutated(document, section, field, value, index=0, sub=None):
    changed = copy.deepcopy(document)
    target = changed[section][index] if isinstance(changed[section], list) else changed[section]
    if sub is not None:
        target = target[sub]
    target[field] = value
    return changed


@pytest.mark.parametrize("sub,field,value,ok", [
    ("download", "delay_ms", 0, True), ("download", "delay_ms", 60_000, True), ("download", "delay_ms", 60_001, False),
    ("download", "delay_ms", -1, False), ("download", "delay_ms", 1.5, False), ("download", "delay_ms", 100.0, False),
    ("download", "delay_ms", "100", False), ("download", "delay_ms", True, False), ("download", "delay_ms", None, False),
    ("download", "delay_ms", 10**20, False), ("download", "jitter_ms", 60_001, False),
    ("download", "loss_percent", 100, True), ("download", "loss_percent", 101, False), ("download", "loss_percent", -1, False),
    ("download", "bandwidth_kbit", 0, False), ("download", "bandwidth_kbit", 1, True),
    ("download", "bandwidth_kbit", 1_000_000, True), ("download", "bandwidth_kbit", 1_000_001, False),
    ("download", "bandwidth_kbit", None, True), ("download", "loss_burst_packets", 100, True),
    ("download", "loss_burst_packets", 101, False), ("upload", "duplicate_percent", 101, False),
    (None, "random_seed", 0, True), (None, "random_seed", 2**63 - 1, True), (None, "random_seed", 2**63, False),
    (None, "random_seed", -1, False), (None, "reroll_interval_seconds", 604_800, True),
    (None, "reroll_interval_seconds", 604_801, False), (None, "outage_probability", 101, False),
    (None, "name", "n" * 100, True), (None, "name", "n" * 101, False), (None, "name", "   ", False),
    (None, "name", "", False), (None, "name", 5, False), (None, "name", None, False),
])
def test_profile_limits_are_enforced_on_import(dashboard, rich_settings, sub, field, value, ok):
    post(dashboard, "/api/import", rich_settings)
    index = next(i for i, profile in enumerate(rich_settings["profiles"]) if profile["id"] == "burst")
    status, data = post(dashboard, "/api/import", mutated(rich_settings, "profiles", field, value, index, sub))
    assert (status == 200) == ok, (field, value, status, data)
    if not ok:
        assert status == 400 and "error" in data
    else:
        # Whatever was accepted must also be applicable.
        assert post(dashboard, "/api/apply", {})[0] == 200


def test_a_profile_id_of_64_characters_is_the_longest(dashboard, rich_settings):
    document = copy.deepcopy(rich_settings)
    index = next(i for i, profile in enumerate(document["profiles"]) if profile["id"] == "burst")
    document["profiles"][index]["id"] = "a" * 64
    status, data = post(dashboard, "/api/import", document)
    assert status == 200, data
    document["profiles"][index]["id"] = "a" * 65
    assert post(dashboard, "/api/import", document)[0] == 400


@pytest.mark.parametrize("section,field,message", [
    ("profiles", "id", "appears twice"), ("devices", "id", "appears twice"), ("groups", "id", "appears twice"),
    ("schedules", "id", "appears twice"), ("scenarios", "id", "appears twice"),
    ("service_lists", "id", "appears twice"), ("service_assignments", "id", "appears twice"),
    ("data_limits", "device_id", "appears twice"), ("group_data_limits", "group_id", "appears twice"),
])
def test_a_duplicate_identifier_is_refused_by_name(dashboard, rich_settings, section, field, message):
    document = copy.deepcopy(rich_settings)
    document[section].append(copy.deepcopy(document[section][0]))
    status, data = post(dashboard, "/api/import", document)
    assert status == 400 and message in data["error"], data
    assert document[section][0][field] in data["error"] or section == "service_lists"


def test_two_devices_cannot_share_an_address_or_a_group(dashboard, rich_settings):
    document = copy.deepcopy(rich_settings)
    twin = {**document["devices"][0], "id": "twin", "mac_address": "02:00:00:00:00:77"}
    document["devices"].append(twin)
    status, data = post(dashboard, "/api/import", document)
    assert status == 400 and "appears twice" in data["error"]


def test_the_capacity_limit_is_a_refusal_not_a_failure(dashboard, rich_settings):
    document = copy.deepcopy(rich_settings)
    document["devices"] = [
        {"id": f"d{i}", "name": f"d{i}", "ipv4_address": f"192.168.50.{i + 10}", "profile_id": "slow"}
        for i in range(16)
    ]
    for section in ("groups", "schedules", "data_limits", "group_data_limits", "service_assignments", "scenarios"):
        document[section] = []
    document["policies"]["site_blocks"] = []
    status, data = post(dashboard, "/api/import", document)
    assert status == 400 and "at most 15" in data["error"], data


@pytest.mark.parametrize("section", ["profiles", "devices", "groups", "schedules", "scenarios", "data_limits"])
@pytest.mark.parametrize("bad", [None, {}, "x", 1, [1], [None], [[]], ["x"]])
def test_a_section_of_the_wrong_shape_is_a_400(dashboard, rich_settings, section, bad):
    document = copy.deepcopy(rich_settings)
    document[section] = bad
    status, data = post(dashboard, "/api/import", document)
    assert status == 400 and "error" in data


@pytest.mark.parametrize("change", [
    {"format": "other"}, {"version": 0}, {"version": 4}, {"version": "3"}, {"version": 3.0}, {"version": True},
    {"version": None}, {"format": None}, {"persist_profiles_after_gateway_outage": "yes"},
    {"general_profile_id": "no-such-profile"}, {"general_profile_id": 5}, {"everyone_else": []},
    {"everyone_else": {"profile_id": 5}}, {"policies": []}, {"policies": {"mac": []}},
])
def test_wrong_header_or_top_level_fields_are_refused(dashboard, rich_settings, change):
    status, data = post(dashboard, "/api/import", {**copy.deepcopy(rich_settings), **change})
    assert status == 400 and "error" in data


def test_unknown_fields_are_ignored_and_do_not_come_back(dashboard, rich_settings):
    document = copy.deepcopy(rich_settings)
    document["surprise"] = {"a": 1}
    document["profiles"][0]["surprise"] = 1
    document["devices"][0]["surprise"] = 1
    status, data = post(dashboard, "/api/import", document)
    assert status == 200, data
    assert "surprise" not in json.dumps(export(dashboard))


@pytest.mark.xfail(strict=True, reason=(
    "an import that carries an unknown field in policies.options is refused with the bare sentence "
    "'invalid settings import' and one with an unknown field in a site block is refused with Python's own "
    "text 'SiteBlock.__init__() got an unexpected keyword argument' (server.py _settings_from_payload "
    "passes **value straight to the dataclass). The person cannot tell which field to fix"))
def test_a_refused_import_says_what_is_wrong_in_plain_words(dashboard, rich_settings):
    messages = []
    options = copy.deepcopy(rich_settings)
    options["policies"]["options"]["surprise"] = 1
    messages.append(post(dashboard, "/api/import", options)[1]["error"])
    blocks = copy.deepcopy(rich_settings)
    blocks["policies"]["site_blocks"] = [{"surprise": 1}]
    messages.append(post(dashboard, "/api/import", blocks)[1]["error"])
    for message in messages:
        assert message != "invalid settings import", messages
        assert "__init__" not in message and "keyword argument" not in message, messages


def test_a_body_over_one_megabyte_is_refused_in_json(dashboard, rich_settings):
    import socket
    document = copy.deepcopy(rich_settings)
    document["profiles"][0]["name"] = "n" * 1_100_000
    body = json.dumps(document).encode()
    head = (f"POST /api/import HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode()
    with socket.create_connection(("127.0.0.1", dashboard.port), timeout=15) as connection:
        try:
            connection.sendall(head + body)
        except OSError:
            pass  # the server may close the socket as soon as it has said no
        reply = b""
        try:
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                reply += chunk
        except OSError:
            pass
    assert b" 400 " in reply.split(b"\r\n")[0], reply[:80]
    assert b"request body is too large" in reply
    assert dashboard.request("GET", "/api/health")[0] == 200


def walk(value, path=()):
    """Every position in a JSON document: the path to it and its value."""

    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk(item, path + (index,))


HOSTILE = st.one_of(
    st.none(), st.booleans(), st.sampled_from(NASTY_NUMBERS), st.integers(-2**64, 2**64), st.sampled_from(NASTY_STRINGS),
    st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=40), st.just([]), st.just({}),
    st.lists(st.sampled_from(NASTY_STRINGS), max_size=3),
)


@settings(max_examples=400, deadline=None, database=None, derandomize=True,
          suppress_health_check=list(HealthCheck))
@given(data=st.data())
def test_any_single_changed_value_is_refused_or_gives_settings_that_apply_and_round_trip(dashboard, rich_settings, data):
    document = copy.deepcopy(rich_settings)
    positions = [path for path, _ in walk(document) if path]
    path = data.draw(st.sampled_from(positions))
    target = document
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = data.draw(HOSTILE)

    first = len(dashboard.logs.records)
    status, headers, body = dashboard.request("POST", "/api/import", body=document)
    problems = judge(dashboard, "POST", "/api/import", status, headers, body, first)
    assert not problems, (path, problems)
    assert status in (200, 400), (path, status)
    if status == 200:
        # What the import accepted must be a document it would accept again, and it must apply.
        again = export(dashboard)
        again_status, _, _ = dashboard.request("POST", "/api/import", body=again)
        assert again_status == 200, (path, "the export of accepted settings was refused on import")
        assert without_time(export(dashboard)) == without_time(again), path
        assert dashboard.request("POST", "/api/apply", body={})[0] == 200

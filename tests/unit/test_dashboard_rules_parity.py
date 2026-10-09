"""The terminal's copies of the dashboard rules give the same answers as the served page."""

import itertools
import time

import pytest

from jittersim import dashboard_rules as rules
from jittersim.api.server import _labelled_domain_payloads
from jittersim.config.models import DomainObservation
from jittersim.remote_client import js_number, js_round
from tests.dashboard_script import NODE, function_source, run_node

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required to run the dashboard")


def js(value):
    import json

    return json.dumps(value)


# --- section: status


def client(name, mac, **fields):
    return {"display_name": name, "mac_address": mac, "unaffected": False,
            "association_verified": True, "assigned_device_id": None,
            "assigned_profile_id": None, "group_profile_id": None, **fields}


PROFILES = [
    {"id": "slow", "name": "Slow"}, {"id": "lossy", "name": ""}, {"id": "cap", "name": "Cap"}
]
CLIENT_SETS = [
    [],
    [client("Phone", "02:00:00:00:00:01")],
    [
        client("Tablet", "02:00:00:00:00:02", assigned_device_id="t", assigned_profile_id="slow",
               group_profile_id="cap"),
        client("", "02:00:00:00:00:03", assigned_device_id="m", assigned_profile_id="lossy"),
        client(None, None, assigned_device_id="x", assigned_profile_id="gone"),
        client("Shielded", "02:00:00:00:00:04", unaffected=True, assigned_device_id="s",
               assigned_profile_id="slow"),
        client("Unverified", "02:00:00:00:00:05", association_verified=False),
    ],
    [client(f"Device {index}", f"02:00:00:00:01:{index:02d}", assigned_device_id=str(index),
            assigned_profile_id="slow") for index in range(7)],
]
GENERALS = [
    None,
    {"profile": None, "active": False},
    {"profile": {"id": "slow", "name": "Shared slow"}, "active": False},
    {"profile": {"id": "slow", "name": "Shared slow"}, "active": True},
    {"profile": {"id": "cap", "name": ""}, "active": True},
]
LISTS = [[], [{"name": "Games", "enabled": True}, {"name": "Off", "enabled": False},
              {"name": "Video", "enabled": True}]]


def sidebar_inputs():
    inputs = []
    for state, running, last_action, general, clients, available, lists, device_only in (
        itertools.product(
            ("applied", "unknown", "reset", "applying", "resetting", "error", None),
            (True, False),
            ("apply", "reset"),
            GENERALS,
            CLIENT_SETS,
            (True, False),
            LISTS,
            (None, [], ["m", "9"]),
        )
    ):
        inputs.append({
            "adminTokenVerified": True,
            "loaded": {name: True for name in rules.SIDEBAR_INPUTS},
            "deviceOnly": device_only,
            "runtimeState": state, "running": running, "lastAction": last_action,
            "generalState": general, "profiles": PROFILES, "clients": clients,
            "clientsAvailable": available, "serviceLists": lists,
        })
    inputs.append({"adminTokenVerified": True, "loaded": {"runtime": True}})
    inputs.append({"adminTokenVerified": False, "loaded": {}})
    return inputs


def test_sidebar_status_matches_the_page_for_every_state_and_device_mix():
    inputs = sidebar_inputs()
    expected = run_node([function_source("sidebarStatus")],
                        f"{js(inputs)}.map((input) => sidebarStatus(input))")
    assert len(expected) == len(inputs) > 1000
    for data, page in zip(inputs, expected):
        assert rules.sidebar_status(data) == page, data


def test_the_terminal_lists_every_affected_device_and_the_page_the_first_five():
    data = sidebar_inputs()[0]
    data.update({"clients": CLIENT_SETS[3], "runtimeState": "applied", "running": True,
                 "generalState": None, "clientsAvailable": True})
    assert rules.sidebar_status(data)["devices"]["more"] == 2
    full = rules.sidebar_status(data, limit=None)["devices"]
    assert (len(full["items"]), full["more"], full["summary"]) == (7, 0, "7 devices affected")


def test_runtime_status_text_matches_the_page():
    cases = [
        [state, running, {"lastAction": action, "helperConfigured": helper, "deviceOnly": only}]
        for state, running, action, helper, only in itertools.product(
            ("applied", "unknown", "reset", "applying", "resetting", "error", "other", None),
            (True, False), ("apply", "reset", None), (True, False, None), (None, [], ["air"]),
        )
    ]
    page = run_node([function_source("runtimeStatusText")],
                    f"{js(cases)}.map((c) => runtimeStatusText(c[0], c[1], c[2]))")
    for (state, running, context), expected in zip(cases, page):
        assert rules.runtime_status_text(state, running, context) == expected


# --- end: status


# --- section: profiles


def test_the_general_badge_matches_the_saved_profiles_list():
    render = function_source("renderSavedProfiles")
    start = render.index("const general = generalProfileState")
    body = render[start : render.index(';', render.index('"General · saved"')) + 1]
    badge = ("function badgeFor(generalProfileState) {\n" + body + "\nreturn general ? "
             "generalBadge : null; }")
    states = [
        None,
        {"profile": None, "active": False},
        {"profile": {"id": "a"}, "active": True, "state": {"observed_state": "applied"}},
        {"profile": {"id": "a"}, "active": False, "state": {"observed_state": "reset"}},
        {"profile": {"id": "a"}, "active": False, "state": {"observed_state": "applied"}},
        {"profile": {"id": "a"}, "active": False},
    ]
    page = run_node([badge], f"{js(states)}.map((s) => badgeFor(s))")
    for general, expected in zip(states, page):
        if expected is not None:
            assert rules.general_badge(general) == expected


def direction(**fields):
    base = {"delay_ms": 0, "jitter_ms": 0, "loss_percent": 0, "bandwidth_kbit": None,
            "reorder_percent": 0, "duplicate_percent": 0, "corrupt_percent": 0}
    return {**base, **fields}


DIRECTIONS = [
    direction(),
    direction(delay_ms=50, jitter_ms=10, loss_percent=2, bandwidth_kbit=5000),
    direction(jitter_ms=15),
    direction(delay_ms=300),
    direction(loss_percent=2.5, bandwidth_kbit=999),
    direction(bandwidth_kbit=1000),
    direction(bandwidth_kbit=1555),
    direction(bandwidth_kbit=1005),
    direction(bandwidth_kbit=15005),
    direction(bandwidth_kbit=0),
    direction(reorder_percent=25, duplicate_percent=5, corrupt_percent=0.5),
    direction(delay_ms=0.5, loss_percent=0.1),
]


def test_profile_summaries_match_the_page():
    profiles = []
    for upload, download in itertools.product(DIRECTIONS, DIRECTIONS[:6]):
        profiles.append({"upload": upload, "download": download, "outage_probability": 0,
                         "website_mode": "block", "website_domains": []})
    for outage, mode, sites in itertools.product((0, 5, 12.5), ("block", "allow_only"),
                                                 ([], ["a"], ["a", "b", "c"])):
        profiles.append({"upload": DIRECTIONS[1], "download": DIRECTIONS[1],
                         "outage_probability": outage, "website_mode": mode,
                         "website_domains": sites})
    page = run_node(
        [function_source("profileSummary"), function_source("profileDirectionSummary")],
        f"{js(profiles)}.map((p) => profileSummary(p))",
    )
    for profile, expected in zip(profiles, page):
        assert rules.profile_summary(profile) == expected


# --- end: profiles


# --- section: traffic


def labelled_rows():
    """Observed names labelled by the server code the page receives them from."""

    now = int(time.time())
    rows = [
        ("mmx-ds.cdn.whatsapp.net", 12, 0), ("a1051.dscg4.akamai.net", 3, 0),
        ("tags-eu.tiqcdn.com", 2, 399), ("api.header.immobilienscout24.de", 4, 400),
        ("content.header.immobilienscout24.de", 4, 400), ("tracking.immobilienscout24.de", 6, 400),
        ("logs.dradis.netflix.com", 3, 1200), ("ipv4-c179-fra002-ix.1.oca.nflxvideo.net", 40, 1200),
        ("config.extension.grammarly.com", 5, 2000), ("cdn.somesmallsite.io", 1, 400),
        ("very-long-unknown-name.sub.example-sample-site.co.uk", 1, 900),
        ("10000.mt.eu.iot.dreame.tech", 7, 0), ("gateway.icloud.com", 5, 0),
        ("www.googleapis.com", 9, 5), ("fonts.gstatic.com", 9, 6), ("Zeta.example.com", 2, 7),
        ("alpha.example.com", 2, 8),
    ]
    observations = [
        DomainObservation("192.168.50.124", domain.lower(), count, now - offset, now - offset)
        for domain, count, offset in rows
    ]
    blocked = {"tracking.immobilienscout24.de", "logs.dradis.netflix.com",
               "ipv4-c179-fra002-ix.1.oca.nflxvideo.net"}
    return _labelled_domain_payloads(observations, blocked)


TRAFFIC_FUNCTIONS = [
    "groupObservations", "isRiskyGroupKind", "domainHint", "groupBlockedText",
    "groupCategoriesUniform", "rowLabelParts", "domainRowParts", "domainSearchUrl",
    "queryCountText", "groupNamesText", "deviceDisplayName",
]


def test_traffic_groups_labels_and_markers_match_the_page():
    rows = labelled_rows()
    sets = [rows, rows[:3], [row for row in rows if row["globally_blocked"]], [],
            [{"domain": "plain.example", "query_count": "3"},
             {"domain": "b.example", "query_count": 1}]]
    program = [function_source(name) for name in TRAFFIC_FUNCTIONS]
    page = run_node(program, f"""{js(sets)}.map((domains) =>
      groupObservations(domains).map((g) => ({{
        key: g.key, name: g.name, kind: g.kind, single: g.single, totalQueries: g.totalQueries,
        names: g.names.map((o) => o.domain), blocked: groupBlockedText(g),
        uniform: groupCategoriesUniform(g),
        rows: g.names.map((o) => {{
          const parts = domainRowParts(o);
          const shown = rowLabelParts(parts, g.single ? "" : g.name,
                                      g.single ? false : groupCategoriesUniform(g));
          return [parts.labelName, parts.category, parts.kind, shown.showName,
                  shown.showCategory, domainHint(o).text, domainHint(o).searchable,
                  queryCountText(o.query_count)];
        }})
      }})))""")
    for domains, expected in zip(sets, page):
        mine = []
        for group in rules.group_observations(domains):
            uniform = rules.group_categories_uniform(group)
            mine.append({
                "key": group["key"], "name": group["name"], "kind": group["kind"],
                "single": group["single"], "totalQueries": group["totalQueries"],
                "names": [item["domain"] for item in group["names"]],
                "blocked": rules.group_blocked_text(group), "uniform": uniform,
                "rows": [
                    [
                        parts["labelName"], parts["category"], parts["kind"], parts["showName"],
                        parts["showCategory"], rules.domain_hint(item)["text"],
                        rules.domain_hint(item)["searchable"],
                        rules.query_count_text(item.get("query_count")),
                    ]
                    for item in group["names"]
                    for parts in [rules.row_label_parts(
                        item, "" if group["single"] else group["name"],
                        False if group["single"] else uniform)]
                ],
            })
        assert mine == expected


def test_device_names_match_the_page():
    devices = [{"display_name": "A", "name": "B"}, {"name": "B"}, {"assigned_device_name": "C"},
               {"ipv4_address": "192.168.50.5"}, {}, {"display_name": "", "name": None}]
    page = run_node([function_source("deviceDisplayName")],
                    f"{js(devices)}.map((d) => deviceDisplayName(d))")
    assert [rules.device_display_name(device) for device in devices] == page


# --- end: traffic


# --- section: numbers

NUMBERS = [0, 1, 2, -2, 2.5, 4.7, 21.6, 0.1, 0.2 + 0.1, 1 / 3, 1e21, 1e20, 123456789.125, 1e-7,
           0.000001, 0.0000015, 5e-324, 1.7976931348623157e308, -0.5, 99.95, 1799.9999999]


def test_numbers_print_like_the_page():
    page = run_node([], f"{js(NUMBERS)}.map((n) => String(n))")
    assert [js_number(number) for number in NUMBERS] == page


def test_rounding_matches_math_round():
    values = [0.5, 1.5, 2.5, -0.5, -1.5, -2.5, 0.49999999999999994, 1799.5, 239.99, 4.45 * 10,
              1e15 + 0.5]
    page = run_node([], f"{js(values)}.map((n) => Math.round(n))")
    assert [js_round(value) for value in values] == page


# --- end: numbers

"""Direct checks of the dashboard display rules (mutation testing, 2026-10-18).

`test_dashboard_rules_parity.py` compares these functions with the page script on a fixed set
of inputs. It needs Node.js and it does not reach every branch: a mutation run left rows such
as the group sort order, the risky-kind rule and the singular words unchecked. These tests
state the expected answer for those inputs without Node.js.
"""

import math

from jittersim import dashboard_rules as rules


def observation(domain, queries=1, **fields):
    return {"domain": domain, "query_count": queries, **fields}


# Numbers


def test_a_query_count_is_a_number_or_zero():
    assert rules._number(True) == 1
    assert rules._number(False) == 0
    assert rules._number(7) == 7
    assert rules._number(2.5) == 2.5
    assert rules._number(float("nan")) == 0
    assert rules._number("12") == 12.0
    assert rules._number("nan") == 0
    assert rules._number("x") == 0
    assert rules._number(None) == 0
    assert rules._number([]) == 0
    assert not math.isnan(rules._number(float("nan")))


# Words


def test_names_and_devices_and_queries_are_singular_only_for_one():
    assert rules.group_names_text(0) == "0 names"
    assert rules.group_names_text(1) == "1 name"
    assert rules.group_names_text(2) == "2 names"
    assert rules.query_count_text(1) == "1 query"
    assert rules.query_count_text(0) == "0 queries"
    assert rules.query_count_text(2) == "2 queries"


def test_a_system_or_cdn_group_is_risky_to_block():
    assert rules.is_risky_group_kind("system") is True
    assert rules.is_risky_group_kind("cdn") is True
    for kind in ("app", "unknown", "", None, "SYSTEM", "CDN"):
        assert rules.is_risky_group_kind(kind) is False


def test_the_running_sentence_counts_devices():
    one = rules.runtime_status_text("applied", True, {"deviceOnly": ["a"]})
    assert one["text"] == (
        "Running for 1 device only. Everyone and other saved settings stay paused. "
        "Start resumes them all.")
    assert one["tone"] == "ok"
    two = rules.runtime_status_text("unknown", True, {"deviceOnly": ["a", "b"]})
    assert two["text"] == (
        "Running for 2 devices only. Everyone and other saved settings stay paused. "
        "Start resumes them all.")
    # Without device-only or in another state the plain sentences apply.
    assert rules.runtime_status_text("applied", True, {"deviceOnly": []})["text"] == (
        "Running. Your saved settings are applied. New changes apply on their own.")
    assert rules.runtime_status_text("applied", True, {"deviceOnly": "a"})["tone"] == "ok"
    assert rules.runtime_status_text("error", True, {"deviceOnly": ["a"]}) == {
        "text": "The last apply failed. Check the Pi.", "tone": "error"}
    assert rules.runtime_status_text("applied", False, {"deviceOnly": ["a"]}) == {
        "text": "Applied. Waiting for the scheduler.", "tone": "muted"}


# The general profile badge


def test_the_general_badge_follows_the_profile_and_the_observed_state():
    assert rules.general_badge(None) == "General · saved"
    assert rules.general_badge({}) == "General · saved"
    assert rules.general_badge({"profile": None, "active": True}) == "General · saved"
    assert rules.general_badge({"profile": {"id": "p"}, "active": True}) == "General · active"
    assert rules.general_badge({"profile": {"id": "p"}, "active": False}) == "General · saved"
    paused = {"profile": {"id": "p"}, "state": {"observed_state": "reset"}}
    assert rules.general_badge(paused) == "General · paused"
    assert rules.general_badge({"state": {"observed_state": "applied"}}) == "General · saved"
    assert rules.general_badge({"state": {"observed_state": "reset"}}) == "General · paused"


# Traffic groups


def test_groups_start_single_and_stop_being_single_with_a_second_name():
    one = rules.group_observations([observation("a.com")])
    assert one[0]["single"] is True
    two = rules.group_observations([observation("a.com"), observation("a.com")])
    assert two[0]["single"] is False
    assert two[0]["totalQueries"] == 2


def test_a_group_without_a_kind_has_an_empty_kind_and_a_name_of_its_domain():
    group = rules.group_observations([observation("a.com", 3)])[0]
    assert group == {
        "key": "domain:a.com", "name": "a.com", "kind": "", "names": [observation("a.com", 3)],
        "totalQueries": 3, "single": True}


def test_a_risky_kind_replaces_a_safe_one_but_never_the_other_way():
    def row(kind):
        return observation("a.com", group={"key": "k", "name": "K", "kind": kind})

    assert rules.group_observations([row("app"), row("cdn")])[0]["kind"] == "cdn"
    assert rules.group_observations([row("app"), row("system")])[0]["kind"] == "system"
    assert rules.group_observations([row("cdn"), row("app")])[0]["kind"] == "cdn"
    assert rules.group_observations([row("system"), row("cdn")])[0]["kind"] == "system"
    assert rules.group_observations([row("app"), row("app")])[0]["kind"] == "app"
    assert rules.group_observations([row(None), row("system")])[0]["kind"] == "system"
    assert rules.group_observations([row("app"), row(None)])[0]["kind"] == "app"
    assert rules.group_observations([row(""), row("unknown")])[0]["kind"] == ""
    assert rules.group_observations([row("unknown"), row("cdn")])[0]["kind"] == "cdn"


def test_groups_are_ordered_by_queries_then_by_lowercase_name():
    def row(name, queries):
        return observation(name, queries, group={"key": name, "name": name})

    ordered = rules.group_observations([row("b", 1), row("a_x", 1), row("aB", 1), row("z", 9)])
    assert [group["name"] for group in ordered] == ["z", "a_x", "aB", "b"]
    assert rules.group_observations(None) == []
    assert rules.group_observations([]) == []


def test_a_name_with_text_for_a_count_counts_as_that_number():
    rows = [observation("a.com", "4"), observation("a.com", "x"), observation("a.com", None)]
    assert rules.group_observations(rows)[0]["totalQueries"] == 4


def test_the_hint_joins_the_apps_and_offers_a_search_for_unknown_and_cdn_names():
    hint = rules.domain_hint(observation("a.com", seen_with=["Mail", "Maps"]))
    assert hint == {"text": "Seen with Mail and Maps (guess)", "searchable": False}
    assert rules.domain_hint(observation("a.com", seen_with=["Mail"]))["text"] == (
        "Seen with Mail (guess)")
    assert rules.domain_hint(observation("a.com")) == {"text": "", "searchable": False}
    assert rules.domain_hint(observation("a.com", label={"kind": "unknown"}))["searchable"]
    assert rules.domain_hint(observation("a.com", label={"kind": "cdn"}))["searchable"]
    assert not rules.domain_hint(observation("a.com", label={"kind": "app"}))["searchable"]
    assert not rules.domain_hint(observation("a.com", label={"kind": "system"}))["searchable"]


def test_a_group_is_uniform_when_every_name_has_the_same_category():
    def group(*categories):
        return {"names": [observation("a.com", label={"category": category} if category is not None
                                      else None) for category in categories]}

    assert rules.group_categories_uniform(group("Video", "Video")) is True
    assert rules.group_categories_uniform(group("Video", "Music")) is False
    assert rules.group_categories_uniform(group("Video", "")) is False
    assert rules.group_categories_uniform(group("", None)) is True
    assert rules.group_categories_uniform(group(None, "Video")) is False
    assert rules.group_categories_uniform(group("Video")) is True
    assert rules.group_categories_uniform(group("", "Video", "Video")) is False
    # A missing category is not a category that happens to be spelled like a placeholder.
    assert rules.group_categories_uniform(group("XXXX", None)) is False
    assert rules.group_categories_uniform({"names": [
        observation("a.com", label={"category": ""}), observation("b.com", label={"other": "x"}),
    ]}) is True


def test_a_row_label_hides_a_name_or_category_that_the_group_already_shows():
    row = observation("a.com", label={"name": "Mail", "category": "Email", "kind": "app"})
    assert rules.row_label_parts(row) == {
        "labelName": "Mail", "category": "Email", "kind": "app", "showName": True,
        "showCategory": True}
    assert rules.row_label_parts(row, "Mail", False) == {
        "labelName": "Mail", "category": "Email", "kind": "app", "showName": False,
        "showCategory": True}
    assert rules.row_label_parts(row, "Mail", True)["showCategory"] is False
    assert rules.row_label_parts(row, "Other", True)["showCategory"] is True
    assert rules.row_label_parts(row, "Other", True)["showName"] is True
    assert rules.row_label_parts(row, "", True)["showCategory"] is True
    assert rules.row_label_parts(row, "", True)["showName"] is True
    assert rules.row_label_parts(row, "Mail")["showCategory"] is True
    assert rules.row_label_parts(observation("a.com", label={"name": "XXXX"}))["showName"] is True
    assert rules.row_label_parts({}) == {
        "labelName": "", "category": "", "kind": "", "showName": False, "showCategory": False}
    no_category = observation("a.com", label={"name": "Mail"})
    assert rules.row_label_parts(no_category, "Mail", True)["showCategory"] is False
    assert rules.row_label_parts(observation("a.com", label={"category": "Email"}),
                                 "", True)["showName"] is False


# The sidebar


def sidebar(**changes):
    data = {
        "adminTokenVerified": True,
        "loaded": {name: True for name in rules.SIDEBAR_INPUTS},
        "runtimeState": "applied", "running": True, "clientsAvailable": True,
        "profiles": [{"id": "slow", "name": "Slow"}],
        "clients": [],
    }
    data.update(changes)
    return data


def client(name, device, profile="slow", **fields):
    return {"display_name": name, "mac_address": "02:00:00:00:00:01", "unaffected": False,
            "association_verified": True, "assigned_device_id": device,
            "assigned_profile_id": profile, **fields}


def test_a_device_that_is_unverified_or_protected_is_not_listed_as_affected():
    clients = [
        client("Plain", "a"),
        client("Unverified", "b", assigned_effective_state="unverified"),
        client("Protected", "c", assigned_effective_state="protected"),
        client("Active", "d", assigned_effective_state="active"),
    ]
    devices = rules.sidebar_status(sidebar(clients=clients))["devices"]
    assert [item["name"] for item in devices["items"]] == ["Plain", "Active"]
    assert devices["summary"] == "2 devices affected"


def test_the_summary_is_singular_for_one_device_and_names_the_empty_cases():
    one = rules.sidebar_status(sidebar(clients=[client("Plain", "a")]))["devices"]
    assert one["summary"] == "1 device affected"
    none = rules.sidebar_status(sidebar(clients=[client("Plain", None)]))["devices"]
    assert none["summary"] == "No device has its own profile"
    other = rules.sidebar_status(sidebar(clients=[client("A", "a", unaffected=True)]))["devices"]
    assert other["summary"] == "No connected device is affected"
    assert rules.sidebar_status(sidebar())["devices"]["summary"] == (
        "No connected device is affected")


def test_a_device_only_start_lists_only_its_own_devices_and_ignores_a_bad_value():
    clients = [client("Own", "a"), client("Other", "b")]
    own = rules.sidebar_status(sidebar(clients=clients, deviceOnly=["a"]))["devices"]
    assert [item["name"] for item in own["items"]] == ["Own"]
    for bad in ("a", {"a": 1}, None, 5, True):
        listed = rules.sidebar_status(sidebar(clients=clients, deviceOnly=bad))["devices"]
        assert [item["name"] for item in listed["items"]] == ["Own", "Other"]


def test_the_list_of_devices_is_cut_at_the_limit_and_counts_the_rest():
    clients = [client(f"D{n}", str(n)) for n in range(8)]
    default = rules.sidebar_status(sidebar(clients=clients))["devices"]
    assert len(default["items"]) == 5 and default["more"] == 3
    everything = rules.sidebar_status(sidebar(clients=clients), limit=None)["devices"]
    assert len(everything["items"]) == 8 and everything["more"] == 0
    two = rules.sidebar_status(sidebar(clients=clients), limit=2)["devices"]
    assert len(two["items"]) == 2 and two["more"] == 6


def test_the_profile_of_a_device_falls_back_to_its_id_and_then_to_nothing():
    clients = [client("Named", "a", "slow"), client("Gone", "b", "gone"),
               client("Unset", "c", None), client("Group", "d", "slow", group_profile_id="gone")]
    items = rules.sidebar_status(sidebar(clients=clients))["devices"]["items"]
    assert [(item["name"], item["profile"]) for item in items] == [
        ("Named", "Slow"), ("Gone", "gone"), ("Unset", ""), ("Group", "gone")]
    nameless = rules.sidebar_status(sidebar(
        profiles=[{"id": "slow", "name": ""}], clients=[client("N", "a")]))["devices"]["items"]
    assert nameless[0]["profile"] == "slow"


def test_the_general_profile_line_names_the_profile_or_its_id():
    general = {"profile": {"id": "slow", "name": ""}, "active": True}
    line = rules.sidebar_status(sidebar(generalState=general))["general"]
    assert line == {"text": "Everyone: slow · active", "tone": "on"}
    named = {"profile": {"id": "slow", "name": "Shared"}, "active": True}
    assert rules.sidebar_status(sidebar(generalState=named))["general"]["text"] == (
        "Everyone: Shared · active")
    saved = rules.sidebar_status(sidebar(generalState={"profile": {"id": "slow"}, "active": False,
                                                       }, runtimeState="unknown",
                                         running=False))["general"]
    assert saved == {"text": "Everyone: slow · saved, not applied", "tone": "muted"}
    paused = rules.sidebar_status(sidebar(generalState=named, runtimeState="reset"))["general"]
    assert paused["text"] == "Everyone: Shared · paused"
    assert rules.sidebar_status(sidebar(generalState=None))["general"] == {
        "text": "Everyone: none", "tone": "muted"}
    assert rules.sidebar_status(sidebar(generalState={"profile": None}))["general"][
        "text"] == "Everyone: none"

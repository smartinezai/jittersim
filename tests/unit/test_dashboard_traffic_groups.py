"""Run the dashboard's Traffic discovery grouping and group button logic in node."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML, MAX_BATCH_DOMAINS

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
ENFORCE = "Apply the configuration to enforce it."
ADD_BLOCK = "/api/observations/add-global-block"
REMOVE_BLOCK = "/api/observations/remove-global-block"


def served_functions():
    # The group buttons reuse profileButtonState and blockButtonState so the slice starts there.
    start = SCRIPT.index("function blockButtonState(")
    return SCRIPT[start : SCRIPT.index("function renderObservations(", start)]


def run_node(program):
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def evaluate(expression, setup=""):
    program = (
        served_functions()
        + "\n"
        + setup
        + "\nconsole.log(JSON.stringify("
        + expression
        + "));"
    )
    return json.loads(run_node(program))


def item(domain, count, key=None, name=None, blocked=None):
    """Build one API item the way /api/observations sends it."""

    value = {"domain": domain, "query_count": count}
    if key is not None:
        value["group"] = {"key": key, "name": name}
    if blocked is not None:
        value["globally_blocked"] = blocked
    return value


IMMO_NAMES = [
    ("content.header.immobilienscout24.de", 4),
    ("osa.s24-media.immobilienscout24.de", 3),
    ("sso.immobilienscout24.de", 2),
    ("tenant-network.immobilienscout24.de", 2),
    ("tracking.immobilienscout24.de", 6),
    ("web.chatbot.immobilienscout24.de", 1),
    ("api.header.immobilienscout24.de", 4),
]
NETFLIX_NAMES = [("logs.dradis.netflix.com", 3), ("ipv4-c179-fra002-ix.1.oca.nflxvideo.net", 40)]


def immo(blocked=None):
    return [item(d, c, "label:immoscout24", "ImmoScout24", blocked) for d, c in IMMO_NAMES]


def netflix(blocked=None):
    return [item(d, c, "label:netflix", "Netflix", blocked) for d, c in NETFLIX_NAMES]


def group_of(items, name="ImmoScout24"):
    return {"key": "label:" + name.lower(), "name": name, "names": items}


@NODE
def test_the_real_example_makes_one_group_per_app_and_keeps_third_party_names_apart():
    domains = (
        immo()
        + netflix()
        + [
            item("config.extension.grammarly.com", 5, "label:grammarly", "Grammarly"),
            item("tags-eu.tiqcdn.com", 2, "domain:tiqcdn.com", "tiqcdn.com"),
            item("a1051.dscg4.akamai.net", 3, "domain:akamai.net", "akamai.net"),
        ]
    )

    groups = evaluate("groupObservations(" + json.dumps(domains) + ")")

    assert [(g["name"], g["totalQueries"], len(g["names"]), g["single"]) for g in groups] == [
        ("Netflix", 43, 2, False),
        ("ImmoScout24", 22, 7, False),
        ("Grammarly", 5, 1, True),
        ("akamai.net", 3, 1, True),
        ("tiqcdn.com", 2, 1, True),
    ]
    assert [g["key"] for g in groups] == [
        "label:netflix",
        "label:immoscout24",
        "label:grammarly",
        "domain:akamai.net",
        "domain:tiqcdn.com",
    ]


@NODE
def test_a_group_keeps_the_api_items_in_the_order_they_came():
    groups = evaluate("groupObservations(" + json.dumps(immo()) + ")")

    assert [name["domain"] for name in groups[0]["names"]] == [d for d, _ in IMMO_NAMES]
    assert groups[0]["names"][0] == immo()[0]


@NODE
def test_groups_with_the_same_total_sort_by_name_ignoring_case():
    domains = [
        item("z.example.com", 5, "domain:z.example.com", "zeta.example"),
        item("a.example.com", 5, "domain:a.example.com", "Alpha.example"),
        item("b.example.com", 5, "domain:b.example.com", "beta.example"),
        item("big.example.com", 9, "domain:big.example.com", "big.example"),
    ]

    groups = evaluate("groupObservations(" + json.dumps(domains) + ")")

    assert [g["name"] for g in groups] == [
        "big.example",
        "Alpha.example",
        "beta.example",
        "zeta.example",
    ]


@NODE
def test_a_bigger_total_beats_a_smaller_name():
    domains = [item("a.example.com", 1, "domain:a", "a"), item("z.example.com", 2, "domain:z", "z")]

    groups = evaluate("groupObservations(" + json.dumps(domains) + ")")

    assert [g["name"] for g in groups] == ["z", "a"]


@NODE
def test_a_missing_group_falls_back_to_the_name_itself():
    domains = [item("x.example.com", 2), item("y.example.com", 1)]

    groups = evaluate("groupObservations(" + json.dumps(domains) + ")")

    assert [(g["key"], g["name"], g["single"]) for g in groups] == [
        ("domain:x.example.com", "x.example.com", True),
        ("domain:y.example.com", "y.example.com", True),
    ]


@NODE
def test_no_names_make_no_groups():
    assert evaluate("groupObservations([])") == []
    assert evaluate("groupObservations(undefined)") == []


@NODE
def test_a_missing_query_count_counts_as_zero():
    domains = [{"domain": "x.example.com", "group": {"key": "domain:e", "name": "e"}}]

    groups = evaluate("groupObservations(" + json.dumps(domains) + ")")

    assert groups[0]["totalQueries"] == 0


def block_action(items):
    return evaluate("groupBlockAction(" + json.dumps(group_of(items)) + ")")


@NODE
def test_no_blocked_name_offers_block_all_with_every_name():
    action = block_action(immo(blocked=False))

    assert action == {
        "label": "Block all",
        "path": ADD_BLOCK,
        "domains": [d for d, _ in IMMO_NAMES],
    }


@NODE
def test_some_blocked_names_offer_block_remaining_with_only_the_open_ones():
    items = immo(blocked=False)
    items[0]["globally_blocked"] = True
    items[4]["globally_blocked"] = True

    action = block_action(items)

    assert action["label"] == "Block remaining"
    assert action["path"] == ADD_BLOCK
    assert action["domains"] == [d for i, (d, _) in enumerate(IMMO_NAMES) if i not in (0, 4)]


@NODE
def test_all_blocked_names_offer_unblock_all_with_every_name():
    action = block_action(immo(blocked=True))

    assert action == {
        "label": "Unblock all",
        "path": REMOVE_BLOCK,
        "domains": [d for d, _ in IMMO_NAMES],
    }


@NODE
def test_a_missing_or_loose_flag_counts_as_not_blocked_like_the_row_button():
    items = [
        item("a.example.com", 1, "domain:e", "e", "true"),
        item("b.example.com", 1, "domain:e", "e"),
    ]

    action = block_action(items)

    assert action["label"] == "Block all"
    assert action["domains"] == ["a.example.com", "b.example.com"]


SLOW = {"id": "slow", "name": "Slow", "website_domains": ["sso.immobilienscout24.de", "a.test"]}


def profile_action(items, profile):
    return evaluate(
        "groupProfileAction(" + json.dumps(group_of(items)) + ", " + json.dumps(profile) + ")"
    )


@NODE
def test_no_profile_disables_add_all():
    expected = {"label": "Add all to profile", "disabled": True, "domains": []}

    assert profile_action(immo(), None) == expected
    group = json.dumps(group_of(immo()))
    assert evaluate("groupProfileAction(" + group + ", undefined)") == expected


@NODE
def test_every_name_already_in_the_profile_disables_the_button_and_says_so():
    profile = {"id": "slow", "name": "Slow", "website_domains": [d for d, _ in IMMO_NAMES]}

    assert profile_action(immo(), profile) == {
        "label": "All already in Slow",
        "disabled": True,
        "domains": [],
    }


@NODE
def test_missing_names_are_offered_and_the_ones_already_in_are_left_out():
    action = profile_action(immo(), SLOW)

    assert action["label"] == "Add all to profile"
    assert action["disabled"] is False
    assert action["domains"] == [d for d, _ in IMMO_NAMES if d != "sso.immobilienscout24.de"]


@NODE
def test_only_an_exact_match_counts_as_already_in_the_profile():
    profile = {"id": "p", "name": "P", "website_domains": ["immobilienscout24.de"]}

    action = profile_action(immo(), profile)

    assert action["disabled"] is False
    assert len(action["domains"]) == 7


@NODE
def test_a_profile_without_a_website_list_can_take_every_name():
    action = profile_action(immo(), {"id": "p", "name": "P"})

    assert action["disabled"] is False
    assert len(action["domains"]) == 7


@NODE
def test_the_block_messages_name_the_group_and_the_count():
    group = group_of(immo(blocked=False))
    adding = evaluate(
        "groupBlockMessages(" + json.dumps(group) + ", groupBlockAction(" + json.dumps(group) + "))"
    )
    assert adding == {
        "busy": "Adding 7 ImmoScout24 names to the global block list...",
        "done": "Added 7 ImmoScout24 names to the global block list. " + ENFORCE,
    }

    group = group_of(immo(blocked=True))
    removing = evaluate(
        "groupBlockMessages(" + json.dumps(group) + ", groupBlockAction(" + json.dumps(group) + "))"
    )
    assert removing == {
        "busy": "Removing 7 ImmoScout24 names from the global block list...",
        "done": "Removed 7 ImmoScout24 names from the global block list. " + ENFORCE,
    }


@NODE
def test_block_remaining_counts_only_the_names_it_changes_and_uses_the_singular_for_one():
    items = immo(blocked=True)
    items[0]["globally_blocked"] = False
    group = group_of(items)

    messages = evaluate(
        "groupBlockMessages(" + json.dumps(group) + ", groupBlockAction(" + json.dumps(group) + "))"
    )

    assert messages["busy"] == "Adding 1 ImmoScout24 name to the global block list..."


@NODE
def test_the_profile_messages_name_the_group_and_the_count():
    group = group_of(immo())
    action = evaluate(
        "groupProfileAction(" + json.dumps(group) + ", " + json.dumps(SLOW) + ")"
    )

    messages = evaluate(
        "groupProfileMessages(" + json.dumps(group) + ", " + json.dumps(action) + ")"
    )

    assert messages == {
        "busy": "Adding 6 ImmoScout24 names to the profile...",
        "done": "Added 6 ImmoScout24 names to the profile.",
    }


@NODE
@pytest.mark.parametrize(
    "blocked, text",
    [([False, False, False], ""), ([True, False, False], "1 blocked"),
     ([True, True, False], "2 blocked"), ([True, True, True], "All blocked")],
)
def test_the_group_says_how_many_of_its_names_are_blocked(blocked, text):
    items = [
        item("n%d.example.com" % i, 1, "domain:e", "e", flag) for i, flag in enumerate(blocked)
    ]

    assert evaluate("groupBlockedText(" + json.dumps(group_of(items)) + ")") == text


@NODE
def test_the_name_count_text_uses_the_singular_for_one():
    assert evaluate("groupNamesText(1)") == "1 name"
    assert evaluate("groupNamesText(7)") == "7 names"


@NODE
def test_the_open_key_joins_the_device_address_and_the_group_key():
    assert evaluate("observationGroupOpenKey('192.168.50.124', 'label:netflix')") == (
        "192.168.50.124|label:netflix"
    )
    one = evaluate("observationGroupOpenKey('192.168.50.124', 'label:netflix')")
    other = evaluate("observationGroupOpenKey('192.168.50.125', 'label:netflix')")
    assert one != other


def test_the_group_limit_matches_the_server_limit():
    assert "const GROUP_ACTION_MAX_NAMES = %d;" % MAX_BATCH_DOMAINS in served_functions()


@NODE
def test_a_group_that_is_too_big_for_one_call_says_why():
    assert evaluate("groupLimitNote(%d)" % MAX_BATCH_DOMAINS) == ""
    note = evaluate("groupLimitNote(%d)" % (MAX_BATCH_DOMAINS + 1))
    assert "65" in note and str(MAX_BATCH_DOMAINS) in note

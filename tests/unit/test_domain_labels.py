import pytest

from jittersim.config.domain_labels import (
    LABEL_KINDS,
    SEEN_WITH_WINDOW_SECONDS,
    TABLE_SOURCE,
    group_domain,
    label_domain,
    registrable_domain,
    seen_with,
)
from jittersim.config.models import normalize_domain
from jittersim.config.service_catalogue import SERVICE_CATALOGUE


@pytest.mark.parametrize(
    ("domain", "kind", "name", "category"),
    [
        ("mmx-ds.cdn.whatsapp.net", "app", "WhatsApp", "Messaging"),
        ("a1051.dscg4.akamai.net", "cdn", "Akamai", "Shared CDN"),
        ("10000.mt.eu.iot.dreame.tech", "iot", "Dreame", "Robot vacuum"),
        ("api.header.immobilienscout24.de", "app", "ImmoScout24", "Real estate"),
        ("osa.s24-media.immobilienscout24.de", "app", "ImmoScout24", "Real estate"),
        ("logs.dradis.netflix.com", "app", "Netflix", "Streaming"),
        ("ipv4-c179-fra002-ix.1.oca.nflxvideo.net", "app", "Netflix", "Streaming"),
        ("config.extension.grammarly.com", "app", "Grammarly", "Writing assistant"),
        ("tags-eu.tiqcdn.com", "tracker", "Tealium", "Tag manager"),
    ],
)
def test_real_names_from_the_dashboard(domain, kind, name, category):
    label = label_domain(domain)
    assert (label.kind, label.name, label.category, label.known) == (kind, name, category, True)


def test_the_longest_suffix_wins():
    # microsoft.com style parents must not hide a more specific entry.
    assert label_domain("teams.microsoft.com").name == "Microsoft Teams"
    assert label_domain("update.microsoft.com").name == "Microsoft"
    assert label_domain("meet.google.com").name == "Google Meet"
    assert label_domain("www.google.com").name == "Google"


def test_an_unknown_name_shows_its_registrable_domain():
    label = label_domain("cdn.somesmallsite.io")
    assert (label.kind, label.name, label.category, label.known) == (
        "unknown", "somesmallsite.io", "Unknown site", False)


def test_the_apex_itself_matches():
    assert label_domain("whatsapp.net").name == "WhatsApp"


def test_a_name_is_normalized_before_matching():
    assert label_domain("MMX-DS.CDN.WhatsApp.net.").name == "WhatsApp"


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("a.b.c.example.com", "example.com"),
        ("example.com", "example.com"),
        ("a.b.example.co.uk", "example.co.uk"),
        ("x.example.com.au", "example.com.au"),
        ("localhost", "localhost"),
        ("deep.sub.immobilienscout24.de", "immobilienscout24.de"),
    ],
)
def test_registrable_domain(domain, expected):
    assert registrable_domain(domain) == expected


def test_the_table_is_clean():
    suffixes = [entry[0] for entry in TABLE_SOURCE]
    assert len(suffixes) == len(set(suffixes)), "duplicate suffix in the label table"
    for suffix, name, kind, category in TABLE_SOURCE:
        assert normalize_domain(suffix) == suffix
        assert kind in LABEL_KINDS and kind != "unknown"
        assert name.strip() and category.strip()


def test_every_catalogue_hostname_is_in_the_table_as_an_app():
    by_suffix = {entry[0]: entry for entry in TABLE_SOURCE}
    for service in SERVICE_CATALOGUE:
        for domain in service.domains:
            _, name, kind, category = by_suffix[domain]
            assert (name, kind) == (service.name, "app")
            assert category != "Online service", service.key


def test_as_dict_has_four_keys():
    assert set(label_domain("whatsapp.net").as_dict()) == {"kind", "name", "category", "known"}


def test_a_shared_name_is_seen_with_the_app_at_the_same_time():
    pairs = [("mmx-ds.cdn.whatsapp.net", 100), ("a1051.dscg4.akamai.net", 100)]
    assert seen_with(pairs) == {"a1051.dscg4.akamai.net": ["WhatsApp"]}


def test_the_window_edge():
    inside = [("whatsapp.net", 100), ("a.akamai.net", 100 + SEEN_WITH_WINDOW_SECONDS)]
    outside = [("whatsapp.net", 100), ("a.akamai.net", 101 + SEEN_WITH_WINDOW_SECONDS)]
    assert seen_with(inside) == {"a.akamai.net": ["WhatsApp"]}
    assert seen_with(outside) == {}


def test_the_window_counts_in_both_directions():
    pairs = [("a.akamai.net", 100), ("whatsapp.net", 100 + SEEN_WITH_WINDOW_SECONDS)]
    assert seen_with(pairs) == {"a.akamai.net": ["WhatsApp"]}


def test_trackers_and_unknown_names_get_a_hint_but_apps_do_not():
    pairs = [
        ("sso.immobilienscout24.de", 5),
        ("tags-eu.tiqcdn.com", 5),
        ("cdn.somesmallsite.io", 5),
    ]
    assert seen_with(pairs) == {
        "tags-eu.tiqcdn.com": ["ImmoScout24"],
        "cdn.somesmallsite.io": ["ImmoScout24"],
    }


def test_system_and_smart_home_names_are_neither_hinted_nor_used_as_hints():
    pairs = [("10000.mt.eu.iot.dreame.tech", 5), ("www.apple.com", 5), ("a.akamai.net", 5)]
    assert seen_with(pairs) == {}


def test_too_many_apps_at_once_gives_no_hint():
    pairs = [("whatsapp.net", 1), ("netflix.com", 1), ("spotify.com", 1), ("a.akamai.net", 1)]
    assert seen_with(pairs) == {}


def test_two_names_of_one_app_count_once_and_nearest_first():
    pairs = [
        ("a.whatsapp.net", 3), ("b.whatsapp.net", 4), ("netflix.com", 9), ("x.akamai.net", 5),
    ]
    assert seen_with(pairs) == {"x.akamai.net": ["WhatsApp", "Netflix"]}


def test_two_apps_at_the_same_distance_sort_by_name():
    pairs = [("netflix.com", 4), ("whatsapp.net", 6), ("x.akamai.net", 5)]
    assert seen_with(pairs) == {"x.akamai.net": ["Netflix", "WhatsApp"]}


def test_no_observations_gives_no_hints():
    assert seen_with([]) == {}


def test_two_netflix_domains_share_a_group():
    a = group_domain("logs.dradis.netflix.com")
    b = group_domain("ipv4-c179-fra002-ix.1.oca.nflxvideo.net")
    assert a == b == {"key": "label:netflix", "name": "Netflix"}


def test_every_immoscout_name_shares_a_group():
    names = [
        "content.header.immobilienscout24.de",
        "osa.s24-media.immobilienscout24.de",
        "sso.immobilienscout24.de",
        "tenant-network.immobilienscout24.de",
        "tracking.immobilienscout24.de",
        "web.chatbot.immobilienscout24.de",
        "api.header.immobilienscout24.de",
    ]
    assert {group_domain(name)["key"] for name in names} == {"label:immoscout24"}


def test_shared_infrastructure_groups_by_registrable_domain():
    assert group_domain("a1051.dscg4.akamai.net") == {
        "key": "domain:akamai.net", "name": "akamai.net"}
    assert group_domain("tags-eu.tiqcdn.com") == {
        "key": "domain:tiqcdn.com", "name": "tiqcdn.com"}


def test_unknown_names_group_by_registrable_domain():
    assert group_domain("a.b.somesmallsite.co.uk") == {
        "key": "domain:somesmallsite.co.uk", "name": "somesmallsite.co.uk"}

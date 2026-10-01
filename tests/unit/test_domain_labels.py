import pytest

from jittersim.config.domain_labels import (
    LABEL_KINDS,
    SEEN_WITH_WINDOW_SECONDS,
    TABLE_SOURCE,
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


@pytest.mark.parametrize(
    ("child", "name", "category", "parent", "parent_name"),
    [
        ("px.ads.linkedin.com", "LinkedIn Insight", "Advertising", "www.linkedin.com", "LinkedIn"),
        ("snap.licdn.com", "LinkedIn Insight", "Advertising", "media.licdn.com", "LinkedIn"),
        ("analytics.tiktok.com", "TikTok Pixel", "Advertising", "www.tiktok.com", "TikTok"),
        ("analytics.twitter.com", "X Ads", "Advertising", "www.twitter.com", "X (Twitter)"),
        ("pixel.facebook.com", "Meta Pixel", "Advertising", "www.facebook.com", "Facebook"),
        ("ct.pinterest.com", "Pinterest Tag", "Advertising", "www.pinterest.com", "Pinterest"),
        ("ads.pinterest.com", "Pinterest Tag", "Advertising", "i.pinimg.com", "Pinterest"),
        ("bat.bing.com", "Microsoft Advertising", "Advertising", "www.bing.com", "Bing"),
        ("adservice.google.com", "Google Ads", "Advertising", "www.google.com", "Google"),
        ("ads.google.com", "Google Ads", "Advertising", "mail.google.com", "Gmail"),
    ],
)
def test_a_tag_endpoint_under_an_app_is_a_tracker_but_the_app_stays_an_app(
    child, name, category, parent, parent_name
):
    label = label_domain(child)
    assert (label.kind, label.name, label.category, label.known) == (
        "tracker", name, category, True)
    assert label_domain("x." + child).kind == "tracker"
    assert label_domain(parent).name == parent_name
    assert label_domain(parent).kind != "tracker"


def test_a_tag_endpoint_under_a_social_app_is_not_a_seen_with_source():
    pairs = [("px.ads.linkedin.com", 5), ("snap.licdn.com", 5), ("a.akamai.net", 5)]
    assert seen_with(pairs) == {}


@pytest.mark.parametrize(
    ("domain", "name"),
    [
        ("storage.googleapis.com", "Google Cloud Storage"),
        ("my-bucket.storage.googleapis.com", "Google Cloud Storage"),
        ("1-2-3-4.bc.googleusercontent.com", "Google Cloud"),
    ],
)
def test_google_shared_platforms_are_cdn_names(domain, name):
    label = label_domain(domain)
    assert (label.kind, label.name, label.category) == ("cdn", name, "Cloud hosting")


def test_the_google_parents_stay_system_names():
    assert label_domain("www.google.com").kind == "system"
    assert label_domain("fonts.googleapis.com").kind == "system"
    assert label_domain("lh3.googleusercontent.com").kind == "system"


@pytest.mark.parametrize(
    "domain", ["amazon.com", "www.amazon.de", "m.media-amazon.com", "x.ssl-images-amazon.com"]
)
def test_the_amazon_apex_names_say_amazon_services(domain):
    label = label_domain(domain)
    assert (label.kind, label.name, label.category) == ("app", "Amazon", "Amazon services")


def test_alexa_com_is_not_a_smart_home_name_but_the_alexa_service_is():
    assert label_domain("alexa.com").known is False
    label = label_domain("device-metrics-us.amazonalexa.com")
    assert (label.kind, label.name) == ("iot", "Amazon Alexa")


@pytest.mark.parametrize(
    ("domain", "name"),
    [
        ("notwhatsapp.net", "notwhatsapp.net"),
        ("evilwhatsapp.net", "evilwhatsapp.net"),
        ("xt.me", "xt.me"),
        ("whatsapp.net.evil.com", "evil.com"),
    ],
)
def test_a_partial_label_never_matches(domain, name):
    label = label_domain(domain)
    assert (label.kind, label.name, label.known) == ("unknown", name, False)


def test_the_nearest_name_of_an_app_decides_the_distance():
    pairs = [("a.whatsapp.net", 100), ("b.whatsapp.net", 120), ("x.akamai.net", 118)]
    assert seen_with(pairs) == {"x.akamai.net": ["WhatsApp"]}


@pytest.mark.parametrize(
    "domain",
    [
        "bucket.s3.amazonaws.com",
        "d111111abcdef8.cloudfront.net",
        "a1051.dscg4.akamai.net",
        "cdn.azureedge.net",
    ],
)
def test_shared_hosting_names_are_cdn_names(domain):
    assert label_domain(domain).kind == "cdn"


@pytest.mark.parametrize("address", ["192.168.1.1", "10.0.0.7", "8.8.8.8"])
def test_a_bare_ipv4_address_stays_whole_and_is_unknown(address):
    assert registrable_domain(address) == address
    label = label_domain(address)
    assert (label.kind, label.name, label.category, label.known) == (
        "unknown", address, "Unknown site", False)


def test_a_name_that_only_looks_like_an_address_still_uses_the_last_two_labels():
    assert registrable_domain("1.2.3.example.com") == "example.com"
    assert registrable_domain("300.1.1.1") == "1.1"


@pytest.mark.parametrize(
    "domain", ["4.3.2.1.in-addr.arpa", "1.0.0.127.in-addr.arpa", "b.a.9.8.7.6.5.4.ip6.arpa"]
)
def test_reverse_lookup_names_are_system_names_and_stay_whole(domain):
    assert registrable_domain(domain) == domain
    label = label_domain(domain)
    assert (label.kind, label.name, label.category, label.known) == (
        "system", "Reverse DNS", "Reverse lookup", True)


@pytest.mark.parametrize(
    "suffix",
    [
        "co.kr", "or.kr", "com.ar", "com.tw", "com.pl", "ne.jp", "or.jp", "co.id", "co.il",
        "com.my", "com.ph", "com.vn", "com.ua", "org.nz", "net.nz", "co.th",
    ],
)
def test_more_second_level_suffixes_keep_three_labels(suffix):
    assert registrable_domain("www.shop.example." + suffix) == "example." + suffix
    assert registrable_domain("example." + suffix) == "example." + suffix


@pytest.mark.parametrize(
    "domain",
    [
        "google.de", "google.co.uk", "google.fr", "google.at", "google.ch", "google.nl",
        "google.es", "google.it", "google.pl",
    ],
)
def test_google_country_domains_are_google_system_names(domain):
    for name in (domain, "www." + domain):
        label = label_domain(name)
        assert (label.kind, label.name, label.category, label.known) == (
            "system", "Google", "Google services", True)

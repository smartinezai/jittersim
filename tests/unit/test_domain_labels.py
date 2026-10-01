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


def test_ties_sort_case_insensitively():
    pairs = [("ebay.com", 5), ("netflix.com", 5), ("x.akamai.net", 5)]
    assert seen_with(pairs) == {"x.akamai.net": ["eBay", "Netflix"]}


def test_a_tracker_is_never_seen_with_the_app_of_its_own_name():
    pairs = [("connect.facebook.net", 5), ("www.facebook.com", 5)]
    assert seen_with(pairs) == {}


def test_the_own_app_is_left_out_before_the_other_apps_are_counted():
    pairs = [("connect.facebook.net", 5), ("www.facebook.com", 5), ("netflix.com", 5)]
    assert seen_with(pairs) == {"connect.facebook.net": ["Netflix"]}
    two_more = pairs + [("whatsapp.net", 5)]
    assert seen_with(two_more) == {"connect.facebook.net": ["Netflix", "WhatsApp"]}
    crowded = two_more + [("spotify.com", 5)]
    assert seen_with(crowded) == {}


def test_other_trackers_still_get_the_facebook_hint():
    pairs = [("tags-eu.tiqcdn.com", 5), ("www.facebook.com", 5)]
    assert seen_with(pairs) == {"tags-eu.tiqcdn.com": ["Facebook"]}


def key_and_name(domain):
    group = group_domain(domain)
    return {"key": group["key"], "name": group["name"]}


def test_two_netflix_domains_share_a_group():
    a = key_and_name("logs.dradis.netflix.com")
    b = key_and_name("ipv4-c179-fra002-ix.1.oca.nflxvideo.net")
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
    assert key_and_name("a1051.dscg4.akamai.net") == {
        "key": "domain:akamai.net", "name": "akamai.net"}
    assert key_and_name("tags-eu.tiqcdn.com") == {
        "key": "domain:tiqcdn.com", "name": "tiqcdn.com"}


def test_unknown_names_group_by_registrable_domain():
    assert key_and_name("a.b.somesmallsite.co.uk") == {
        "key": "domain:somesmallsite.co.uk", "name": "somesmallsite.co.uk"}


# Two-part public suffixes. The registered name sits left of them so two sites never merge.
TWO_PART_SUFFIXES = [
    "co.kr", "or.kr", "com.ar", "com.pl", "co.at", "or.at", "com.co", "com.pe", "com.ve",
    "co.ve", "com.ec", "com.uy", "com.pk", "com.bd", "com.ng", "com.eg", "com.sa", "co.ke",
    "com.gh", "co.tz", "net.cn", "org.cn", "gov.cn", "edu.au", "gov.au", "ac.nz", "govt.nz",
    "ac.jp", "go.jp", "gr.jp", "com.pt", "com.gr", "com.ro", "com.es", "com.fr", "co.hu",
    "com.ru",
]
# Hosting suffixes where every tenant is a different owner so the tenant label stays.
TENANT_SUFFIXES = [
    "github.io", "gitlab.io", "herokuapp.com", "vercel.app", "netlify.app", "pages.dev",
    "workers.dev", "web.app", "firebaseapp.com", "appspot.com", "azurewebsites.net",
    "blogspot.com", "wordpress.com", "onrender.com", "fly.dev", "myshopify.com",
    "cloudfront.net", "s3.amazonaws.com", "execute-api.amazonaws.com", "elb.amazonaws.com",
    "compute.amazonaws.com", "trafficmanager.net", "azureedge.net", "akamaized.net",
    "edgekey.net", "fastly.net", "b-cdn.net",
]


@pytest.mark.parametrize("suffix", TWO_PART_SUFFIXES)
def test_names_under_a_two_part_suffix_keep_their_registered_name(suffix):
    assert registrable_domain("www.alpha." + suffix) == "alpha." + suffix
    assert registrable_domain("alpha." + suffix) == "alpha." + suffix
    alpha = key_and_name("www.alpha." + suffix)
    beta = key_and_name("www.beta." + suffix)
    assert alpha == {"key": "domain:alpha." + suffix, "name": "alpha." + suffix}
    assert alpha["key"] != beta["key"]


@pytest.mark.parametrize("suffix", TENANT_SUFFIXES)
def test_tenants_of_a_shared_host_are_different_groups(suffix):
    alpha = key_and_name("api.alpha." + suffix)
    beta = key_and_name("api.beta." + suffix)
    assert alpha == {"key": "domain:alpha." + suffix, "name": "alpha." + suffix}
    assert beta["key"] == "domain:beta." + suffix
    assert group_domain("alpha." + suffix)["key"] == alpha["key"]


@pytest.mark.parametrize("suffix", TWO_PART_SUFFIXES + TENANT_SUFFIXES)
def test_a_name_that_is_only_a_suffix_is_not_grouped_with_anything(suffix):
    assert key_and_name(suffix) == {"key": "domain:" + suffix, "name": suffix}
    assert registrable_domain(suffix) == suffix


def test_the_korean_shops_from_the_review_are_different_groups():
    coupang = group_domain("www.coupang.co.kr")
    naver = group_domain("www.naver.co.kr")
    assert coupang["key"] == "domain:coupang.co.kr"
    assert naver["key"] == "domain:naver.co.kr"


def test_two_github_pages_and_two_heroku_apps_are_different_groups():
    assert group_domain("a.github.io")["key"] == "domain:a.github.io"
    assert group_domain("b.github.io")["key"] == "domain:b.github.io"
    assert group_domain("foo.herokuapp.com")["key"] != group_domain("bar.herokuapp.com")["key"]


def test_deeper_names_under_a_tenant_still_belong_to_that_tenant():
    assert registrable_domain("a.b.c.herokuapp.com") == "c.herokuapp.com"
    assert registrable_domain("bucket.s3.amazonaws.com") == "bucket.s3.amazonaws.com"
    assert registrable_domain("x.bucket.s3.amazonaws.com") == "bucket.s3.amazonaws.com"
    assert registrable_domain("d111abcdef8.cloudfront.net") == "d111abcdef8.cloudfront.net"


def test_the_longest_matching_suffix_decides_for_hosting_under_amazonaws():
    # amazonaws.com itself is not a tenant suffix so other names keep two labels.
    assert registrable_domain("sqs.us-east-1.amazonaws.com") == "amazonaws.com"
    assert group_domain("x.execute-api.amazonaws.com")["key"] == "domain:x.execute-api.amazonaws.com"


def test_a_known_label_still_wins_over_the_registrable_grouping():
    assert group_domain("logs.dradis.netflix.com")["key"] == "label:netflix"
    assert group_domain("www.google.com")["key"] == "label:google"
    assert group_domain("api.header.immobilienscout24.de")["key"] == "label:immoscout24"


def test_an_unknown_tenant_is_labelled_with_its_tenant_name():
    label = label_domain("foo.herokuapp.com")
    assert (label.known, label.name) == (False, "foo.herokuapp.com")


@pytest.mark.parametrize(
    ("domain", "kind"),
    [
        ("logs.dradis.netflix.com", "app"),
        ("config.extension.grammarly.com", "app"),
        ("www.google.com", "system"),
        ("connectivitycheck.gstatic.com", "system"),
        ("www.msftncsi.com", "system"),
        ("gateway.icloud.com", "system"),
        ("10000.mt.eu.iot.dreame.tech", "iot"),
        ("a1051.dscg4.akamai.net", "cdn"),
        ("d111abcdef8.cloudfront.net", "cdn"),
        ("tags-eu.tiqcdn.com", "tracker"),
        ("cdn.somesmallsite.io", "unknown"),
        ("a.b.somesmallsite.co.uk", "unknown"),
        ("co.kr", "unknown"),
    ],
)
def test_the_group_carries_the_label_kind_of_the_name(domain, kind):
    assert group_domain(domain)["kind"] == kind


def test_the_group_of_an_app_is_the_whole_object():
    assert group_domain("logs.dradis.netflix.com") == {
        "key": "label:netflix", "name": "Netflix", "kind": "app"}
    assert group_domain("tags-eu.tiqcdn.com") == {
        "key": "domain:tiqcdn.com", "name": "tiqcdn.com", "kind": "tracker"}


# What survives when one grouping rule is taken away. System and iot names group by their label
# and a tie in the query count breaks by the name without looking at the case.
def test_system_names_group_by_their_label():
    assert key_and_name("www.google.com") == {"key": "label:google", "name": "Google"}
    assert key_and_name("clients4.google.com")["key"] == "label:google"
    assert key_and_name("gateway.icloud.com") == {"key": "label:icloud", "name": "iCloud"}
    assert key_and_name("init.itunes.apple.com")["key"] == "label:apple"
    assert key_and_name("a.apple.com")["key"] == "label:apple"
    assert key_and_name("connectivitycheck.gstatic.com")["key"] == "label:google"
    assert key_and_name("www.msftncsi.com") == {"key": "label:windows", "name": "Windows"}


def test_iot_names_group_by_their_label():
    assert key_and_name("10000.mt.eu.iot.dreame.tech") == {
        "key": "label:dreame", "name": "Dreame"}
    assert key_and_name("other.dreame.tech")["key"] == "label:dreame"
    assert key_and_name("a.meethue.com") == {"key": "label:philips hue", "name": "Philips Hue"}


def test_cdn_and_tracker_names_never_group_by_their_label():
    # Two sites behind one CDN or one tag manager are not one app.
    assert key_and_name("a.akamaihd.net")["key"] == "domain:akamaihd.net"
    assert key_and_name("tags.tiqcdn.com")["key"] == "domain:tiqcdn.com"
    assert key_and_name("www.google-analytics.com")["key"] == "domain:google-analytics.com"

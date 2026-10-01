import pytest

from jittersim.config.domain_labels import (
    LABEL_KINDS,
    TABLE_SOURCE,
    label_domain,
    registrable_domain,
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

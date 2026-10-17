"""The rules that decide whether a new private address is a saved device that came back."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from jittersim.engine.address_follow import (
    ASK_POINTS,
    Arrival,
    SavedIdentity,
    build_identities,
    describe,
    find_suggestions,
    is_generic_hostname,
    is_private_address,
    qualifies,
    score,
)

NOW = 1_000_000
OLD = "aa:bb:cc:00:00:01"  # a fixed vendor address
NEW = "02:11:22:33:44:55"  # a private address
OTHER_NEW = "0a:11:22:33:44:66"


def dad(**changes):
    values = dict(mac_address=OLD, name="Dad's phone", device_id="dad",
                  ipv4_address="192.168.50.20", hostname="dads-iphone", last_seen=NOW - 300)
    values.update(changes)
    return SavedIdentity(**values)


def arrival(**changes):
    values = dict(mac_address=NEW, hostname="Dads-iPhone", ipv4_address="192.168.50.20",
                  connected_seconds=120, ipv4_verified=True)
    values.update(changes)
    return Arrival(**values)


def suggest(identities, arrivals, present=(), **kwargs):
    present = set(present) | {item.mac_address for item in arrivals}
    return find_suggestions(identities, arrivals, present=present, now=NOW, **kwargs)


# ---- addresses and host names -------------------------------------------------------------


@pytest.mark.parametrize("mac, private", [
    ("02:00:00:00:00:01", True), ("06:00:00:00:00:01", True), ("0a:00:00:00:00:01", True),
    ("fe:00:00:00:00:01", True), ("00:11:22:33:44:55", False), ("aa:bb:cc:dd:ee:ff", True),
    ("a8:bb:cc:dd:ee:ff", False), ("03:00:00:00:00:01", False), ("not a mac", False), ("", False),
])
def test_private_addresses_have_the_locally_administered_bit(mac, private):
    assert is_private_address(mac) is private


@pytest.mark.parametrize("name", [
    "iPhone", "iphone-2", "iPhone (3)", "android", "android-1a2b3c4d5e6f", "Galaxy-S23",
    "localhost", "MacBook-Air", "macbook-pro", "Pixel-7a", "", "*", "ab", "3c22fb6a1d9e",
    "DESKTOP-4KQ1J7P", "raspberrypi", "Chromecast",
])
def test_common_host_names_are_generic(name):
    assert is_generic_hostname(name)


@pytest.mark.parametrize("name", ["Dads-iPhone", "Sams-MacBook-Air", "aeromac", "kitchen-ipad",
                                  "Lenas-Galaxy"])
def test_personal_host_names_are_distinctive(name):
    assert not is_generic_hostname(name)


# ---- scoring -------------------------------------------------------------------------------


def test_same_distinctive_name_same_address_and_good_timing_is_strong():
    evidence = score(dad(), arrival(), now=NOW)
    assert evidence.reasons == ("hostname", "ipv4", "timing")
    assert evidence.points == 7 and evidence.strong
    assert qualifies(evidence)


def test_distinctive_name_alone_asks_but_is_not_strong():
    evidence = score(dad(last_seen=None), arrival(ipv4_address="192.168.50.99"), now=NOW)
    assert evidence.reasons == ("hostname",) and evidence.points == 4
    assert qualifies(evidence) and not evidence.strong


def test_a_generic_name_and_the_same_address_only_ask():
    evidence = score(dad(hostname="iphone", name="iPhone", last_seen=None),
                     arrival(hostname="iPhone"), now=NOW)
    assert evidence.reasons == ("generic_hostname", "ipv4")
    assert evidence.points == ASK_POINTS and not evidence.strong


def test_a_generic_name_never_also_counts_as_the_saved_name():
    # A guest's "iPhone" must not reach the threshold because the saved device is called iPhone.
    evidence = score(dad(hostname="iphone", name="iPhone", last_seen=None),
                     arrival(hostname="iPhone", ipv4_address="192.168.50.99"), now=NOW)
    assert evidence.reasons == ("generic_hostname",)
    assert not qualifies(evidence)


def test_the_saved_name_matches_a_host_name_without_punctuation():
    named = arrival(hostname="Dads-Phone", ipv4_address=None)
    evidence = score(dad(hostname="", last_seen=None), named, now=NOW)
    assert evidence.reasons == ("name",)
    assert not qualifies(evidence)  # 2 points only
    evidence = score(dad(hostname=""), named, now=NOW)
    assert evidence.reasons == ("name", "timing") and qualifies(evidence)


def test_no_host_name_needs_the_same_address_and_timing_and_never_is_strong():
    evidence = score(dad(hostname=""), arrival(hostname=""), now=NOW)
    assert evidence.reasons == ("ipv4", "timing") and qualifies(evidence)
    assert not evidence.strong
    evidence = score(dad(hostname=""), arrival(hostname="", ipv4_address="192.168.50.99"), now=NOW)
    assert evidence.reasons == ("timing",) and not qualifies(evidence)


def test_an_unconfirmed_address_earns_nothing():
    evidence = score(dad(hostname="", last_seen=None), arrival(hostname="", ipv4_verified=False),
                     now=NOW)
    assert evidence.points == 0


def test_both_online_together_rules_the_pair_out():
    # The old address was seen five minutes after the new one joined.
    assert score(dad(last_seen=NOW - 60), arrival(connected_seconds=360), now=NOW) is None
    # Within the sighting margin it still counts as leaving before.
    assert score(dad(last_seen=NOW - 60), arrival(connected_seconds=120), now=NOW) is not None


def test_timing_is_only_a_point_inside_fifteen_minutes():
    near = score(dad(last_seen=NOW - 120 - 14 * 60), arrival(), now=NOW)
    far = score(dad(last_seen=NOW - 120 - 16 * 60), arrival(), now=NOW)
    assert "timing" in near.reasons and "timing" not in far.reasons
    unknown = score(dad(), arrival(connected_seconds=None), now=NOW)
    assert "timing" not in unknown.reasons


def test_a_last_seen_in_the_future_is_ignored():
    evidence = score(dad(last_seen=NOW + 3600), arrival(), now=NOW)
    assert evidence is not None and "timing" not in evidence.reasons


def test_a_protected_device_needs_a_distinctive_name():
    evidence = score(dad(hostname="", protected=True), arrival(hostname=""), now=NOW)
    assert qualifies(evidence) and not qualifies(evidence, protected=True)
    evidence = score(dad(protected=True), arrival(), now=NOW)
    assert qualifies(evidence, protected=True)


def test_evidence_is_described_in_plain_words():
    sentences = describe(score(dad(), arrival(), now=NOW), hostname="dads-iphone")
    assert sentences == [
        'It sends the same device name "dads-iphone".',
        "It got the same IP address.",
        "It joined shortly after the old address left.",
    ]


# ---- matching and the edge cases ----------------------------------------------------------


def test_a_device_that_came_back_is_suggested():
    [item] = suggest([dad()], [arrival()])
    assert (item.old.mac_address, item.new.mac_address) == (OLD, NEW)


def test_a_device_that_is_still_connected_is_never_suggested():
    assert suggest([dad()], [arrival()], present=[OLD]) == []


def test_the_old_address_returning_ends_the_suggestion():
    assert suggest([dad()], [arrival()]) != []
    assert suggest([dad()], [arrival(), Arrival(OLD)], present=[OLD]) == []


def test_a_fixed_new_address_is_never_suggested():
    assert suggest([dad()], [arrival(mac_address="a8:11:22:33:44:55")]) == []


def test_a_new_address_that_a_saved_entry_knows_is_not_new():
    known = SavedIdentity(NEW, "Tablet", labelled=True)
    assert suggest([dad(), known], [arrival()]) == []


def test_a_really_new_device_matches_nothing():
    assert suggest([dad()], [arrival(hostname="guest-laptop", ipv4_address="192.168.50.99",
                                     connected_seconds=None)]) == []


def test_a_guest_with_a_generic_name_and_a_new_address_matches_nothing():
    saved = dad(hostname="iphone", name="iPhone")
    assert suggest([saved], [arrival(hostname="iPhone", ipv4_address="192.168.50.99",
                                     connected_seconds=None)]) == []


def test_two_saved_devices_that_fit_equally_give_no_guess():
    twin = dad(mac_address="aa:bb:cc:00:00:02", device_id="mum", name="Mum's phone")
    assert suggest([dad(), twin], [arrival()]) == []


def test_one_saved_device_that_fits_two_new_addresses_gives_no_guess():
    assert suggest([dad()], [arrival(), arrival(mac_address=OTHER_NEW)]) == []


def test_dismissing_one_of_two_candidates_does_not_make_the_other_a_guess():
    twin = dad(mac_address="aa:bb:cc:00:00:02", device_id="mum", name="Mum's phone")
    assert suggest([dad(), twin], [arrival()], dismissed={(OLD, NEW)}) == []


def test_a_dismissed_pair_is_not_suggested():
    assert suggest([dad()], [arrival()], dismissed={(OLD, NEW)}) == []


def test_a_vetoed_pair_takes_no_part_at_all():
    twin = dad(mac_address="aa:bb:cc:00:00:02", device_id="mum", name="Mum's phone")
    # The twin was seen online together with the new address, so only Dad is left.
    [item] = suggest([dad(), twin], [arrival()], vetoed={("aa:bb:cc:00:00:02", NEW)})
    assert item.old.mac_address == OLD


def test_two_devices_that_changed_together_are_matched_one_to_one():
    mum = SavedIdentity("aa:bb:cc:00:00:02", "Mum's laptop", device_id="mum",
                        ipv4_address="192.168.50.30", hostname="mums-laptop", last_seen=NOW - 200)
    items = suggest([dad(), mum], [
        arrival(),
        arrival(mac_address=OTHER_NEW, hostname="Mums-Laptop", ipv4_address="192.168.50.30"),
    ])
    assert {(item.old.mac_address, item.new.mac_address) for item in items} == {
        (OLD, NEW), ("aa:bb:cc:00:00:02", OTHER_NEW)}


def test_a_device_without_any_host_name_is_suggested_only_with_address_and_timing():
    saved = dad(hostname="")
    [item] = suggest([saved], [arrival(hostname="")])
    assert not item.evidence.strong
    assert suggest([saved], [arrival(hostname="", ipv4_address="192.168.50.99")]) == []


def test_identities_merge_device_protection_label_and_sighting_by_address():
    identities = build_identities(
        devices=[("dad", "Client 192.168.50.20", "192.168.50.20", OLD), ("x", "No mac", "1", None)],
        protected={OLD: "Dad protected", "02:00:00:00:00:09": "Printer"},
        labels={OLD: "Dad's phone"},
        sightings={OLD: ("dads-iphone", "192.168.50.20", NOW - 10)},
    )
    assert [identity.mac_address for identity in identities] == ["02:00:00:00:00:09", OLD]
    printer, saved = identities
    assert (printer.name, printer.protected, printer.device_id) == ("Printer", True, None)
    assert (saved.name, saved.device_id, saved.protected, saved.labelled, saved.hostname) == (
        "Dad's phone", "dad", True, True, "dads-iphone")


# ---- the no-guess rule for any input ------------------------------------------------------

HOSTS = st.sampled_from(["", "iPhone", "android", "dads-iphone", "mums-laptop", "kitchen-ipad"])
ADDRESSES = st.sampled_from([None, "192.168.50.20", "192.168.50.30", "192.168.50.40"])
SEEN = st.one_of(st.none(), st.integers(min_value=NOW - 3600, max_value=NOW))


@st.composite
def worlds(draw):
    count = draw(st.integers(min_value=1, max_value=5))
    identities = [
        SavedIdentity(
            mac_address=f"aa:bb:cc:00:00:{index:02x}", name=draw(HOSTS) or f"Device {index}",
            device_id=f"d{index}", ipv4_address=draw(ADDRESSES), hostname=draw(HOSTS),
            last_seen=draw(SEEN), protected=draw(st.booleans()),
        )
        for index in range(count)
    ]
    arrivals = [
        Arrival(
            mac_address=f"02:00:00:00:01:{index:02x}", hostname=draw(HOSTS),
            ipv4_address=draw(ADDRESSES), ipv4_verified=draw(st.booleans()),
            connected_seconds=draw(st.one_of(st.none(), st.integers(0, 3600))),
        )
        for index in range(draw(st.integers(min_value=1, max_value=4)))
    ]
    return identities, arrivals


@settings(max_examples=300, deadline=None)
@given(worlds())
def test_every_suggestion_is_a_unique_mutual_match(world):
    identities, arrivals = world
    items = suggest(identities, arrivals)
    for item in items:
        over_for_new = [
            identity for identity in identities
            if qualifies(score(identity, item.new, now=NOW), protected=identity.protected)
        ]
        over_for_old = [
            new for new in arrivals
            if qualifies(score(item.old, new, now=NOW), protected=item.old.protected)
        ]
        assert over_for_new == [item.old] and over_for_old == [item.new]
    # One saved device and one new address appear at most once.
    assert len({item.old.mac_address for item in items}) == len(items)
    assert len({item.new.mac_address for item in items}) == len(items)


@settings(max_examples=300, deadline=None)
@given(worlds())
def test_a_copy_of_a_suggested_device_removes_its_suggestion(world):
    identities, arrivals = world
    for item in suggest(identities, arrivals):
        copy = SavedIdentity(**{**item.old.__dict__, "mac_address": "aa:bb:cc:ff:ff:ff",
                                "device_id": "copy"})
        again = suggest(identities + [copy], arrivals)
        assert all(other.new.mac_address != item.new.mac_address for other in again)

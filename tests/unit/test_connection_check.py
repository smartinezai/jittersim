"""Connection check: the statistics, the expected ranges, the verdicts and the burst loop."""

import csv
import io
import json
import math
import socket
import time

import pytest

from jittersim import connection_check as check
from jittersim.config.speed_tests import MAX_JSON_BYTES
from jittersim.speed_test import IcmpProber, NoProber, icmp_checksum

PHONE = "dc:1e:d5:3e:97:4c"


def records(count=100, *, lost=(), rtt=lambda index: 100.0, copies=lambda index: 1,
            arrival=None):
    """Probe records in send order. `lost` are the indexes without an answer."""

    lost = set(lost)
    result, order = [], 0
    for index in range(count):
        if index in lost:
            result.append({"rtt_ms": None, "copies": 0, "arrival": None})
            continue
        position = order if arrival is None else arrival(index)
        order += 1
        result.append({"rtt_ms": rtt(index), "copies": copies(index), "arrival": position})
    return result


def setting(**values):
    base = {"delay_ms": 0, "jitter_ms": 0, "loss_percent": 0, "duplicate_percent": 0,
            "reorder_percent": 0, "corrupt_percent": 0, "bandwidth_kbit": None}
    base.update(values)
    return base


def summary(recs, **changes):
    values = {"planned": len(recs), "source": "device", "impairment": setting(),
              "burst_packets": 0, "service_rules": False}
    values.update(changes)
    return check.build_summary(recs, **values)


def metric(result, name):
    return next(row for row in result["metrics"] if row["id"] == name)


# The interval.

def test_wilson_interval_matches_hand_computed_values():
    low, high = check.wilson_interval(0, 100, 1.96)
    assert low == 0.0 and high == pytest.approx(0.03699, abs=1e-4)
    low, high = check.wilson_interval(50, 100, 1.96)
    assert (low, high) == (pytest.approx(0.4038, abs=1e-4), pytest.approx(0.5962, abs=1e-4))
    low, high = check.wilson_interval(100, 100, 1.96)
    assert low == pytest.approx(0.9630, abs=1e-3) and high == 1.0
    assert check.wilson_interval(0, 0, 1.96) == (0.0, 1.0)
    # A wider level gives a wider interval.
    narrow, wide = check.wilson_interval(10, 100, 1.96), check.wilson_interval(10, 100, 2.576)
    assert wide[0] < narrow[0] and wide[1] > narrow[1]


def binomial(count, events, share):
    return math.comb(count, events) * share ** events * (1 - share) ** (count - events)


@pytest.mark.parametrize("share", [0.05, 0.10, 0.20, 0.50])
def test_a_correct_independent_loss_is_rarely_called_less_or_more(share):
    # Exact binomial tail sums for 100 independent probes: the chance that a correct profile is
    # accused. It is not zero (the interval has approximate coverage) but it is small.
    band = check.expected_loss_range(share, 0.0, 0.0)
    accused = matches = 0.0
    for events in range(101):
        status, _, _ = check.share_status(events, 100, band)
        chance = binomial(100, events, share)
        accused += chance if status in ("less", "more") else 0.0
        matches += chance if status in ("matches", "inconclusive") else 0.0
    assert accused < 0.02, accused
    assert matches > 0.9, matches


def test_a_healthy_link_with_two_percent_loss_passes_a_zero_loss_profile_most_of_the_time():
    band = check.expected_loss_range(0.0, 0.0, 0.0)
    accused = sum(
        binomial(100, events, 0.02) for events in range(101)
        if check.share_status(events, 100, band)[0] in ("less", "more"))
    assert accused < 0.02  # 6 or more lost of 100 happens about 1.5 percent of the time


@pytest.mark.parametrize("events, total, band, expected", [
    (10, 100, (0.10, 0.12), "matches"),
    (0, 100, (0.10, 0.12), "less"),
    (30, 100, (0.10, 0.12), "more"),
    (0, 100, (0.0, 0.02), "matches"),
    (0, 100, (0.03, 0.05), "inconclusive"),
    (5, 100, (0.05, 0.07), "matches"),
    (3, 100, (0.05, 0.07), "inconclusive"),
    (0, 20, (0.50, 0.52), "less"),
])
def test_share_status(events, total, band, expected):
    assert check.share_status(events, total, band)[0] == expected


def test_a_result_between_the_two_levels_is_borderline_with_its_direction():
    status, direction, _ = check.share_status(0, 100, (0.05, 0.07))
    assert (status, direction) == ("borderline", "low")
    # Against a healthy link (up to 2 percent): 4 lost is fine, 5 is close, 6 is clearly more.
    assert check.share_status(4, 100, (0.0, 0.02))[0] == "matches"
    status, direction, _ = check.share_status(5, 100, (0.0, 0.02))
    assert (status, direction) == ("borderline", "high")
    assert check.share_status(6, 100, (0.0, 0.02))[0] == "more"


def test_bursty_loss_widens_the_interval():
    assert check.burst_inflation(0.10, 0) == 1.0
    assert check.burst_inflation(0.10, 1) == 1.0
    assert check.burst_inflation(0.10, 4) == pytest.approx(6.2, abs=0.05)
    # Runs this short cannot lose this much: the renderer leaves the good state at once.
    assert check.burst_inflation(0.9, 2) == 1.0
    plain = check.share_status(14, 100, (0.10, 0.12))
    bursty = check.share_status(14, 100, (0.10, 0.12), inflation=6.2)
    assert bursty[2][1] - bursty[2][0] > plain[2][1] - plain[2][0]


def test_probes_needed_follows_the_verdict_rule():
    assert check.probes_needed(0.10) == 20
    assert check.probes_needed(0.05) == 84
    assert check.probes_needed(0.045) == 121
    assert check.probes_needed(0.04) == 189
    assert check.probes_needed(0.03) is None
    assert check.probes_needed(0.0) is None
    assert check.probes_needed(0.10, inflation=check.burst_inflation(0.10, 4)) == 73
    # With fewer probes than needed the set value cannot be told from a healthy link.
    needed = check.probes_needed(0.05)
    low, _ = check.wilson_interval(0.05 * needed, needed, check.Z_MATCH)
    assert low > check.WIFI_SHARE_ALLOWANCE


# What the profile should give.

def test_loss_range_with_duplication_and_corruption():
    assert check.expected_loss_range(0.10, 0.0, 0.0) == pytest.approx((0.10, 0.12))
    # A duplicated and lost packet still leaves one copy.
    assert check.expected_loss_range(0.10, 0.5, 0.0) == pytest.approx((0.05, 0.07))
    # Corruption adds 90 to 100 percent of itself to the loss.
    assert check.expected_loss_range(0.10, 0.0, 0.20) == pytest.approx(
        (1 - 0.9 * 0.82, 1 - 0.9 * 0.8 + 0.02))
    assert check.expected_loss_range(1.0, 0.0, 0.0) == (1.0, 1.0)
    # With duplication every copy needs its own corruption: both are damaged only 25 percent
    # of the time at 50 percent corruption and 100 percent duplication.
    assert check.expected_loss_range(0.0, 1.0, 0.5) == pytest.approx((0.2025, 0.27))
    # Without duplication or loss the unanswered share is the corruption.
    assert check.unanswered_share(0.0, 0.0, 0.3) == pytest.approx(0.3)
    assert check.unanswered_share(0.2, 0.0, 0.0) == pytest.approx(0.2)
    assert check.unanswered_share(0.2, 0.5, 0.0) == pytest.approx(0.1)


def test_duplicate_range():
    assert check.expected_duplicate_range(0.0, 0.10) == pytest.approx((0.10, 0.12))
    assert check.expected_duplicate_range(0.0, 0.0) == pytest.approx((0.0, 0.02))
    low, high = check.expected_duplicate_range(0.5, 0.5)
    assert low == pytest.approx(0.125 / 0.75) and high == pytest.approx(0.52)


def test_delay_range_and_window_limits():
    assert check.expected_delay_range(100, 20, None) == (80, 100 + 20 + 30)
    low, high = check.expected_delay_range(50, 0, 250)
    assert low == 50 and high == pytest.approx(50 + check.FRAME_BYTES * 8 / 250 + 30)
    assert check.expected_delay_range(1000, 0, None)[1] == 1000 + 300
    assert check.window_limit(100, 20, 1000) is None
    assert "longer than the check waits" in check.window_limit(4000, 600, None)
    assert check.window_limit(4500, 0, None) is None
    assert "lower than the probes need" in check.window_limit(0, 0, 1)
    assert check.window_limit(0, 0, 16) is None


def test_the_simulation_is_repeatable_and_matches_the_model():
    first = check.simulated_timing(100, 20, 0.0)
    assert first == check.simulated_timing(100, 20, 0.0)
    assert first["jitter_ms"] == pytest.approx(2 * 20 / 3, abs=0.6)
    assert first["reorder_share"] == 0.0
    # Packets that skip a 100 ms delay alternate with delayed ones: |change| is 100 or 0.
    alternating = check.simulated_timing(100, 0, 0.25)
    assert alternating["jitter_ms"] == pytest.approx(2 * 0.25 * 0.75 * 100, abs=2.0)
    # No later probe is sent within a delay of 100 ms, so nothing can overtake.
    assert alternating["reorder_share"] == 0.0
    # With 150 ms the next probe can overtake: (1-R) * (1-(1-R)^1).
    assert check.simulated_timing(150, 0, 0.25)["reorder_share"] == pytest.approx(
        0.75 * 0.25, abs=0.015)
    # With 300 ms two later probes can (the third arrives at the same moment): (1-R) * (1-(1-R)^2).
    assert check.simulated_timing(300, 0, 0.10)["reorder_share"] == pytest.approx(
        0.9 * (1 - 0.9 ** 2), abs=0.02)
    # A jitter larger than the probe spacing keeps delayed packets in order, so the average
    # change is smaller than 2J/3.
    assert check.simulated_timing(100, 100, 0.0)["jitter_ms"] < 2 * 100 / 3


def test_the_grace_time_follows_the_delay():
    assert check.grace_seconds({"delay_ms": 0, "jitter_ms": 0}, shaped=True) == 1.0
    assert check.grace_seconds({"delay_ms": 100, "jitter_ms": 20}, shaped=True) == pytest.approx(1.24)
    assert check.grace_seconds({"delay_ms": 60000, "jitter_ms": 0}, shaped=True) == 10.0
    assert check.grace_seconds(None, shaped=False) == check.PLAIN_GRACE_SECONDS
    assert check.grace_seconds(None, shaped=True) == check.UNKNOWN_GRACE_SECONDS
    assert check.waits_for_late_copies({"duplicate_percent": 5})
    assert not check.waits_for_late_copies({"duplicate_percent": 0})
    assert not check.waits_for_late_copies(None)


# The statistics of a burst.

def test_probe_statistics():
    recs = records(10, lost={2, 3}, rtt=lambda index: 10.0 + index,
                   copies=lambda index: 2 if index in (0, 7) else 1)
    stats = check.probe_statistics(recs)
    assert (stats["sent"], stats["received"], stats["lost"]) == (10, 8, 2)
    assert (stats["answered_twice"], stats["extra_copies"]) == (2, 2)
    assert (stats["rtt_min_ms"], stats["rtt_max_ms"]) == (10.0, 19.0)
    assert stats["rtt_median_ms"] == 15.0
    # Adjacent pairs are 0-1, 4-5, 5-6, 6-7, 7-8 and 8-9, each a change of 1 ms.
    assert stats["jitter_pairs"] == 6 and stats["jitter_ms"] == 1.0
    assert check.probe_statistics([])["rtt_median_ms"] is None


def test_reordering_counts_first_answers_that_arrive_after_a_later_probe():
    # Probe 1 is answered after probe 2 and probe 3: two answers arrived before it, one of them
    # (probe 3) was sent later, so only probe 1 is out of order.
    arrival = {0: 0, 1: 3, 2: 1, 3: 2, 4: 4}
    stats = check.probe_statistics(records(5, arrival=lambda index: arrival[index]))
    assert stats["reordered"] == 1
    # A late copy of a probe is a copy, not an arrival: it never counts as reordered.
    recs = records(5, copies=lambda index: 2 if index == 0 else 1)
    assert check.probe_statistics(recs)["reordered"] == 0
    # Records without an arrival order (a fake prober) count nothing.
    assert check.probe_statistics([{"rtt_ms": 5.0, "copies": 1, "arrival": None}] * 3
                                  )["reordered"] == 0


# The verdicts.

def test_a_correct_profile_matches():
    recs = records(100, lost=range(0, 100, 10), rtt=lambda index: 100.0 + (index % 5))
    result = summary(recs, impairment=setting(delay_ms=100, loss_percent=10))
    assert result["verdict"] == "matches"
    assert metric(result, "loss")["status"] == "matches"
    assert metric(result, "delay")["status"] == "matches"
    assert result["shaped"] is True
    assert any("download side" in note for note in result["notes"])


def test_a_profile_that_is_not_in_effect_is_less_than_set():
    result = summary(records(100, rtt=lambda index: 4.0),
                     impairment=setting(delay_ms=100, loss_percent=20))
    assert result["verdict"] == "less"
    assert metric(result, "loss")["status"] == "less"
    assert metric(result, "delay")["status"] == "less"
    assert "not fully in effect" in metric(result, "loss")["sentence"]


def test_more_loss_than_set():
    result = summary(records(100, lost=range(0, 100, 5), rtt=lambda index: 4.0))
    assert metric(result, "loss")["status"] == "more"
    assert result["verdict"] == "more"


def test_a_slow_phone_is_extra_delay_not_an_accusation():
    result = summary(records(100, rtt=lambda index: 120.0),
                     impairment=setting(delay_ms=20))
    assert metric(result, "delay")["status"] == "extra"
    assert result["verdict"] == "matches"
    assert "not counted against the profile" in metric(result, "delay")["sentence"]
    assert any("power" in note for note in result["notes"])


def test_less_and_more_together_differ():
    result = summary(records(100, rtt=lambda index: 4.0, copies=lambda index: 2 if index < 25 else 1),
                     impairment=setting(loss_percent=20))
    assert metric(result, "loss")["status"] == "less"
    assert metric(result, "duplicate")["status"] == "more"
    assert result["verdict"] == "differs"


def test_a_small_set_loss_cannot_be_told_from_a_healthy_link():
    result = summary(records(100), impairment=setting(loss_percent=3))
    assert metric(result, "loss")["status"] == "inconclusive"
    assert result["verdict"] == "inconclusive"
    assert result["probes_needed"] is None and result["probes_needed_over_max"] is True
    assert any("More than 300 probes" in note for note in result["notes"])
    result = summary(records(100), impairment=setting(loss_percent=5))
    assert metric(result, "loss")["status"] == "borderline"
    assert result["verdict"] == "borderline"
    assert result["probes_needed"] == 84


def test_zero_replies_is_no_answer_unless_the_profile_explains_it():
    result = summary(records(100, lost=range(100)), impairment=setting(loss_percent=30))
    assert result["verdict"] == "no-answer"
    assert check.NO_ANSWER_NOTE in result["notes"]
    assert metric(result, "loss")["status"] == "more"
    assert summary(records(100, lost=range(100)))["verdict"] == "no-answer"
    explained = summary(records(100, lost=range(100)), impairment=setting(loss_percent=100))
    assert explained["verdict"] == "matches"


def test_a_shared_profile_is_not_comparable_and_a_protected_device_is_a_baseline():
    shared = summary(records(100, lost={1, 2}), source="everyone", impairment=None)
    assert shared["verdict"] == "not-comparable"
    assert shared["shaped"] is False and shared["expected"] is None
    assert check.SHARED_NOTE in shared["notes"]
    assert all(row["status"] == "not-judged" for row in shared["metrics"])
    assert summary(records(100), source="everyone-else", impairment=None)[
        "verdict"] == "not-comparable"
    protected = summary(records(100), source="none", protected=True, impairment=None)
    assert protected["verdict"] == "baseline" and check.BASELINE_NOTE in protected["notes"]
    assert summary(records(100), source="none", impairment=None)["verdict"] == "baseline"
    # A protected device wins over a stale device source: nothing shapes it.
    assert summary(records(100), protected=True)["verdict"] == "baseline"


def test_service_rules_withhold_the_verdict_but_keep_the_numbers():
    recs = records(100, lost=range(0, 100, 5))
    for rules, verdict in ((True, "service-rules"), (None, "service-rules-unknown")):
        result = summary(recs, impairment=setting(loss_percent=20), service_rules=rules)
        assert result["verdict"] == verdict
        assert metric(result, "loss")["status"] == "not-judged"
        assert result["probes"]["lost"] == 20


def test_a_change_or_an_unverified_end_gives_no_verdict():
    recs = records(100)
    changed = summary(recs, impairment=setting(loss_percent=20), changed_reason="It changed.")
    assert changed["verdict"] == "changed" and "It changed." in changed["notes"]
    unverified = summary(recs, impairment=setting(loss_percent=20),
                         unverified_reason="Could not confirm.")
    assert unverified["verdict"] == "unverified"
    assert all(row["status"] == "not-judged" for row in unverified["metrics"])
    # A probe sender that could not run says so.
    assert summary(recs, probe_status="unavailable")["verdict"] == "unavailable"


def test_a_profile_beyond_the_window_is_out_of_range():
    result = summary(records(100, lost=range(100)), impairment=setting(delay_ms=6000))
    assert result["verdict"] == "out-of-range"
    assert "longer than the check waits" in result["notes"][0]
    capped = summary(records(100, rtt=lambda index: 900.0), impairment=setting(bandwidth_kbit=2))
    assert capped["verdict"] == "out-of-range"
    assert "lower than the probes need" in capped["notes"][0]


def test_delay_and_jitter_are_not_judged_when_reordering_is_set():
    recs = records(100, rtt=lambda index: 5.0)
    result = summary(recs, impairment=setting(delay_ms=200, jitter_ms=10, reorder_percent=25))
    assert metric(result, "delay")["status"] == "not-judged"
    assert metric(result, "jitter")["status"] == "not-judged"
    assert "reorders packets" in metric(result, "delay")["sentence"]


def test_jitter_is_judged_against_the_simulation():
    expected = check.simulated_timing(100, 20, 0.0)["jitter_ms"]
    # Round trips that change by the expected amount from one probe to the next.
    alternate = lambda index: 100.0 + (expected if index % 2 else 0.0)
    result = summary(records(100, rtt=alternate), impairment=setting(delay_ms=100, jitter_ms=20))
    assert metric(result, "jitter")["status"] == "matches"
    flat = summary(records(100, rtt=lambda index: 100.0),
                   impairment=setting(delay_ms=100, jitter_ms=20))
    assert metric(flat, "jitter")["status"] == "less"
    wild = summary(records(100, rtt=lambda index: 100.0 + (90 if index % 2 else 0)),
                   impairment=setting(delay_ms=100, jitter_ms=20))
    assert metric(wild, "jitter")["status"] == "extra"
    # Too few answers in a row cannot give a jitter.
    sparse = summary(records(100, lost=range(0, 100, 2)),
                     impairment=setting(delay_ms=100, jitter_ms=20))
    assert metric(sparse, "jitter")["status"] == "not-judged"


def test_duplicates_are_judged_only_when_the_share_can_be_predicted():
    doubled = lambda index: 2 if index % 10 == 0 else 1
    result = summary(records(100, copies=doubled), impairment=setting(duplicate_percent=10))
    assert metric(result, "duplicate")["status"] == "matches"
    # Bursty loss or corruption makes the clone and the original depend on each other.
    for changes in ({"loss_percent": 10}, {"corrupt_percent": 5}):
        result = summary(records(100, copies=doubled), burst_packets=4,
                         impairment=setting(duplicate_percent=10, **changes))
        assert metric(result, "duplicate")["status"] == "not-judged", changes
    # Independent loss with a known length is fine, an unknown length is not.
    ok = summary(records(100, copies=doubled, lost=range(0, 100, 10)), burst_packets=0,
                 impairment=setting(duplicate_percent=10, loss_percent=10))
    assert metric(ok, "duplicate")["status"] != "not-judged"
    unknown = summary(records(100, copies=doubled, lost=range(0, 100, 10)), burst_packets=None,
                      impairment=setting(duplicate_percent=10, loss_percent=10))
    assert metric(unknown, "duplicate")["status"] == "not-judged"
    assert any("not known" in note for note in unknown["notes"])


def test_reordering_is_judged_when_probes_can_overtake_each_other():
    expected = check.simulated_timing(300, 0, 0.10)["reorder_share"]
    count = round(expected * 100)
    # Probes 0 to count-1 are answered after the one sent right after them.
    arrival = {}
    order = 0
    for index in range(0, 100, 2):
        late = index // 2 < count
        arrival[index] = order + (1 if late else 0)
        arrival[index + 1] = order + (0 if late else 1)
        order += 2
    result = summary(records(100, arrival=lambda index: arrival[index], rtt=lambda index: 5.0),
                     impairment=setting(delay_ms=300, reorder_percent=10))
    assert metric(result, "reorder")["status"] in ("matches", "borderline", "inconclusive")
    # A delay shorter than the probe spacing cannot reorder anything.
    short = summary(records(100), impairment=setting(delay_ms=50, reorder_percent=25))
    assert metric(short, "reorder")["status"] == "not-judged"
    assert "100 ms" in metric(short, "reorder")["sentence"]


def test_corruption_and_the_speed_cap_get_rows_but_no_status():
    result = summary(records(100), impairment=setting(corrupt_percent=5, bandwidth_kbit=250))
    assert metric(result, "corrupt")["status"] == "not-judged"
    assert metric(result, "cap")["status"] == "not-judged"
    assert "Test speed" in metric(result, "cap")["sentence"]
    plain = summary(records(100))
    assert {row["id"] for row in plain["metrics"]} == {
        "loss", "delay", "jitter", "duplicate", "reorder"}


def test_a_profile_that_sets_only_upload_values_is_not_called_a_match():
    # The probes pass the download queue only. A healthy link must not "match" a profile that
    # sets nothing there.
    result = summary(records(100), upload_set=True)
    assert result["verdict"] == "upload-only"
    assert result["verdict_text"].startswith("Not judged")
    assert result["notes"][0] == check.UPLOAD_NOTE
    assert result["notes"].count(check.UPLOAD_NOTE) == 1
    # A profile that sets nothing on either side still describes a healthy link.
    assert summary(records(100))["verdict"] == "matches"
    # A download value is judged as usual, whatever the upload side sets.
    lossy = summary(records(100, lost=range(0, 100, 10)),
                    impairment=setting(loss_percent=10), upload_set=True)
    assert lossy["verdict"] == "matches"
    # A download speed cap keeps its own guidance.
    capped = summary(records(100), impairment=setting(bandwidth_kbit=250), upload_set=True)
    assert capped["verdict"] == "inconclusive"
    # A withheld verdict and a baseline come first.
    assert summary(records(100), upload_set=True, service_rules=True)["verdict"] == (
        "service-rules")
    assert summary(records(100), upload_set=True, source="none", impairment=None)["verdict"] == (
        "baseline")


def test_duplication_with_corruption_does_not_accuse_a_correct_profile():
    # Both copies of a probe must be damaged to lose it: 25 percent at 50 percent corruption.
    result = summary(records(100, lost=range(0, 100, 4)),
                     impairment=setting(duplicate_percent=100, corrupt_percent=50))
    assert metric(result, "loss")["status"] == "matches"
    assert metric(result, "duplicate")["status"] == "not-judged"
    assert result["verdict"] == "matches"


def test_bursty_loss_with_duplication_and_corruption_is_not_judged_for_loss():
    result = summary(records(100), burst_packets=4,
                     impairment=setting(loss_percent=20, duplicate_percent=50, corrupt_percent=5))
    assert metric(result, "loss")["status"] == "not-judged"
    assert "uncertain" in metric(result, "loss")["sentence"]


def test_a_loss_that_answers_are_missing_for_does_not_hide_reordering():
    # With 80 percent loss most answers that would show reordering never come back.
    without = check.simulated_timing(250, 0, 0.5, 0.0)["reorder_share"]
    with_loss = check.simulated_timing(250, 0, 0.5, 0.8)["reorder_share"]
    assert with_loss < without / 2
    band = (check.REORDER_LOW_FACTOR * with_loss,
            min(1.0, check.REORDER_HIGH_FACTOR * with_loss + check.WIFI_SHARE_ALLOWANCE))
    # The seeded reproduction of the review: 61 answers, 4 out of order.
    assert check.share_status(4, 61, band)[0] == "matches"
    # The loss is used by the verdict as well: 60 answers of 300 are enough to judge it.
    result = summary(records(300, lost=[i for i in range(300) if i % 5]),
                     impairment=setting(delay_ms=250, reorder_percent=50, loss_percent=80))
    assert metric(result, "reorder")["status"] in ("less", "borderline", "inconclusive", "matches")
    # With 20 answers of 100 it would expect fewer than 3 reordered, so it says so.
    thin = summary(records(100, lost=[i for i in range(100) if i % 5]),
                   impairment=setting(delay_ms=250, reorder_percent=50, loss_percent=80))
    assert metric(thin, "reorder")["status"] == "not-judged"


def test_jitter_and_reordering_are_not_judged_with_duplication_or_corruption():
    for changes in ({"duplicate_percent": 10}, {"corrupt_percent": 5}):
        result = summary(records(100, rtt=lambda index: 100.0),
                         impairment=setting(delay_ms=100, jitter_ms=20, reorder_percent=0,
                                            loss_percent=0, **changes))
        assert metric(result, "jitter")["status"] == "not-judged", changes
        assert "cannot be compared" in metric(result, "jitter")["sentence"]
    result = summary(records(100), impairment=setting(delay_ms=300, reorder_percent=25,
                                                      duplicate_percent=10))
    assert metric(result, "reorder")["status"] == "not-judged"


def test_a_corruption_only_profile_gets_the_right_guidance():
    # 4 percent of the probes lost against 5 percent corruption: the answer names 121 probes.
    result = summary(records(100, lost=range(4)), impairment=setting(corrupt_percent=5))
    loss = metric(result, "loss")
    assert loss["status"] == "inconclusive"
    assert "at least 121 probes" in loss["sentence"]
    assert result["probes_needed"] == 121
    # Seven lost are clearly more than a healthy link, and not "no more loss than a healthy link".
    clear = summary(records(100, lost=range(7)), impairment=setting(corrupt_percent=5))
    assert metric(clear, "loss")["status"] == "matches"
    assert "healthy" not in metric(clear, "loss")["sentence"]


def test_a_change_is_never_hidden_by_a_baseline_or_a_shared_profile():
    for source, protected in (("none", False), ("everyone", False), ("everyone-else", False),
                              ("none", True)):
        result = summary(records(100), source=source, protected=protected, impairment=None,
                         changed_reason="The device's address changed during the check.")
        assert result["verdict"] == "changed", (source, protected)
        assert "address changed" in result["notes"][0]


def test_a_fault_gives_no_verdict_even_when_every_number_matched():
    recs = records(80, lost=range(0, 80, 10), rtt=lambda index: 100.0)
    result = summary(recs, impairment=setting(delay_ms=100, loss_percent=10),
                     fault_reason="The check stopped after an error.")
    assert result["verdict"] == "fault"
    assert all(row["status"] == "not-judged" for row in result["metrics"])
    assert result["probes"]["lost"] == 8
    assert "stopped after an error" in result["notes"][0]


def test_the_summary_is_small_and_has_no_secrets():
    result = summary(records(300, lost=range(0, 300, 7)), planned=300,
                     impairment=setting(delay_ms=100, jitter_ms=20, loss_percent=10,
                                        duplicate_percent=5, reorder_percent=0,
                                        corrupt_percent=2, bandwidth_kbit=500),
                     burst_packets=3)
    assert len(json.dumps(result)) < MAX_JSON_BYTES // 2
    assert result["probes"]["planned"] == 300 and result["expected"]["loss_percent"] == 10
    text = json.dumps(result)
    for word in ("token", "password", "secret", "psk"):
        assert word not in text.lower()


def test_every_verdict_and_status_has_a_label():
    for verdict in ("matches", "less", "more", "differs", "borderline", "inconclusive",
                    "baseline", "not-comparable", "service-rules", "service-rules-unknown",
                    "out-of-range", "unverified", "no-answer", "unavailable", "changed", "fault",
                    "upload-only"):
        assert verdict in check.VERDICT_LABELS
    for status in ("matches", "less", "more", "extra", "borderline", "inconclusive",
                   "not-judged"):
        assert status in check.STATUS_LABELS
    for name in check.METRIC_IDS:
        assert name in check.METRIC_LABELS


# The export.

def saved_result(**changes):
    result = {
        "id": 3, "started_at": 100, "finished_at": 140, "device_name": "=cmd()",
        "mac_address": PHONE, "label": "+Before", "status": "complete", "source": "device",
        "protected": False, "profile": {"profile_name": "Lossy"},
        "summary": summary(records(100, lost=range(0, 100, 10)),
                           impairment=setting(loss_percent=10)),
    }
    result.update(changes)
    return result


def test_the_csv_export_has_one_row_per_result_and_escapes_formulas():
    text = check.check_export_csv([saved_result(), saved_result(id=4, protected=True)])
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [row["id"] for row in rows] == ["3", "4"]
    assert rows[0]["device_name"] == "'=cmd()" and rows[0]["label"] == "'+Before"
    assert rows[0]["set_loss_percent"] == "10" and rows[0]["loss_percent"] == "10.0"
    assert rows[0]["verdict"] == "matches" and rows[0]["loss_status"] == "matches"
    assert list(rows[0]) == list(check.CHECK_EXPORT_COLUMNS)
    for word in ("token", "password", "Bearer"):
        assert word not in text


def test_the_json_export_names_its_format():
    data = json.loads(check.check_export_json([saved_result()]))
    assert data["format"] == "jittersim-connection-checks" and data["version"] == 1
    assert data["results"][0]["id"] == 3


# The burst loop.

class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ScriptedProber:
    """Answers per a script: sequence index to (delay in seconds or None for lost, copies)."""

    def __init__(self, clock, script=lambda index: (0.01, 1)):
        self.clock, self.script = clock, script
        self.sent_times, self.answers, self.collects = [], [], []

    def send(self):
        index = len(self.sent_times)
        self.sent_times.append(self.clock.now)
        delay, copies = self.script(index)
        if delay is not None:
            for copy in range(copies):
                self.answers.append((self.clock.now + delay + copy * 0.05, index))

    def collect(self, until):
        self.collects.append(until)
        self.clock.now = max(self.clock.now, until)

    def counts(self):
        answered = {index for when, index in self.answers if when <= self.clock.now}
        return len(self.sent_times), len(answered)


def run_burst(count=5, **options):
    clock = FakeClock()
    prober = ScriptedProber(clock, options.pop("script", lambda index: (0.01, 1)))
    finished = check.run_probe_burst(
        prober, count, grace=options.pop("grace", 2.0), clock=clock, sleep=clock.sleep, **options)
    return finished, prober, clock


def test_the_burst_sends_one_probe_per_interval_and_stops_when_all_answered():
    finished, prober, clock = run_burst(5)
    assert finished is True
    assert len(prober.sent_times) == 5
    gaps = [later - earlier for earlier, later in zip(prober.sent_times, prober.sent_times[1:])]
    assert all(gap == pytest.approx(0.1) for gap in gaps)
    # Everything is answered after the last probe, so the wait ends at once.
    assert clock.now - prober.sent_times[-1] < 0.5


def test_a_lost_probe_makes_the_loop_wait_the_whole_grace_time():
    finished, prober, clock = run_burst(5, script=lambda index: (None, 1) if index == 2 else (0.01, 1))
    assert finished is True
    assert clock.now - prober.sent_times[-1] == pytest.approx(0.1 + 2.0, abs=0.25)


def test_a_late_copy_is_waited_for_when_settling():
    # Every probe has its first answer at once, the second copy comes 0.05 s later.
    script = lambda index: (0.01, 2)
    _, early, clock = run_burst(3, script=script, settle=False)
    after_early = len([when for when, _ in early.answers if when <= clock.now])
    _, settled, clock2 = run_burst(3, script=script, settle=True)
    after_settled = len([when for when, _ in settled.answers if when <= clock2.now])
    assert after_settled == 6
    assert after_early <= after_settled
    assert clock2.now - settled.sent_times[-1] >= 2.0 - 0.01


def test_a_stop_ends_the_burst_at_the_next_listening_step():
    state = {"stop": False}

    clock = FakeClock()
    prober = ScriptedProber(clock)
    original = prober.send

    def send_then_stop():
        original()
        if len(prober.sent_times) == 3:
            state["stop"] = True

    prober.send = send_then_stop
    finished = check.run_probe_burst(prober, 100, grace=5.0, clock=clock, sleep=clock.sleep,
                                     stop=lambda: state["stop"])
    assert finished is False
    assert len(prober.sent_times) == 3


def test_the_deadline_ends_a_prober_that_never_answers():
    clock = FakeClock()
    prober = ScriptedProber(clock, lambda index: (None, 1))
    prober.collect = lambda until: None  # a prober that cannot wait
    finished = check.run_probe_burst(prober, 100, grace=10.0, clock=clock, sleep=clock.sleep,
                                     deadline=clock.now + 3.0)
    assert finished is True
    assert clock.now <= 1000.0 + 3.0 + 0.2
    assert len(prober.sent_times) < 100


def test_the_watch_can_end_the_burst_early():
    calls = {"count": 0}

    def watch():
        calls["count"] += 1
        return calls["count"] == 4

    finished, prober, _ = run_burst(50, on_tick=watch)
    assert finished is True
    assert len(prober.sent_times) == 4


# The prober keeps what the check needs.

class PairSocket:
    """One end of a socket pair that stands in for the ICMP datagram socket."""

    def __init__(self, local="192.168.50.1"):
        self.here, self.there = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.local = local

    def setblocking(self, flag):
        self.here.setblocking(flag)

    def connect(self, address):
        pass

    def getsockname(self):
        return (self.local, 0)

    def fileno(self):
        return self.here.fileno()

    def send(self, data):
        return self.here.send(data)

    def recv(self, size):
        return self.here.recv(size)

    def close(self):
        self.here.close()
        self.there.close()


def reply_to(request):
    """The echo reply a device would send for a request."""

    return bytes([0]) + request[1:]


def test_the_prober_counts_copies_keeps_arrival_order_and_pads_to_ping_size():
    pair = PairSocket()
    prober = IcmpProber("192.168.50.10", socket_factory=lambda: pair, max_probes=10,
                        payload_size=check.PAYLOAD_BYTES)
    for _ in range(3):
        prober.send()
    pair.there.settimeout(1)
    requests = [pair.there.recv(2048) for _ in range(3)]
    assert all(len(request) == 8 + check.PAYLOAD_BYTES for request in requests)
    assert all(icmp_checksum(request) == 0 for request in requests)
    # Probe 2 answers first, then probe 1 twice. Probe 3 never answers.
    for index in (1, 0, 0):
        pair.there.send(reply_to(requests[index]))
    prober.collect(until=time.monotonic() + 0.3)
    records = prober.records()
    assert [record["copies"] for record in records] == [2, 1, 0]
    assert [record["arrival"] for record in records] == [1, 0, None]
    assert records[2]["rtt_ms"] is None
    assert prober.counts() == (3, 2)
    stats = check.probe_statistics(records)
    assert (stats["answered_twice"], stats["reordered"], stats["lost"]) == (1, 1, 1)
    prober.close()


def test_the_prober_keeps_every_probe_of_a_long_burst_and_drops_the_oldest_beyond_that():
    pair = PairSocket()
    prober = IcmpProber("192.168.50.10", socket_factory=lambda: pair, max_probes=5)
    for _ in range(8):
        prober.send()
    assert prober.counts() == (5, 0)
    assert len(prober.records()) == 5
    prober.close()
    long_pair = PairSocket()
    long_run = IcmpProber("192.168.50.10", socket_factory=lambda: long_pair,
                          max_probes=check.MAX_PROBES)
    for _ in range(check.MAX_PROBES):
        long_run.send()
    assert long_run.counts()[0] == check.MAX_PROBES
    long_run.close()


def test_the_default_prober_is_unchanged_for_the_speed_test():
    pair = PairSocket()
    prober = IcmpProber("192.168.50.10", socket_factory=lambda: pair)
    prober.send()
    pair.there.settimeout(1)
    assert len(pair.there.recv(2048)) == 8 + 8  # the header and the eight byte nonce
    assert prober.max_probes == 120
    prober.close()


def test_a_prober_that_cannot_run_has_empty_records():
    prober = IcmpProber("192.168.50.10", socket_factory=lambda: PairSocket("10.0.0.5"))
    assert prober.status == "unavailable"
    assert (prober.records(), prober.counts()) == ([], (0, 0))
    no_prober = NoProber()
    assert (no_prober.records(), no_prober.counts(), no_prober.status) == (
        [], (0, 0), "unavailable")

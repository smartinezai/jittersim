"""The terminal's Impact rules: the dashboard node tests' numbers and parity with the page."""

import json
import random

import pytest

from jittersim import impact_rules as rules
from jittersim.api.server import DASHBOARD_HTML
from tests.dashboard_script import NODE, SCRIPT, run_node
from tests.unit.test_dashboard_impact import PRELUDE, served_functions

needs_node = pytest.mark.skipif(NODE is None, reason="node is required")

BASE = {"device_id": "lab", "direction": "upload", "interface": "eth0", "handle": "11:",
        "kind": "netem", "name": "Lab", "packets": 0, "bytes": 0, "drops": 0, "overlimits": 0,
        "requeues": 0, "backlog_bytes": 0, "qlen": 0, "generation": "a1b2c3d4-1"}
NO_IMPAIRMENT = {"delay_ms": 0, "jitter_ms": 0, "loss_percent": 0, "bandwidth_kbit": None,
                 "reorder_percent": 0, "duplicate_percent": 0, "corrupt_percent": 0}
CAP_2000 = {"bandwidth_kbit": 2000}


def entry(fields=None):
    """The node tests' entry(): a queue answer with these counters and settings."""

    fields = dict(fields or {})
    impairment = {**NO_IMPAIRMENT, **fields.pop("impairment", {})}
    return {**BASE, **fields, "impairment": impairment}


def feed(samples, start=None):
    history = start or {}
    for moment, fields in samples:
        history = rules.record_impact_sample(history, {"queues": [entry(fields)]}, moment)
    return history


def rows_of(samples):
    return rules.impact_rows(feed(samples))


def two(impairment, second, first=None):
    return rows_of([(0, {"impairment": impairment, **(first or {})}),
                    (10, {"impairment": impairment, **second})])[0]


def kbit_bytes(kbit, seconds=60):
    return int(kbit * 1000 / 8 * seconds)


def burst(kbits, impairment, step=5, packets=100, backlog=0):
    samples = [(0, {"impairment": impairment, "packets": 0, "bytes": 0})]
    moment = sent = count = 0
    for kbit in kbits:
        moment += step
        sent += kbit_bytes(kbit, step)
        count += packets if kbit else 0
        samples.append((moment, {"impairment": impairment, "packets": count, "bytes": sent,
                                 "backlog_bytes": backlog}))
    return rows_of(samples)[0]


def test_constants_and_labels_match_the_page():
    assert (rules.IMPACT_WINDOW_SECONDS, rules.IMPACT_MIN_PACKETS, rules.IMPACT_CAP_RATIO,
            rules.IMPACT_MAX_SAMPLES, rules.IMPACT_MIN_EXPECTED_DROPS,
            rules.IMPACT_MIN_QUEUE_WAIT_MS) == (60, 50, 0.9, 13, 3, 10)
    for verdict, label in rules.IMPACT_VERDICT_LABELS.items():
        assert f'{json.dumps(verdict) if "-" in verdict else verdict}: "{label}"' in SCRIPT
    assert rules.IMPACT_NOTE in DASHBOARD_HTML
    assert rules.IMPACT_NOTHING_SLOWED in SCRIPT


def test_the_specified_loss_example():
    row = rows_of([(0, {"impairment": {"loss_percent": 5}}),
                   (60, {"impairment": {"loss_percent": 5}, "packets": 629, "drops": 31})])[0]
    assert row["lines"] == ["Loss: set 5% · actual 4.7% (31 of 660 packets dropped)"]
    assert (row["offered"], row["drops"], row["lossPercent"]) == (660, 31, 4.7)
    assert row["verdict"] == "affected"
    assert row["windowSeconds"] == 60


def test_a_whole_percent_prints_without_a_decimal_and_no_traffic_gives_zero():
    row = two({"loss_percent": 20}, {"packets": 80, "drops": 20})
    assert row["lines"] == ["Loss: set 20% · actual 20% (20 of 100 packets dropped)"]
    idle = two({"loss_percent": 20}, {"packets": 0})
    assert (idle["lossPercent"], idle["verdict"]) == (0, "idle")


def test_a_queue_that_drops_without_a_loss_setting_says_why():
    assert two(CAP_2000, {"packets": 90, "drops": 10})["lines"][0] == (
        "Dropped (queue full at the speed cap) · actual 10% (10 of 100 packets dropped)")
    assert two({"delay_ms": 50}, {"packets": 90, "drops": 10})["lines"][0] == (
        "Dropped (queue full) · actual 10% (10 of 100 packets dropped)")


def test_duplicates_qualify_the_loss_percentage():
    row = two({"loss_percent": 10, "duplicate_percent": 5}, {"packets": 90, "drops": 10})
    assert row["lines"][0].endswith(" (may read low because duplicated packets are counted)")


@pytest.mark.parametrize(
    "sent, reached", [(kbit_bytes(1800), True), (kbit_bytes(1802), True), (kbit_bytes(1798), False)]
)
def test_cap_is_reached_at_ninety_percent_of_the_limit(sent, reached):
    row = rows_of([(0, {"impairment": CAP_2000}),
                   (60, {"impairment": CAP_2000, "packets": 100, "bytes": sent})])[0]
    assert row["capReached"] is reached
    assert row["verdict"] == ("affected" if reached else "clean")


def test_a_burst_then_idle_still_reads_as_the_cap_reached():
    row = burst([2000] * 4 + [0] * 7, CAP_2000)
    assert (row["windowSeconds"], row["rateKbit"], row["peakKbit"]) == (55, 727, 2000)
    assert row["lines"][0] == "Speed: set 2 Mbit/s · using 727 kbit/s · peak 2 Mbit/s · cap reached"


def test_the_average_divides_by_the_sample_span():
    row = burst([2000] * 4 + [0] * 2, CAP_2000)
    assert row["lines"][0] == (
        "Speed: set 2 Mbit/s · using 1.33 Mbit/s · peak 2 Mbit/s · cap reached")


def test_speed_line_formats_both_units():
    assert burst([1960] * 6, CAP_2000, step=10)["lines"][0] == (
        "Speed: set 2 Mbit/s · using 1.96 Mbit/s · peak 1.96 Mbit/s · cap reached")
    assert burst([100] * 6, {"bandwidth_kbit": 640}, step=10)["lines"][0] == (
        "Speed: set 640 kbit/s · using 100 kbit/s · peak 100 kbit/s · below the cap")


def test_queue_wait_divides_the_backlog_by_the_cap_and_hides_below_ten_milliseconds():
    row = burst([2000] * 4, CAP_2000, backlog=80_000)
    assert (row["queueWaitMs"], row["lines"][1]) == (320, "Queue wait now: about 320 ms")
    assert burst([2000] * 4, CAP_2000, backlog=2_500)["queueWaitMs"] == 10
    small = burst([2000] * 4, CAP_2000, backlog=2_000)
    assert small["queueWaitMs"] == 8
    assert not any(line.startswith("Queue wait") for line in small["lines"])


def test_a_backlog_at_a_sample_proves_the_cap():
    row = rows_of([(0, {"impairment": CAP_2000}),
                   (5, {"impairment": CAP_2000, "packets": 100, "bytes": kbit_bytes(799, 5),
                        "backlog_bytes": 210_000})])[0]
    assert (row["capReached"], row["peakKbit"], row["queueWaitMs"]) == (True, 799, 840)


def test_packets_still_in_the_delay_queue_count_as_offered():
    row = two({"loss_percent": 20, "delay_ms": 2000}, {"packets": 0, "drops": 216, "qlen": 784})
    assert (row["offered"], row["lossPercent"]) == (1000, 21.6)
    assert row["lines"][0] == "Loss: set 20% · actual 21.6% (216 of 1000 packets dropped)"


@pytest.mark.parametrize(
    "impairment, counters, expected",
    [
        ({"loss_percent": 5}, {"drops": 1, "packets": 10}, "affected"),
        ({"loss_percent": 5}, {"packets": 0}, "idle"),
        ({"loss_percent": 5}, {"packets": 49}, "too-few"),
        ({"loss_percent": 5}, {"packets": 59}, "too-few"),
        ({"loss_percent": 5}, {"packets": 60}, "clean"),
        ({"loss_percent": 10}, {"packets": 49}, "too-few"),
        ({"loss_percent": 10}, {"packets": 50}, "clean"),
        ({"loss_percent": 100}, {"packets": 50}, "clean"),
        (CAP_2000, {"packets": 49}, "too-few"),
        (CAP_2000, {"packets": 50}, "clean"),
        (CAP_2000, {"packets": 30, "drops": 1}, "affected"),
        ({"delay_ms": 50}, {"packets": 0}, "idle"),
        ({"delay_ms": 50}, {"packets": 3}, "unmeasured"),
        ({"reorder_percent": 20}, {"packets": 500}, "unmeasured"),
        ({"delay_ms": 50}, {"packets": 90, "drops": 10}, "affected"),
    ],
)
def test_verdicts(impairment, counters, expected):
    assert two(impairment, counters)["verdict"] == expected


def test_one_sample_or_samples_at_one_instant_are_still_collecting():
    assert rows_of([(0, {"impairment": {"loss_percent": 5}})])[0]["lines"] == ["Collecting…"]
    assert rows_of([(5, {"impairment": {"loss_percent": 5}}),
                    (5, {"impairment": {"loss_percent": 5}, "packets": 10})])[0]["verdict"] == (
        "collecting")


def test_a_new_apply_between_the_two_samples_restarts_the_history():
    history = feed([(0, {"impairment": {"loss_percent": 5}, "packets": 10, "generation": "a-1"}),
                    (5, {"impairment": {"loss_percent": 5}, "packets": 500, "generation": "a-2"})])
    assert len(history["eth0/11:"]["samples"]) == 1


def test_samples_older_than_the_window_are_ignored():
    outside = rows_of([(0, {"impairment": {"loss_percent": 5}}),
                       (61, {"impairment": {"loss_percent": 5}, "packets": 100})])[0]
    assert outside["verdict"] == "collecting"


def test_rows_are_sorted_by_name_then_upload_before_download():
    imp = {"loss_percent": 5}
    history = rules.record_impact_sample({}, {"queues": [
        entry({"name": "b", "direction": "upload", "handle": "11:", "impairment": imp}),
        entry({"name": "a", "direction": "upload", "handle": "12:", "impairment": imp}),
        entry({"name": "b", "direction": "download", "interface": "wlan0", "impairment": imp}),
        entry({"name": "a", "direction": "download", "interface": "wlan0", "handle": "12:",
               "impairment": imp}),
    ]}, 0)
    assert [row["name"] + "/" + row["direction"] for row in rules.impact_rows(history)] == [
        "a/upload", "a/download", "b/upload", "b/download"]


def test_queues_without_any_impairment_are_left_out():
    assert rows_of([(0, {}), (10, {"packets": 100})]) == []


def test_recording_does_not_change_the_history_it_was_given():
    history = feed([(0, {"impairment": {"loss_percent": 5}})])
    before = json.dumps(history)
    rules.record_impact_sample(history, {"queues": [entry({"impairment": {"loss_percent": 5},
                                                           "packets": 3})]}, 5)
    assert json.dumps(history) == before


def random_samples(generator):
    impairment = {}
    for field, choices in (
        ("delay_ms", (0, 0, 50, 2000)), ("jitter_ms", (0, 0, 15)),
        ("loss_percent", (0, 1, 5, 10, 2.5, 100)), ("bandwidth_kbit", (None, 250, 640, 2000)),
        ("reorder_percent", (0, 25)), ("duplicate_percent", (0, 5)),
        ("corrupt_percent", (0, 1)),
    ):
        impairment[field] = generator.choice(choices)
    count = generator.choice((1, 2, 2, 2, 3, 5, 13, 15))
    moment = generator.choice((0, 1000.25))
    packets = drops = sent = 0
    samples = []
    for _ in range(count):
        samples.append([moment, {"impairment": impairment, "packets": packets, "drops": drops,
                                 "bytes": sent, "qlen": generator.choice((0, 0, 30, 784)),
                                 "backlog_bytes": generator.choice((0, 0, 2000, 2500, 80000))}])
        moment += generator.choice((5, 5, 10, 0.37, 30, 61))
        packets += generator.choice((0, 3, 49, 50, 100, 1000))
        drops += generator.choice((0, 0, 1, 11, 216))
        sent += generator.choice((0, 1500, kbit_bytes(1800, 5), kbit_bytes(2000, 10)))
    return samples


@needs_node
def test_the_python_rules_give_the_page_rows_for_random_samples():
    generator = random.Random(20261002)
    cases = [random_samples(generator) for _ in range(400)]
    page = run_node([served_functions(), PRELUDE], f"{json.dumps(cases)}.map((c) => rowsOf(c))")
    for samples, expected in zip(cases, page):
        assert rows_of([tuple(item) for item in samples]) == expected, samples


@needs_node
def test_two_answers_from_the_api_give_the_page_rows():
    generator = random.Random(7)
    for _ in range(50):
        samples = random_samples(generator)[:2]
        if len(samples) < 2:
            continue
        first = {"sampled_at": samples[0][0], "queues": [entry(samples[0][1])]}
        second = {"sampled_at": samples[1][0], "queues": [entry(samples[1][1])]}
        page = run_node([served_functions(), PRELUDE], f"rowsOf({json.dumps(samples)})")
        assert rules.two_sample_rows(first, second) == page

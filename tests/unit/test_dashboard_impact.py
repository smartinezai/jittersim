"""Run the dashboard's impact-check functions in node."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)

PRELUDE = """
function entry(o) {
  o = o || {};
  const base = {device_id: 'lab', direction: 'upload', interface: 'eth0', handle: '11:',
    kind: 'netem', name: 'Lab', packets: 0, bytes: 0, drops: 0, overlimits: 0, requeues: 0,
    backlog_bytes: 0, qlen: 0, generation: 'a1b2c3d4-1'};
  const impairment = Object.assign({delay_ms: 0, jitter_ms: 0, loss_percent: 0,
    bandwidth_kbit: null, reorder_percent: 0, duplicate_percent: 0, corrupt_percent: 0},
    o.impairment || {});
  return Object.assign(base, o, {impairment: impairment});
}
function feed(samples, start) {
  let history = start || {};
  samples.forEach((item) => {
    history = recordImpactSample(history, {queues: [entry(item[1])]}, item[0]);
  });
  return history;
}
function rowsOf(samples) { return impactRows(feed(samples)); }
function show(value) { console.log(JSON.stringify(value)); }
"""


def served_functions():
    start = SCRIPT.index("const IMPACT_WINDOW_SECONDS")
    return SCRIPT[start : SCRIPT.index("// end of the impact functions", start)]


def run(expression):
    program = served_functions() + PRELUDE + "\nshow(" + expression + ");\n"
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")


def kbit_bytes(kbit, seconds=60):
    """Bytes sent in `seconds` at this many kbit/s."""

    return int(kbit * 1000 / 8 * seconds)


def js(value):
    return json.dumps(value)


def two(impairment, second, first=None):
    """One row from two samples 10 seconds apart. `second` holds the counters of the last one."""

    return run(
        f"rowsOf([[0, {{impairment: {js(impairment)}, {first or 'packets: 0'}}}],"
        f" [10, {{impairment: {js(impairment)}, {second}}}]])"
    )[0]


def burst(kbits, impairment, step=5, packets=100, backlog=0):
    """One row from samples `step` seconds apart. Each number is the speed of one interval."""

    samples = [f"[0, {{impairment: {js(impairment)}, packets: 0, bytes: 0}}]"]
    time_now = bytes_sent = packets_sent = 0
    for kbit in kbits:
        time_now += step
        bytes_sent += kbit_bytes(kbit, step)
        packets_sent += packets if kbit else 0
        samples.append(
            f"[{time_now}, {{impairment: {js(impairment)}, packets: {packets_sent},"
            f" bytes: {bytes_sent}, backlog_bytes: {backlog}}}]"
        )
    return run("rowsOf([" + ",".join(samples) + "])")[0]


CAP_2000 = {"bandwidth_kbit": 2000}


@needs_node
def test_constants_match_the_plan():
    assert run(
        "[IMPACT_WINDOW_SECONDS, IMPACT_MIN_PACKETS, IMPACT_CAP_RATIO, IMPACT_MAX_SAMPLES,"
        " IMPACT_MIN_EXPECTED_DROPS, IMPACT_MIN_QUEUE_WAIT_MS]"
    ) == [60, 50, 0.9, 13, 3, 10]


@needs_node
def test_the_specified_loss_example():
    row = run(
        "rowsOf([[0, {impairment: {loss_percent: 5}}],"
        " [60, {impairment: {loss_percent: 5}, packets: 629, drops: 31}]])"
    )[0]
    assert row["lines"] == ["Loss: set 5% · actual 4.7% (31 of 660 packets dropped)"]
    assert (row["offered"], row["drops"], row["lossPercent"]) == (660, 31, 4.7)
    assert row["verdict"] == "affected"
    assert row["windowSeconds"] == 60
    assert (row["name"], row["direction"]) == ("Lab", "upload")


@needs_node
def test_a_whole_percent_prints_without_a_decimal_and_no_traffic_gives_zero():
    row = two({"loss_percent": 20}, "packets: 80, drops: 20")
    assert row["lines"] == ["Loss: set 20% · actual 20% (20 of 100 packets dropped)"]
    idle = two({"loss_percent": 20}, "packets: 0")
    assert idle["lossPercent"] == 0
    assert idle["verdict"] == "idle"


@needs_node
def test_a_queue_that_drops_without_a_loss_setting_says_why():
    capped = two(CAP_2000, "packets: 90, drops: 10")
    assert capped["lines"][0] == (
        "Dropped (queue full at the speed cap) · actual 10% (10 of 100 packets dropped)"
    )
    assert not any(line.startswith("Loss") for line in capped["lines"])
    delayed = two({"delay_ms": 50}, "packets: 90, drops: 10")
    assert delayed["lines"][0] == "Dropped (queue full) · actual 10% (10 of 100 packets dropped)"
    assert delayed["verdict"] == "affected"
    with_loss = two({"loss_percent": 10, "bandwidth_kbit": 2000}, "packets: 90, drops: 10")
    assert with_loss["lines"][0].startswith("Loss: set 10% · actual 10%")


@needs_node
def test_duplicates_qualify_the_loss_percentage():
    note = " (may read low because duplicated packets are counted)"
    row = two({"loss_percent": 10, "duplicate_percent": 5}, "packets: 90, drops: 10")
    assert row["lines"][0] == "Loss: set 10% · actual 10% (10 of 100 packets dropped)" + note
    plain = two({"loss_percent": 10}, "packets: 90, drops: 10")
    assert note not in plain["lines"][0]
    dropped = two({"bandwidth_kbit": 2000, "duplicate_percent": 5}, "packets: 90, drops: 10")
    assert dropped["lines"][0].endswith(note)


@needs_node
@pytest.mark.parametrize(
    "bytes_sent, reached",
    [(kbit_bytes(1800), True), (kbit_bytes(1802), True), (kbit_bytes(1798), False)],
)
def test_cap_is_reached_at_ninety_percent_of_the_limit(bytes_sent, reached):
    row = run(
        "rowsOf([[0, {impairment: {bandwidth_kbit: 2000}}],"
        f" [60, {{impairment: {{bandwidth_kbit: 2000}}, packets: 100, bytes: {bytes_sent}}}]])"
    )[0]
    assert row["capReached"] is reached
    assert row["verdict"] == ("affected" if reached else "clean")
    assert ("cap reached" in row["lines"][0]) is reached
    assert ("below the cap" in row["lines"][0]) is not reached


@needs_node
def test_a_burst_then_idle_still_reads_as_the_cap_reached():
    """2000 kbit/s for 20 seconds and then nothing inside a 55 second span."""

    row = burst([2000] * 4 + [0] * 7, CAP_2000)
    assert row["windowSeconds"] == 55
    assert row["rateKbit"] == 727
    assert row["peakKbit"] == 2000
    assert row["capReached"] is True
    assert row["verdict"] == "affected"
    assert row["lines"][0] == (
        "Speed: set 2 Mbit/s · using 727 kbit/s · peak 2 Mbit/s · cap reached"
    )


@needs_node
def test_the_average_divides_by_the_sample_span_and_not_by_a_minute():
    row = burst([2000] * 4 + [0] * 2, CAP_2000)
    assert row["windowSeconds"] == 30
    assert row["rateKbit"] == 1333
    assert row["peakKbit"] == 2000
    assert row["lines"][0] == (
        "Speed: set 2 Mbit/s · using 1.33 Mbit/s · peak 2 Mbit/s · cap reached"
    )
    short = burst([2000, 2000], CAP_2000)
    assert (short["windowSeconds"], short["rateKbit"]) == (10, 2000)


@needs_node
def test_one_slow_and_busy_stretch_never_hides_a_fast_one():
    row = burst([300, 400, 1900, 300, 200], CAP_2000)
    assert row["peakKbit"] == 1900
    assert row["capReached"] is True
    steady = burst([1000, 1100, 1000, 1200], CAP_2000)
    assert steady["capReached"] is False
    assert steady["peakKbit"] == 1200
    assert steady["lines"][0].endswith("peak 1.2 Mbit/s · below the cap")
    assert steady["verdict"] == "clean"


@needs_node
def test_a_fast_interval_without_offered_packets_is_not_a_cap_hit():
    row = run(
        "rowsOf([[0, {impairment: {bandwidth_kbit: 2000}}],"
        f" [5, {{impairment: {{bandwidth_kbit: 2000}}, bytes: {kbit_bytes(2000, 5)}}}],"
        " [10, {impairment: {bandwidth_kbit: 2000}, packets: 100,"
        f" bytes: {kbit_bytes(2000, 5) + kbit_bytes(100, 5)}}}]])"
    )[0]
    assert (row["offered"], row["capReached"]) == (100, False)


@needs_node
def test_dropped_packets_count_as_offered_in_every_interval():
    row = run(
        "rowsOf([[0, {impairment: {bandwidth_kbit: 2000}}],"
        f" [5, {{impairment: {{bandwidth_kbit: 2000}}, drops: 100, bytes: {kbit_bytes(2000, 5)}}}]])"
    )[0]
    assert (row["offered"], row["capReached"], row["verdict"]) == (100, True, "affected")


@needs_node
def test_speed_line_formats_both_units():
    row = burst([1960] * 6, CAP_2000, step=10)
    assert row["lines"][0] == (
        "Speed: set 2 Mbit/s · using 1.96 Mbit/s · peak 1.96 Mbit/s · cap reached"
    )
    assert row["rateKbit"] == 1960
    slow = burst([100] * 6, {"bandwidth_kbit": 640}, step=10)
    assert slow["lines"][0] == (
        "Speed: set 640 kbit/s · using 100 kbit/s · peak 100 kbit/s · below the cap"
    )


@needs_node
def test_a_cap_without_traffic_is_never_reached():
    row = two(CAP_2000, "packets: 0, bytes: 0")
    assert row["capReached"] is False
    assert row["verdict"] == "idle"


@needs_node
def test_queue_wait_divides_the_backlog_by_the_cap_and_hides_below_ten_milliseconds():
    # 80000 bytes * 8 bits / 2000 kbit/s = 320 ms
    row = burst([2000] * 4, CAP_2000, backlog=80_000)
    assert row["queueWaitMs"] == 320
    assert row["lines"][1] == "Queue wait now: about 320 ms"
    # 2500 bytes at 2000 kbit/s is exactly 10 ms and 2000 bytes is 8 ms
    ten = burst([2000] * 4, CAP_2000, backlog=2_500)
    assert ten["queueWaitMs"] == 10
    assert "Queue wait now: about 10 ms" in ten["lines"]
    small = burst([2000] * 4, CAP_2000, backlog=2_000)
    assert small["queueWaitMs"] == 8
    assert not any(line.startswith("Queue wait") for line in small["lines"])
    assert not any(line.startswith("Queue wait") for line in burst([2000] * 4, CAP_2000)["lines"])


@needs_node
def test_queue_wait_reads_the_last_sample_and_only_without_delay_or_jitter():
    # the burst is over and the backlog has drained so the old burst shows no wait now
    drained = run(
        "rowsOf([[0, {impairment: {bandwidth_kbit: 2000}}],"
        f" [5, {{impairment: {{bandwidth_kbit: 2000}}, packets: 100, bytes: {kbit_bytes(2000, 5)},"
        " backlog_bytes: 80000}],"
        f" [10, {{impairment: {{bandwidth_kbit: 2000}}, packets: 100, bytes: {kbit_bytes(2000, 5)},"
        " backlog_bytes: 0}]])"
    )[0]
    assert drained["capReached"] is True
    assert not any(line.startswith("Queue wait") for line in drained["lines"])
    for extra in ({"delay_ms": 50}, {"jitter_ms": 5}):
        row = burst([2000] * 4, dict(CAP_2000, **extra), backlog=80_000)
        assert row["queueWaitMs"] is None
        assert not any(line.startswith("Queue wait") for line in row["lines"])
    below = burst([300] * 4, CAP_2000)
    assert below["queueWaitMs"] is None
    assert burst([2000] * 4, {"loss_percent": 10}, backlog=80_000)["queueWaitMs"] is None


def backlog_row(backlog, impairment=CAP_2000, rate_kbit=300):
    """Two samples 5 seconds apart that move `rate_kbit` and hold `backlog` bytes at the last."""

    return run(
        f"rowsOf([[0, {{impairment: {js(impairment)}}}],"
        f" [5, {{impairment: {js(impairment)}, packets: 100, bytes: {kbit_bytes(rate_kbit, 5)},"
        f" backlog_bytes: {backlog}}}]])"
    )[0]


@needs_node
def test_a_backlog_at_a_sample_proves_the_cap_even_when_no_interval_ran_at_the_cap():
    """A 500 kB burst at a 2 Mbit/s cap on the real kernel averaged 799 kbit/s over 5 seconds."""

    row = backlog_row(210_000, rate_kbit=799)
    assert row["capReached"] is True
    assert row["verdict"] == "affected"
    assert row["peakKbit"] == 799
    assert row["lines"][0] == (
        "Speed: set 2 Mbit/s · using 799 kbit/s · peak 799 kbit/s · cap reached"
    )
    assert row["queueWaitMs"] == 840
    assert row["lines"][1] == "Queue wait now: about 840 ms"


@needs_node
def test_a_backlog_counts_from_ten_milliseconds_of_cap_time():
    # 2500 bytes at 2000 kbit/s is exactly 10 ms and 2000 bytes is 8 ms
    assert backlog_row(2_500)["capReached"] is True
    assert backlog_row(2_000)["capReached"] is False
    assert backlog_row(2_000)["verdict"] == "clean"
    assert backlog_row(0)["capReached"] is False
    # the same bytes are a longer wait at a lower cap
    assert backlog_row(2_000, {"bandwidth_kbit": 1000})["capReached"] is True


@needs_node
def test_a_backlog_of_an_older_sample_still_counts_for_the_minute():
    row = run(
        "rowsOf([[0, {impairment: {bandwidth_kbit: 2000}}],"
        " [5, {impairment: {bandwidth_kbit: 2000}, packets: 50, bytes: 40000,"
        " backlog_bytes: 50000}],"
        " [10, {impairment: {bandwidth_kbit: 2000}, packets: 100, bytes: 80000}]])"
    )[0]
    assert row["capReached"] is True
    # the backlog is gone now so no wait is shown
    assert row["queueWaitMs"] == 0
    assert not any(line.startswith("Queue wait") for line in row["lines"])


@needs_node
@pytest.mark.parametrize("extra", [{"delay_ms": 50}, {"jitter_ms": 5}])
def test_a_backlog_with_a_delay_is_no_sign_of_the_cap(extra):
    row = backlog_row(80_000, dict(CAP_2000, **extra))
    assert row["capReached"] is False
    assert row["verdict"] == "clean"


@needs_node
def test_a_backlog_without_a_cap_says_nothing():
    row = backlog_row(80_000, {"loss_percent": 10})
    assert row["capReached"] is False
    assert row["queueWaitMs"] is None


@needs_node
def test_a_burst_that_drained_between_two_samples_is_still_missed():
    """The stated limit: no backlog at a sample and under 90% of one interval at the cap."""

    row = run(
        "rowsOf([[0, {impairment: {bandwidth_kbit: 2000}}],"
        f" [5, {{impairment: {{bandwidth_kbit: 2000}}, packets: 357, bytes: 499260}}]])"
    )[0]
    assert row["peakKbit"] == 799
    assert row["capReached"] is False
    assert row["verdict"] == "clean"


@needs_node
def test_packets_still_in_the_delay_queue_count_as_offered():
    """Loss 20% with a 2000 ms delay on the real kernel: 216 dropped and 784 still waiting."""

    row = two(
        {"loss_percent": 20, "delay_ms": 2000}, "packets: 0, drops: 216, qlen: 784"
    )
    assert (row["offered"], row["packets"], row["drops"]) == (1000, 0, 216)
    assert row["lossPercent"] == 21.6
    assert row["lines"][0] == "Loss: set 20% · actual 21.6% (216 of 1000 packets dropped)"
    assert row["verdict"] == "affected"
    # once the delay is over the same packets have left and the figure does not change
    after = run(
        "rowsOf([[0, {impairment: {loss_percent: 20, delay_ms: 2000}}],"
        " [5, {impairment: {loss_percent: 20, delay_ms: 2000}, packets: 0, drops: 216, qlen: 784}],"
        " [10, {impairment: {loss_percent: 20, delay_ms: 2000}, packets: 784, drops: 216,"
        " qlen: 0}]])"
    )[0]
    assert (after["offered"], after["lossPercent"]) == (1000, 21.6)


@needs_node
def test_a_queue_that_only_drains_offers_nothing_new_but_is_not_idle():
    row = run(
        "rowsOf([[0, {impairment: {loss_percent: 20, delay_ms: 2000}, qlen: 784}],"
        " [5, {impairment: {loss_percent: 20, delay_ms: 2000}, packets: 784, qlen: 0}]])"
    )[0]
    assert (row["offered"], row["packets"]) == (0, 784)
    assert row["verdict"] == "too-few"
    nothing = run(
        "rowsOf([[0, {impairment: {loss_percent: 20, delay_ms: 2000}}],"
        " [5, {impairment: {loss_percent: 20, delay_ms: 2000}}]])"
    )[0]
    assert nothing["verdict"] == "idle"


@needs_node
def test_packets_that_only_wait_make_the_queue_busy_and_not_idle():
    row = two({"loss_percent": 20, "delay_ms": 2000}, "packets: 0, qlen: 300")
    assert (row["offered"], row["drops"]) == (300, 0)
    assert row["verdict"] == "clean"
    unmeasured = two({"delay_ms": 2000}, "packets: 0, qlen: 300")
    assert unmeasured["verdict"] == "unmeasured"


@needs_node
def test_an_entry_without_a_queue_length_counts_as_zero():
    row = run(
        "(() => { const old = entry({impairment: {loss_percent: 20}, drops: 3, packets: 7});"
        " delete old.qlen; let h = recordImpactSample({}, {queues: [entry({impairment:"
        " {loss_percent: 20}})]}, 0); h = recordImpactSample(h, {queues: [old]}, 5);"
        " return impactRows(h)[0]; })()"
    )
    assert (row["offered"], row["lossPercent"]) == (10, 30)


@needs_node
def test_delay_line_with_and_without_jitter_is_marked_not_measured():
    def lines(impairment):
        return two(impairment, "packets: 100")["lines"]

    assert lines({"delay_ms": 100, "jitter_ms": 20})[-1] == (
        "Delay: set 100 ms ± 20 · configured, not measured"
    )
    assert lines({"delay_ms": 100})[-1] == "Delay: set 100 ms · configured, not measured"
    assert lines({"jitter_ms": 15})[-1] == "Delay: set 0 ms ± 15 · configured, not measured"
    assert not any(line.startswith("Delay") for line in lines({"loss_percent": 5}))


@needs_node
def test_reorder_duplicate_and_corrupt_are_shown_as_configured_and_not_measured():
    row = two(
        {"reorder_percent": 25, "duplicate_percent": 5, "corrupt_percent": 1},
        "packets: 100",
    )
    assert row["lines"] == [
        "100 packets passed in the last minute",
        "Reorder: set 25% · configured, not measured",
        "Duplicate: set 5% · configured, not measured",
        "Corrupt: set 1% · configured, not measured",
    ]
    only = two({"duplicate_percent": 7}, "packets: 100")
    assert only["lines"][1:] == ["Duplicate: set 7% · configured, not measured"]


@needs_node
def test_lines_come_in_the_documented_order():
    impairment = {
        "loss_percent": 5, "bandwidth_kbit": 1000, "delay_ms": 0, "reorder_percent": 10,
        "duplicate_percent": 2, "corrupt_percent": 1,
    }
    row = run(
        f"rowsOf([[0, {{impairment: {js(impairment)}}}],"
        f" [60, {{impairment: {js(impairment)}, packets: 190, drops: 10,"
        f" bytes: {kbit_bytes(1000)}, backlog_bytes: 12500}}]])"
    )[0]
    assert [line.split(":")[0] for line in row["lines"]] == [
        "Loss", "Speed", "Queue wait now", "Reorder", "Duplicate", "Corrupt",
    ]
    delayed = dict(impairment, delay_ms=30)
    row = run(
        f"rowsOf([[0, {{impairment: {js(delayed)}}}],"
        f" [60, {{impairment: {js(delayed)}, packets: 190, drops: 10,"
        f" bytes: {kbit_bytes(1000)}}}]])"
    )[0]
    assert [line.split(":")[0] for line in row["lines"]] == [
        "Loss", "Speed", "Delay", "Reorder", "Duplicate", "Corrupt",
    ]


@needs_node
@pytest.mark.parametrize(
    "impairment, counters, expected",
    [
        # loss only
        ({"loss_percent": 5}, "drops: 1, packets: 10", "affected"),
        ({"loss_percent": 5}, "packets: 0", "idle"),
        ({"loss_percent": 5}, "packets: 49", "too-few"),
        ({"loss_percent": 5}, "packets: 59", "too-few"),
        ({"loss_percent": 5}, "packets: 60", "clean"),
        ({"loss_percent": 5}, "packets: 500", "clean"),
        ({"loss_percent": 10}, "packets: 49", "too-few"),
        ({"loss_percent": 10}, "packets: 50", "clean"),
        ({"loss_percent": 100}, "packets: 50", "clean"),
        ({"loss_percent": 5}, "packets: 10, drops: 39", "affected"),
        # speed cap only
        (CAP_2000, "packets: 49", "too-few"),
        (CAP_2000, "packets: 50", "clean"),
        (CAP_2000, "packets: 30, drops: 1", "affected"),
        # nothing the kernel counters can judge
        ({"delay_ms": 50}, "packets: 0", "idle"),
        ({"delay_ms": 50}, "packets: 3", "unmeasured"),
        ({"delay_ms": 50}, "packets: 500", "unmeasured"),
        ({"reorder_percent": 20}, "packets: 500", "unmeasured"),
        ({"duplicate_percent": 20}, "packets: 500", "unmeasured"),
        ({"corrupt_percent": 20}, "packets: 500", "unmeasured"),
        ({"delay_ms": 50}, "packets: 90, drops: 10", "affected"),
    ],
)
def test_verdicts(impairment, counters, expected):
    assert two(impairment, counters)["verdict"] == expected


@needs_node
def test_an_unmeasured_queue_says_how_many_packets_passed():
    row = two({"delay_ms": 50}, "packets: 500")
    assert row["lines"][0] == "500 packets passed in the last minute"
    one = two({"delay_ms": 50}, "packets: 1")
    assert one["lines"][0] == "1 packet passed in the last minute"
    idle = two({"delay_ms": 50}, "packets: 0")
    assert not any("passed" in line for line in idle["lines"])
    measured = two({"loss_percent": 10}, "packets: 500")
    assert not any("passed" in line for line in measured["lines"])


@needs_node
def test_one_sample_or_samples_at_one_instant_are_still_collecting():
    single = run("rowsOf([[0, {impairment: {loss_percent: 5}}]])")[0]
    assert single["verdict"] == "collecting"
    assert single["lines"] == ["Collecting…"]
    same_instant = run(
        "rowsOf([[5, {impairment: {loss_percent: 5}}],"
        " [5, {impairment: {loss_percent: 5}, packets: 10}]])"
    )[0]
    assert same_instant["verdict"] == "collecting"
    assert same_instant["rateKbit"] == 0
    kept = run(
        "(() => { const h = feed([[5, {impairment: {loss_percent: 5}}],"
        " [5, {impairment: {loss_percent: 5}, packets: 10}]]);"
        " return h['eth0/11:'].samples.length; })()"
    )
    assert kept == 1


@needs_node
def test_a_clock_that_steps_back_restarts_instead_of_waiting():
    result = run(
        "(() => { const h = feed([[100, {impairment: {loss_percent: 5}, packets: 10}],"
        " [105, {impairment: {loss_percent: 5}, packets: 20}],"
        " [90, {impairment: {loss_percent: 5}, packets: 30}],"
        " [95, {impairment: {loss_percent: 5}, packets: 140}]]);"
        " return {samples: h['eth0/11:'].samples.map((s) => s.t), rows: impactRows(h)}; })()"
    )
    assert result["samples"] == [90, 95]
    assert result["rows"][0]["offered"] == 110
    assert result["rows"][0]["windowSeconds"] == 5


@needs_node
@pytest.mark.parametrize("counter", ["packets", "drops", "bytes"])
def test_a_counter_that_goes_backwards_restarts_that_queue(counter):
    both = {"packets": 100, "drops": 10, "bytes": 5000}
    lower = dict(both, **{counter: both[counter] - 1})
    imp = "impairment: {loss_percent: 5}"
    history = run(
        f"(() => {{ const h = feed([[0, {{{imp}, packets: 100, drops: 10, bytes: 5000}}],"
        f" [10, {{{imp}, packets: {lower['packets']}, drops: {lower['drops']},"
        f" bytes: {lower['bytes']}}}]]);"
        " return {samples: h['eth0/11:'].samples.length, rows: impactRows(h)}; })()"
    )
    assert history["samples"] == 1
    assert history["rows"][0]["verdict"] == "collecting"


@needs_node
def test_the_history_grows_again_after_a_restart():
    row = run(
        "rowsOf([[0, {impairment: {loss_percent: 5}, packets: 900}],"
        " [5, {impairment: {loss_percent: 5}, packets: 3}],"
        " [10, {impairment: {loss_percent: 5}, packets: 103, drops: 5}]])"
    )[0]
    assert (row["offered"], row["drops"], row["windowSeconds"]) == (105, 5, 5)


@needs_node
def test_a_new_apply_restarts_the_history_even_with_identical_settings_and_larger_counters():
    result = run(
        "(() => { const imp = {loss_percent: 5};"
        " const h = feed([[0, {impairment: imp, packets: 10, bytes: 1000, generation: 'aa11-1'}],"
        " [5, {impairment: imp, packets: 500, bytes: 90000, generation: 'aa11-2'}]]);"
        " return {samples: h['eth0/11:'].samples.length, rows: impactRows(h)}; })()"
    )
    assert result["samples"] == 1
    assert result["rows"][0]["verdict"] == "collecting"
    kept = run(
        "(() => { const imp = {loss_percent: 5};"
        " const h = feed([[0, {impairment: imp, packets: 10, generation: 'aa11-2'}],"
        " [5, {impairment: imp, packets: 500, generation: 'aa11-2'}]]);"
        " return h['eth0/11:'].samples.length; })()"
    )
    assert kept == 2


@needs_node
def test_a_queue_that_changed_hands_or_settings_restarts_even_when_counters_grow():
    base = "packets: 10, bytes: 1000"
    bigger = "packets: 500, bytes: 90000"
    for second in (
        "device_id: 'other'",
        "direction: 'download'",
        "impairment: {loss_percent: 6}",
    ):
        history = run(
            f"(() => {{ const h = feed([[0, {{impairment: {{loss_percent: 5}}, {base}}}],"
            f" [5, {{impairment: {{loss_percent: 5}}, {bigger}, {second}}}]]);"
            " return h['eth0/11:'].samples.length; })()"
        )
        assert history == 1, second


@needs_node
def test_samples_older_than_the_window_are_ignored():
    row = run(
        "rowsOf([[0, {impairment: {loss_percent: 5}, packets: 0}],"
        " [100, {impairment: {loss_percent: 5}, packets: 1000}],"
        " [130, {impairment: {loss_percent: 5}, packets: 1100, drops: 4}]])"
    )[0]
    assert (row["offered"], row["drops"], row["windowSeconds"]) == (104, 4, 30)
    exact = run(
        "rowsOf([[0, {impairment: {loss_percent: 5}, packets: 0}],"
        " [60, {impairment: {loss_percent: 5}, packets: 100}]])"
    )[0]
    assert (exact["offered"], exact["windowSeconds"]) == (100, 60)
    outside = run(
        "rowsOf([[0, {impairment: {loss_percent: 5}, packets: 0}],"
        " [61, {impairment: {loss_percent: 5}, packets: 100}]])"
    )[0]
    assert outside["verdict"] == "collecting"


@needs_node
def test_thirteen_samples_five_seconds_apart_cover_the_whole_minute():
    kept = run(
        "(() => { const h = feed(Array.from({length: 20}, (_, i) =>"
        " [i * 5, {impairment: {loss_percent: 5}, packets: i * 100}]));"
        " const samples = h['eth0/11:'].samples;"
        " return [samples.length, samples[0].t, samples[12].t, impactRows(h)[0].windowSeconds]; })()"
    )
    assert kept == [13, 35, 95, 60]


@needs_node
def test_queues_missing_from_the_response_are_dropped():
    result = run(
        "(() => { let h = {};"
        " h = recordImpactSample(h, {queues: [entry({handle: '11:'}),"
        " entry({handle: '12:', direction: 'download', interface: 'wlan0'})]}, 0);"
        " h = recordImpactSample(h, {queues: [entry({handle: '11:'})]}, 5);"
        " const kept = Object.keys(h);"
        " h = recordImpactSample(h, {queues: []}, 10);"
        " return [kept, Object.keys(h), h]; })()"
    )
    assert result[0] == ["eth0/11:"]
    assert result[1] == []
    assert result[2] == {}


@needs_node
def test_keys_combine_interface_and_handle_and_samples_hold_only_counters():
    result = run(
        "(() => { const h = recordImpactSample({}, {queues: [entry({packets: 7, drops: 2,"
        " bytes: 900, overlimits: 1, backlog_bytes: 40})]}, 12.5);"
        " return h; })()"
    )
    assert list(result) == ["eth0/11:"]
    assert result["eth0/11:"]["samples"] == [
        {"t": 12.5, "packets": 7, "drops": 2, "bytes": 900, "overlimits": 1, "backlog_bytes": 40,
         "qlen": 0}
    ]
    assert result["eth0/11:"]["latest"]["device_id"] == "lab"


@needs_node
def test_recording_does_not_change_the_history_it_was_given():
    unchanged = run(
        "(() => { const h = feed([[0, {impairment: {loss_percent: 5}}]]);"
        " const before = JSON.stringify(h);"
        " recordImpactSample(h, {queues: [entry({impairment: {loss_percent: 5}, packets: 3})]}, 5);"
        " return before === JSON.stringify(h); })()"
    )
    assert unchanged is True


@needs_node
def test_queues_without_any_impairment_are_left_out():
    assert run("rowsOf([[0, {}], [10, {packets: 100}]])") == []
    row = two({"reorder_percent": 5}, "packets: 100")
    assert row["verdict"] == "unmeasured"
    for field in (
        "delay_ms", "jitter_ms", "loss_percent", "bandwidth_kbit", "reorder_percent",
        "duplicate_percent", "corrupt_percent",
    ):
        rows = run(
            f"rowsOf([[0, {{impairment: {{{field}: 1}}}}], [10, {{impairment: {{{field}: 1}}}}]])"
        )
        assert len(rows) == 1, field


@needs_node
def test_rows_are_sorted_by_name_then_upload_before_download():
    result = run(
        "(() => { const imp = {loss_percent: 5};"
        " const h = recordImpactSample({}, {queues: ["
        " entry({name: 'b', direction: 'upload', handle: '11:', impairment: imp}),"
        " entry({name: 'a', direction: 'upload', handle: '12:', impairment: imp}),"
        " entry({name: 'b', direction: 'download', interface: 'wlan0', handle: '11:', impairment: imp}),"
        " entry({name: 'a', direction: 'download', interface: 'wlan0', handle: '12:', impairment: imp})"
        "]}, 0);"
        " return impactRows(h).map((row) => row.name + '/' + row.direction); })()"
    )
    assert result == ["a/upload", "a/download", "b/upload", "b/download"]


@needs_node
def test_rows_carry_the_configured_impairment_and_a_key():
    row = two({"loss_percent": 5}, "packets: 0")
    assert row["key"] == "eth0/11:"
    assert row["configured"]["loss_percent"] == 5


def test_the_functions_never_touch_the_page():
    source = served_functions()
    assert "document." not in source
    assert "innerHTML" not in source

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
    backlog_bytes: 0, qlen: 0};
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


@needs_node
def test_constants_match_the_plan():
    assert run("[IMPACT_WINDOW_SECONDS, IMPACT_MIN_PACKETS, IMPACT_CAP_RATIO, IMPACT_MAX_SAMPLES]") == [
        60, 50, 0.9, 12,
    ]


@needs_node
def test_the_specified_loss_example():
    (row,) = run(
        "rowsOf([[0, {impairment: {loss_percent: 5}}],"
        " [60, {impairment: {loss_percent: 5}, packets: 629, drops: 31}]])"
    )
    assert row["lines"] == ["Loss: set 5% · actual 4.7% (31 of 660 packets dropped)"]
    assert (row["offered"], row["drops"], row["lossPercent"]) == (660, 31, 4.7)
    assert row["verdict"] == "affected"
    assert row["windowSeconds"] == 60
    assert (row["name"], row["direction"]) == ("Lab", "upload")


@needs_node
def test_a_whole_percent_prints_without_a_decimal_and_no_traffic_gives_zero():
    (row,) = run(
        "rowsOf([[0, {impairment: {loss_percent: 20}}],"
        " [10, {impairment: {loss_percent: 20}, packets: 80, drops: 20}]])"
    )
    assert row["lines"] == ["Loss: set 20% · actual 20% (20 of 100 packets dropped)"]
    (idle,) = run(
        "rowsOf([[0, {impairment: {loss_percent: 20}}], [10, {impairment: {loss_percent: 20}}]])"
    )
    assert idle["lossPercent"] == 0
    assert idle["verdict"] == "idle"


@needs_node
def test_loss_line_only_appears_when_loss_is_set_or_packets_dropped():
    (quiet,) = run(
        "rowsOf([[0, {impairment: {delay_ms: 50}}],"
        " [10, {impairment: {delay_ms: 50}, packets: 100}]])"
    )
    assert not any(line.startswith("Loss") for line in quiet["lines"])
    (overflow,) = run(
        "rowsOf([[0, {impairment: {bandwidth_kbit: 1000}}],"
        " [10, {impairment: {bandwidth_kbit: 1000}, packets: 90, drops: 10}]])"
    )
    assert overflow["lines"][0] == "Loss: set 0% · actual 10% (10 of 100 packets dropped)"


@needs_node
@pytest.mark.parametrize(
    "bytes_sent, reached",
    [(kbit_bytes(900), True), (kbit_bytes(901), True), (6_743_750, False), (kbit_bytes(500), False)],
)
def test_cap_is_reached_at_ninety_percent_of_the_limit(bytes_sent, reached):
    (row,) = run(
        "rowsOf([[0, {impairment: {bandwidth_kbit: 1000}}],"
        f" [60, {{impairment: {{bandwidth_kbit: 1000}}, packets: 100, bytes: {bytes_sent}}}]])"
    )
    assert row["capReached"] is reached
    assert row["verdict"] == ("affected" if reached else "no-effect")
    assert ("cap reached" in row["lines"][0]) is reached
    assert ("below the cap" in row["lines"][0]) is not reached


@needs_node
def test_speed_line_formats_both_units():
    (row,) = run(
        "rowsOf([[0, {impairment: {bandwidth_kbit: 2000}}],"
        f" [60, {{impairment: {{bandwidth_kbit: 2000}}, packets: 100, bytes: {kbit_bytes(1960)}}}]])"
    )
    assert row["lines"][0] == "Speed: set 2 Mbit/s · using 1.96 Mbit/s · cap reached"
    assert row["lines"][1:] == ["Queue wait: about 0 ms"]
    assert row["rateKbit"] == 1960
    (slow,) = run(
        "rowsOf([[0, {impairment: {bandwidth_kbit: 640}}],"
        f" [60, {{impairment: {{bandwidth_kbit: 640}}, packets: 100, bytes: {kbit_bytes(100)}}}]])"
    )
    assert slow["lines"] == ["Speed: set 640 kbit/s · using 100 kbit/s · below the cap"]


@needs_node
def test_a_cap_without_traffic_is_never_reached():
    (row,) = run(
        "rowsOf([[0, {impairment: {bandwidth_kbit: 1000}}],"
        " [60, {impairment: {bandwidth_kbit: 1000}}]])"
    )
    assert row["capReached"] is False
    assert row["verdict"] == "idle"


@needs_node
def test_a_cap_is_not_reached_by_bytes_alone_without_any_offered_packet():
    (row,) = run(
        "rowsOf([[0, {impairment: {bandwidth_kbit: 1000}}],"
        f" [60, {{impairment: {{bandwidth_kbit: 1000}}, bytes: {kbit_bytes(2000)}}}]])"
    )
    assert (row["offered"], row["capReached"], row["verdict"]) == (0, False, "idle")


@needs_node
def test_queue_wait_only_for_a_cap_that_is_reached_without_delay_or_jitter():
    def rows(impairment, bytes_sent=kbit_bytes(1000), backlog=40_000):
        return run(
            f"rowsOf([[0, {{impairment: {json.dumps(impairment)}}}],"
            f" [60, {{impairment: {json.dumps(impairment)}, packets: 100,"
            f" bytes: {bytes_sent}, backlog_bytes: {backlog}}}]])"
        )[0]

    capped = rows({"bandwidth_kbit": 1000})
    assert capped["queueWaitMs"] == 320
    assert "Queue wait: about 320 ms" in capped["lines"]
    assert capped["lines"].index("Queue wait: about 320 ms") == 1

    assert rows({"bandwidth_kbit": 1000, "delay_ms": 50})["queueWaitMs"] is None
    assert rows({"bandwidth_kbit": 1000, "jitter_ms": 5})["queueWaitMs"] is None
    below = rows({"bandwidth_kbit": 1000}, bytes_sent=kbit_bytes(300))
    assert below["queueWaitMs"] is None
    assert not any(line.startswith("Queue wait") for line in below["lines"])
    assert rows({"loss_percent": 10})["queueWaitMs"] is None


@needs_node
def test_delay_line_with_and_without_jitter_is_marked_not_measured():
    def lines(impairment):
        return run(
            f"rowsOf([[0, {{impairment: {json.dumps(impairment)}}}],"
            f" [10, {{impairment: {json.dumps(impairment)}, packets: 100}}]])"
        )[0]["lines"]

    assert lines({"delay_ms": 100, "jitter_ms": 20}) == [
        "Delay: set 100 ms ± 20 · configured, not measured"
    ]
    assert lines({"delay_ms": 100}) == ["Delay: set 100 ms · configured, not measured"]
    assert lines({"jitter_ms": 15}) == ["Delay: set 0 ms ± 15 · configured, not measured"]
    assert not any(line.startswith("Delay") for line in lines({"loss_percent": 5}))


@needs_node
def test_lines_come_in_the_documented_order():
    impairment = {"loss_percent": 5, "bandwidth_kbit": 1000, "delay_ms": 0}
    (row,) = run(
        f"rowsOf([[0, {{impairment: {json.dumps(impairment)}}}],"
        f" [60, {{impairment: {json.dumps(impairment)}, packets: 190, drops: 10,"
        f" bytes: {kbit_bytes(1000)}, backlog_bytes: 12500}}]])"
    )
    assert [line.split(":")[0] for line in row["lines"]] == ["Loss", "Speed", "Queue wait"]
    impairment = {"loss_percent": 5, "bandwidth_kbit": 1000, "delay_ms": 30}
    (row,) = run(
        f"rowsOf([[0, {{impairment: {json.dumps(impairment)}}}],"
        f" [60, {{impairment: {json.dumps(impairment)}, packets: 190, drops: 10,"
        f" bytes: {kbit_bytes(1000)}}}]])"
    )
    assert [line.split(":")[0] for line in row["lines"]] == ["Loss", "Speed", "Delay"]


@needs_node
@pytest.mark.parametrize(
    "counters, expected",
    [
        ("drops: 1, packets: 10", "affected"),
        ("packets: 0", "idle"),
        ("packets: 49", "too-few"),
        ("packets: 50", "no-effect"),
        ("packets: 500", "no-effect"),
        ("packets: 10, drops: 39", "affected"),
        ("packets: 20, drops: 29", "affected"),
    ],
)
def test_verdicts(counters, expected):
    (row,) = run(
        "rowsOf([[0, {impairment: {loss_percent: 5}}],"
        f" [10, {{impairment: {{loss_percent: 5}}, {counters}}}]])"
    )
    assert row["verdict"] == expected


@needs_node
def test_forty_nine_offered_packets_is_too_few_and_fifty_is_enough():
    (few,) = run(
        "rowsOf([[0, {impairment: {delay_ms: 20}}],"
        " [10, {impairment: {delay_ms: 20}, packets: 49}]])"
    )
    (enough,) = run(
        "rowsOf([[0, {impairment: {delay_ms: 20}}],"
        " [10, {impairment: {delay_ms: 20}, packets: 50}]])"
    )
    assert (few["offered"], few["verdict"]) == (49, "too-few")
    assert (enough["offered"], enough["verdict"]) == (50, "no-effect")


@needs_node
def test_one_sample_or_samples_at_one_instant_are_still_collecting():
    (single,) = run("rowsOf([[0, {impairment: {loss_percent: 5}}]])")
    assert single["verdict"] == "collecting"
    assert single["lines"] == ["Collecting…"]
    (same_instant,) = run(
        "rowsOf([[5, {impairment: {loss_percent: 5}}],"
        " [5, {impairment: {loss_percent: 5}, packets: 10}]])"
    )
    assert same_instant["verdict"] == "collecting"
    assert same_instant["rateKbit"] == 0


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
    (row,) = run(
        "rowsOf([[0, {impairment: {loss_percent: 5}, packets: 900}],"
        " [5, {impairment: {loss_percent: 5}, packets: 3}],"
        " [10, {impairment: {loss_percent: 5}, packets: 103, drops: 5}]])"
    )
    assert (row["offered"], row["drops"], row["windowSeconds"]) == (105, 5, 5)


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
    (row,) = run(
        "rowsOf([[0, {impairment: {loss_percent: 5}, packets: 0}],"
        " [100, {impairment: {loss_percent: 5}, packets: 1000}],"
        " [130, {impairment: {loss_percent: 5}, packets: 1100, drops: 4}]])"
    )
    assert (row["offered"], row["drops"], row["windowSeconds"]) == (104, 4, 30)
    (exact,) = run(
        "rowsOf([[0, {impairment: {loss_percent: 5}, packets: 0}],"
        " [60, {impairment: {loss_percent: 5}, packets: 100}]])"
    )
    assert (exact["offered"], exact["windowSeconds"]) == (100, 60)
    (outside,) = run(
        "rowsOf([[0, {impairment: {loss_percent: 5}, packets: 0}],"
        " [61, {impairment: {loss_percent: 5}, packets: 100}]])"
    )
    assert outside["verdict"] == "collecting"


@needs_node
def test_at_most_twelve_samples_are_kept_per_queue():
    result = run(
        "(() => { const h = feed(Array.from({length: 20}, (_, i) =>"
        " [i * 5, {impairment: {loss_percent: 5}, packets: i}]));"
        " const samples = h['eth0/11:'].samples;"
        " return [samples.length, samples[0].t, samples[11].t]; })()"
    )
    assert result == [12, 40, 95]


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
        {"t": 12.5, "packets": 7, "drops": 2, "bytes": 900, "overlimits": 1, "backlog_bytes": 40}
    ]
    assert result["eth0/11:"]["latest"]["device_id"] == "lab"


@needs_node
def test_recording_does_not_change_the_history_it_was_given():
    changed = run(
        "(() => { const h = feed([[0, {impairment: {loss_percent: 5}}]]);"
        " const before = JSON.stringify(h);"
        " recordImpactSample(h, {queues: [entry({impairment: {loss_percent: 5}, packets: 3})]}, 5);"
        " return before === JSON.stringify(h); })()"
    )
    assert changed is True


@needs_node
def test_queues_without_any_impairment_are_left_out():
    assert run("rowsOf([[0, {}], [10, {packets: 100}]])") == []
    (row,) = run("rowsOf([[0, {impairment: {reorder_percent: 5}}], [10, {impairment: {reorder_percent: 5}, packets: 100}]])")
    assert row["verdict"] == "no-effect"
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
    (row,) = run(
        "rowsOf([[0, {impairment: {loss_percent: 5}}], [10, {impairment: {loss_percent: 5}}]])"
    )
    assert row["key"] == "eth0/11:"
    assert row["configured"]["loss_percent"] == 5


def test_the_functions_never_touch_the_page():
    source = served_functions()
    assert "document." not in source
    assert "innerHTML" not in source

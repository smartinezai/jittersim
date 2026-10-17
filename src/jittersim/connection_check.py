"""Check one device's connection with a short burst of ping probes.

See docs/implementation-plan-connection-check-2026-10-17.md. The Pi sends a bounded burst of
ICMP echo probes to one device and counts what comes back. This module holds the arithmetic only:
the binomial interval, what a profile should give, the statistics of a burst, the verdicts and the
export. It has no HTTP, database or socket code. The probe sender is `IcmpProber` in
`jittersim.speed_test` and the routes are in `jittersim.api.connection_checks`.

What a probe passes (from the renderer, proved by tests/kernel/test_connection_check.py): the
Pi's echo request to a device with its own profile passes that device's download queue. The
device's answer to the Pi passes no upload shaping. So only the download side of the profile can
be checked, and a profile shared by everyone is not applied to the Pi's own packets at all.
"""

import csv
import io
import json
import math
import random
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_PROBES = 100
MIN_PROBES = 20
MAX_PROBES = 300
PROBE_INTERVAL_SECONDS = 0.1
# A normal ping is 56 bytes of data. With the ICMP, IP and Ethernet headers a probe is 98 bytes.
PAYLOAD_BYTES = 56
FRAME_BYTES = PAYLOAD_BYTES + 8 + 20 + 14
MIN_GRACE_SECONDS = 1.0
MAX_GRACE_SECONDS = 10.0
# Waits for late replies when no profile is expected (a plain Wi-Fi round trip is milliseconds)
# and when the delay is not known.
PLAIN_GRACE_SECONDS = 2.0
UNKNOWN_GRACE_SECONDS = 3.0

# 95 percent: a number matches when the expected range overlaps this interval.
# 99 percent: a number is called less or more only when the expected range lies outside it.
Z_MATCH = 1.96
Z_CLEAR = 2.576
# Netem flips one bit of a probe frame. The device drops the frame unless the bit is in the source
# MAC address (6 of 98 bytes), so at least 90 percent of the corrupted probes show up as lost.
CORRUPT_DROP_FLOOR = 0.9
# A healthy Wi-Fi link loses, duplicates or reorders about 0 to 2 percent of pings by itself.
WIFI_SHARE_ALLOWANCE = 0.02
# The Wi-Fi hop adds a few milliseconds. The slack is the larger of this and a share of the delay.
WIFI_DELAY_FLOOR_MS = 30.0
WIFI_DELAY_SHARE = 0.3
# Netem and the timer resolution make a round trip a little shorter than the set delay at times.
DELAY_LOW_SLACK_MS = 2.0
MIN_EXPECTED_EVENTS = 3
MIN_REPLIES_FOR_DELAY = 5
MIN_REPLIES_FOR_JITTER = 20
MIN_PAIRS_FOR_JITTER = 10
JITTER_LOW_FACTOR = 0.6
JITTER_HIGH_FACTOR = 1.4
JITTER_SLACK_MS = 5.0
REORDER_LOW_FACTOR = 0.7
REORDER_HIGH_FACTOR = 1.3
SIMULATED_PROBES = 4000
SIMULATION_SEED = 20261017
# A device that answers slower than this is probably saving power, not shaped.
SLOW_ROUND_TRIP_MS = 100.0
# What a burst of probes cannot measure. The grace time never passes MAX_GRACE_SECONDS, so a delay
# plus jitter of more than 4.5 seconds puts valid answers outside the check. A cap below twice
# what the probes themselves need makes them queue up faster than they leave.
MAX_JUDGED_DELAY_MS = (MAX_GRACE_SECONDS - MIN_GRACE_SECONDS) / 2 * 1000
PROBE_DEMAND_KBIT = FRAME_BYTES * 8 / PROBE_INTERVAL_SECONDS / 1000
MIN_JUDGED_BANDWIDTH_KBIT = 2 * PROBE_DEMAND_KBIT

# The saved rows share the speed test table layout. The provider column names the probe.
CHECK_PROVIDER = "icmp-echo"

METRIC_IDS = ("loss", "delay", "jitter", "duplicate", "reorder", "corrupt", "cap")
# The numbers that can give a verdict. Corruption and the cap never do.
JUDGED_IDS = ("loss", "delay", "jitter", "duplicate", "reorder")
METRIC_LABELS = {
    "loss": "Packet loss",
    "delay": "Round trip delay",
    "jitter": "Jitter",
    "duplicate": "Duplicated answers",
    "reorder": "Answers out of order",
    "corrupt": "Corruption",
    "cap": "Speed cap",
}
STATUS_LABELS = {
    "matches": "Matches",
    "less": "Less than set",
    "more": "More than set",
    "extra": "Higher than set (link or device)",
    "borderline": "Close, but not sure",
    "inconclusive": "Not enough probes",
    "not-judged": "Not judged",
}
VERDICT_LABELS = {
    "matches": "Matches the profile",
    "less": "Less than set: the profile is not fully in effect",
    "more": "More than set: more impairment than the profile asks for",
    "differs": "Some numbers are less and some more than set",
    "borderline": "Close to the profile, but not sure: run it again with more probes",
    "inconclusive": "Not enough probes to tell the set values from no impairment",
    "baseline": "Baseline: no profile applies, so these numbers show the plain Wi-Fi path",
    "not-comparable": "Unshaped Wi-Fi round trip, not comparable with the profile",
    "service-rules": "Not judged: a service rule sends some traffic through another queue",
    "service-rules-unknown": "Not judged: the active service rules could not be confirmed",
    "out-of-range": "Not judged: the set delay or speed cap is beyond what a probe burst can measure",
    "unverified": "Not judged: the Pi could not confirm at the end that nothing changed",
    "fault": "Not judged: the check stopped after an error",
    "no-answer": "The device did not answer ping",
    "unavailable": "The Pi could not send probes",
    "changed": "Not judged: the profile, the Wi-Fi connection or the protection changed",
}
NO_ANSWER_NOTE = (
    "Many phones, TVs and PCs ignore ping (Windows blocks it by default), so this is not packet "
    "loss. Use Test speed or the Impact check to see the profile working on this device."
)
UPLOAD_NOTE = (
    "Only the download side of a profile can be checked this way: the Pi's probes pass the "
    "device's download queue, and the device's answers pass no upload queue. Use Test speed "
    "to check the upload."
)
SHARED_NOTE = (
    "This device is covered by a profile shared by everyone. The Pi's own packets are not shaped "
    "by it, so these numbers are the plain Wi-Fi round trip. Use Test speed to see the profile."
)
BASELINE_NOTE = (
    "No profile applies to this device, so these numbers show how the plain Wi-Fi path behaves. "
    "A healthy link loses 0 to 2 percent of pings and answers in a few milliseconds."
)
SERVICE_RULES_CHECK_NOTE = (
    "A service rule or service list applies to this device, so the Pi cannot be sure which queue "
    "its traffic takes. The numbers are kept but no verdict is given."
)
SERVICE_RULES_UNKNOWN_CHECK_NOTE = (
    "The active service rules could not be confirmed, so no verdict is given. The numbers are "
    "kept."
)
POWER_SAVE_NOTE = (
    "A phone that saves power can add up to about 200 ms to its first answers. Wake the device "
    "(turn on the screen or open an app) before checking."
)


# Statistics.

def wilson_interval(events: float, total: float, z: float) -> Tuple[float, float]:
    """The Wilson score interval of a binomial share. Valid at small counts and near 0 or 1."""

    if total <= 0:
        return 0.0, 1.0
    share = max(0.0, min(1.0, events / total))
    z2 = z * z
    denominator = 1 + z2 / total
    centre = (share + z2 / (2 * total)) / denominator
    margin = z * math.sqrt(share * (1 - share) / total + z2 / (4 * total * total)) / denominator
    # The ends are exact: no event gives a lower end of 0 and every event an upper end of 1.
    low = 0.0 if share <= 0 else max(0.0, centre - margin)
    high = 1.0 if share >= 1 else min(1.0, centre + margin)
    return low, high


def burst_inflation(loss_share: float, burst_packets: Optional[int]) -> float:
    """How much longer than independent loss a bursty loss makes the interval (variance factor).

    The renderer's Gilbert model loses every packet in the bad state. Successive probes are alike
    then, so the same number of probes tells less. This is the same formula as the kernel test
    `test_bursty_loss_keeps_the_configured_average`.
    """

    if not burst_packets or burst_packets < 2 or not 0 < loss_share < 1:
        return 1.0
    leave = 1 / burst_packets
    enter = loss_share * leave / (1 - loss_share)
    if enter > 1:
        enter, leave = 1.0, (1 - loss_share) / loss_share
    correlation = 1 - leave - enter
    return max(1.0, (1 + correlation) / (1 - correlation))


def percentile(values: Sequence[float], share: float) -> Optional[float]:
    """Nearest-rank percentile, or None for no values."""

    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def _overlaps(low_a: float, high_a: float, low_b: float, high_b: float) -> bool:
    # A tiny tolerance keeps rounding in the last digit from separating two ranges that touch.
    return low_a <= high_b + 1e-9 and low_b <= high_a + 1e-9


def share_status(
    events: int, total: int, band: Tuple[float, float], inflation: float = 1.0
) -> Tuple[str, Optional[str], Tuple[float, float]]:
    """Compare an observed share with the range the profile should give.

    Returns (status, direction, 95 percent interval). `direction` is `low` or `high` for a
    borderline status. A number matches when the 95 percent interval overlaps the expected range.
    It is less or more only when the expected range lies outside the 99 percent interval.
    """

    low, high = band
    effective = total / max(1.0, inflation)
    seen = events * effective / total if total else 0.0
    low95, high95 = wilson_interval(seen, effective, Z_MATCH)
    low99, high99 = wilson_interval(seen, effective, Z_CLEAR)
    interval = (low95, high95)
    if total <= 0:
        return "not-judged", None, interval
    if high99 < low - 1e-9:
        return "less", None, interval
    if low99 > high + 1e-9:
        return "more", None, interval
    if _overlaps(low95, high95, low, high):
        # A set value that the observed interval cannot tell from a healthy link is not proof.
        if low > 0 and _overlaps(low95, high95, 0.0, WIFI_SHARE_ALLOWANCE):
            return "inconclusive", None, interval
        return "matches", None, interval
    return "borderline", ("low" if high95 < low else "high"), interval


def probes_needed(
    set_share: float, *, answered_factor: float = 1.0, inflation: float = 1.0
) -> Optional[int]:
    """The fewest probes whose expected count of events clears the healthy-link allowance.

    The count is what the profile should give: with this many probes the 95 percent interval of
    the expected share lies above the Wi-Fi allowance, so the set value can be told from none.
    Returns None when more than MAX_PROBES would be needed.
    """

    if set_share <= 0:
        return None
    for count in range(MIN_PROBES, MAX_PROBES + 1):
        effective = count * answered_factor / max(1.0, inflation)
        if effective <= 0:
            continue
        low, _ = wilson_interval(set_share * effective, effective, Z_MATCH)
        if low > WIFI_SHARE_ALLOWANCE:
            return count
    return None


# What the profile should give.

def _share(percent: Any) -> float:
    return max(0.0, min(1.0, float(percent or 0) / 100.0))


def unanswered_share(loss: float, duplicate: float, drop: float) -> float:
    """The share of probes that get no answer when losses and drops are independent.

    `drop` is the share of delivered copies the device drops because netem corrupted them.
    Netem decides duplication first. With no duplicate the probe is lost with probability L, or
    delivered and dropped. When a duplicated probe is also lost one copy still goes on (it only
    loses the loss decision), so that copy is dropped with probability `drop` alone. When the
    original is not lost its clone goes through the queue again with its own loss and
    corruption decisions, and the probe is unanswered only if both copies are gone.
    """

    single = loss + (1 - loss) * drop
    return (1 - duplicate) * single + duplicate * (
        loss * drop + (1 - loss) * drop * single)


def expected_loss_range(loss: float, duplicate: float, corrupt: float) -> Tuple[float, float]:
    """The share of probes that get no answer (shares from 0 to 1).

    A duplicated and lost packet still leaves one copy, so duplication lowers the loss. A
    corrupted frame is dropped by the device unless the flipped bit is in the source MAC
    address: at least 90 percent of the corrupted copies and at most all of them. The high end
    also allows a healthy Wi-Fi link.
    """

    low = unanswered_share(loss, duplicate, CORRUPT_DROP_FLOOR * corrupt)
    high = unanswered_share(loss, duplicate, corrupt)
    return low, min(1.0, high + WIFI_SHARE_ALLOWANCE)


def expected_duplicate_range(loss: float, duplicate: float) -> Tuple[float, float]:
    """The share of answered probes that were answered twice.

    A probe is answered at least once with probability 1 - (1 - Pd) * L and twice with
    Pd * (1 - L) squared (the clone gets its own loss decision), so among the answered probes
    the share is a little under Pd when loss is set.
    """

    answered = 1 - (1 - duplicate) * loss
    twice = duplicate * (1 - loss) ** 2
    low = twice / answered if answered > 0 else 0.0
    return min(low, duplicate), min(1.0, duplicate + WIFI_SHARE_ALLOWANCE)


def serialization_ms(bandwidth_kbit: Optional[float]) -> float:
    """How long a probe frame takes to send at the set speed cap (kbit/s are bits per ms)."""

    return FRAME_BYTES * 8 / bandwidth_kbit if bandwidth_kbit else 0.0


def window_limit(delay_ms: float, jitter_ms: float, bandwidth_kbit: Optional[float]
                 ) -> Optional[str]:
    """Why a burst cannot measure this profile, or None when it can."""

    if delay_ms + jitter_ms > MAX_JUDGED_DELAY_MS:
        return (f"The set delay of {int(delay_ms + jitter_ms)} ms is longer than the check waits "
                "for answers, so valid answers would be counted as lost.")
    if bandwidth_kbit and bandwidth_kbit < MIN_JUDGED_BANDWIDTH_KBIT:
        return (f"The set speed cap of {bandwidth_kbit} kbit/s is lower than the probes need "
                f"(about {PROBE_DEMAND_KBIT:.0f} kbit/s), so they would queue up and show as loss.")
    return None


def waits_for_late_copies(impairment: Optional[Dict[str, Any]]) -> bool:
    """A duplicated packet can arrive after every probe has its first answer: wait the full grace."""

    return bool(impairment and impairment.get("duplicate_percent"))


def expected_delay_range(delay_ms: float, jitter_ms: float, bandwidth_kbit: Optional[float]
                         ) -> Tuple[float, float]:
    """The median round trip the profile should give. Only the request passes the delay."""

    low = max(0.0, delay_ms - jitter_ms)
    high = (delay_ms + jitter_ms + serialization_ms(bandwidth_kbit)
            + max(WIFI_DELAY_FLOOR_MS, WIFI_DELAY_SHARE * delay_ms))
    return low, high


def simulated_timing(
    delay_ms: float, jitter_ms: float, reorder_share: float, loss_share: float = 0.0,
    interval: float = PROBE_INTERVAL_SECONDS, count: int = SIMULATED_PROBES,
) -> Dict[str, float]:
    """What netem should do to probes one interval apart: jitter and the share reordered.

    Delay and jitter are uniform around the set delay and never negative. A rate (the renderer
    always sets one with jitter) keeps delayed packets in order. With reordering a share of
    packets skips the delay and jumps to the head of the queue, so they overtake delayed ones. A
    lost packet never enters the queue, and an answer that never comes is no evidence of
    reordering, so only the packets that get through count (jitter over probes in a row that
    both got through, reordering among the answered). The random numbers are seeded, so the
    answer is the same every time.
    """

    generator = random.Random(SIMULATION_SEED)
    step = interval * 1000.0
    last_delayed = float("-inf")
    arrivals: List[Tuple[float, int]] = []
    round_trips: Dict[int, float] = {}
    for index in range(count):
        if loss_share and generator.random() < loss_share:
            continue
        sent = index * step
        if reorder_share and delay_ms > 0 and generator.random() < reorder_share:
            arrival = sent
        else:
            delay = delay_ms
            if jitter_ms:
                delay = max(0.0, generator.uniform(delay_ms - jitter_ms, delay_ms + jitter_ms))
                arrival = max(sent + delay, last_delayed)
                last_delayed = arrival
            else:
                arrival = sent + delay
                last_delayed = max(last_delayed, arrival)
        arrivals.append((arrival, index))
        round_trips[index] = arrival - sent
    differences = [abs(round_trips[index + 1] - round_trips[index])
                   for index in round_trips if index + 1 in round_trips]
    highest = -1
    reordered = 0
    for _, index in sorted(arrivals):
        if index < highest:
            reordered += 1
        else:
            highest = index
    return {
        "jitter_ms": sum(differences) / len(differences) if differences else 0.0,
        "reorder_share": reordered / len(arrivals) if arrivals else 0.0,
    }


# The statistics of one burst.

def probe_statistics(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts and round trip numbers from the probe records, in the order they were sent.

    A record has `rtt_ms` (None when no answer came), `copies` (how many answers) and `arrival`
    (the order the first answers arrived in).
    """

    sent = len(records)
    answered = [(index, record) for index, record in enumerate(records)
                if record.get("rtt_ms") is not None]
    round_trips = [record["rtt_ms"] for _, record in answered]
    twice = sum(1 for _, record in answered if (record.get("copies") or 1) > 1)
    extra = sum(max(0, (record.get("copies") or 1) - 1) for _, record in answered)
    # A probe is out of order when its answer arrives after the answer to a probe sent later.
    ordered = sorted(
        ((record["arrival"], index) for index, record in answered
         if record.get("arrival") is not None))
    highest, reordered = -1, 0
    for _, index in ordered:
        if index < highest:
            reordered += 1
        else:
            highest = index
    # Jitter: the average change of the round trip between two probes sent one after another.
    by_index = {index: record["rtt_ms"] for index, record in answered}
    differences = [abs(by_index[index + 1] - by_index[index])
                   for index in by_index if index + 1 in by_index]
    return {
        "sent": sent,
        "received": len(answered),
        "lost": sent - len(answered),
        "answered_twice": twice,
        "extra_copies": extra,
        "reordered": reordered,
        "rtt_min_ms": _round(min(round_trips), 2) if round_trips else None,
        "rtt_mean_ms": _round(sum(round_trips) / len(round_trips), 2) if round_trips else None,
        "rtt_median_ms": _round(percentile(round_trips, 0.5), 2),
        "rtt_p95_ms": _round(percentile(round_trips, 0.95), 2),
        "rtt_max_ms": _round(max(round_trips), 2) if round_trips else None,
        "jitter_ms": _round(sum(differences) / len(differences), 2) if differences else None,
        "jitter_pairs": len(differences),
    }


def _round(value: Optional[float], digits: int = 1) -> Optional[float]:
    return None if value is None else round(value, digits)


def _percent_text(share: Optional[float]) -> str:
    if share is None:
        return "-"
    text = f"{round(share * 100, 1):.1f}"
    return text[:-2] if text.endswith(".0") else text


def _ms_text(value: Optional[float]) -> str:
    if value is None:
        return "-"
    text = f"{value:.1f}"
    return (text[:-2] if text.endswith(".0") else text) + " ms"


def _row(metric: str, set_text: str, measured: str, status: str, sentence: str,
         direction: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": metric, "label": METRIC_LABELS[metric], "set": set_text, "measured": measured,
        "status": status, "status_label": STATUS_LABELS[status], "direction": direction,
        "sentence": sentence,
    }


def grace_seconds(impairment: Optional[Dict[str, Any]], *, shaped: bool) -> float:
    """How long to wait for late answers after the last probe."""

    if impairment is None:
        return UNKNOWN_GRACE_SECONDS if shaped else PLAIN_GRACE_SECONDS
    reach = (impairment.get("delay_ms") or 0) + (impairment.get("jitter_ms") or 0)
    return max(MIN_GRACE_SECONDS, min(MAX_GRACE_SECONDS, 2 * reach / 1000 + 1))


def run_probe_burst(
    prober, count: int, *, interval: float = PROBE_INTERVAL_SECONDS, grace: float,
    clock: Callable[[], float], sleep: Callable[[float], None],
    stop: Callable[[], bool] = lambda: False, deadline: Optional[float] = None,
    settle: bool = False, on_tick: Optional[Callable[[], bool]] = None,
) -> bool:
    """Send `count` probes one interval apart, then wait up to `grace` seconds for late answers.

    Returns False when `stop()` ended it early. The loop never passes `deadline` (monotonic
    time) whatever the prober does, and it listens in steps of one interval so a stop is seen
    within 100 ms. The prober needs `send()`, `collect(until)` and `counts()`. With `settle` the
    wait ends only when the grace time is over, because a duplicate can still be on its way
    after every probe has its first answer. `on_tick()` runs after every probe and ends the burst
    early by returning True (the caller found that the profile changed).
    """

    def over() -> bool:
        return stop() or (deadline is not None and clock() >= deadline)

    next_at = clock()
    for _ in range(count):
        if over():
            return not stop()
        prober.send()
        next_at += interval
        prober.collect(until=next_at)
        remaining = next_at - clock()
        if remaining > 0:
            # A prober that cannot wait (no socket) still has to keep the pace.
            sleep(remaining)
        if on_tick is not None and on_tick():
            return not stop()
    end = clock() + grace
    for _ in range(int(grace / interval) + 2):
        sent, received = prober.counts()
        if over() or (received >= sent and not settle) or clock() >= end:
            break
        until = min(end, clock() + interval)
        prober.collect(until=until)
        remaining = until - clock()
        if remaining > 0:
            sleep(remaining)
    return not stop()


def _impairment_text(impairment: Dict[str, Any]) -> Dict[str, Any]:
    return {key: impairment.get(key) for key in (
        "delay_ms", "jitter_ms", "loss_percent", "duplicate_percent", "reorder_percent",
        "corrupt_percent", "bandwidth_kbit", "loss_burst_packets")}


class _Burst:
    """What the profile sets and what the burst saw, for the per-number rows.

    `reason` says why no number is judged (no profile shapes the probes, the result is withheld),
    or is None when the numbers are compared with the profile.
    """

    def __init__(self, stats: Dict[str, Any], setting: Dict[str, Any], *,
                 reason: Optional[str], burst_packets: Optional[int]):
        self.stats = stats
        self.setting = setting
        self.reason = reason
        self.burst_packets = burst_packets
        self.sent, self.received = stats["sent"], stats["received"]
        self.judging = reason is None and self.sent > 0
        self.loss = _share(setting.get("loss_percent"))
        self.duplicate = _share(setting.get("duplicate_percent"))
        self.corrupt = _share(setting.get("corrupt_percent"))
        self.reorder = _share(setting.get("reorder_percent"))
        self.delay = float(setting.get("delay_ms") or 0)
        self.jitter = float(setting.get("jitter_ms") or 0)
        self.bandwidth = setting.get("bandwidth_kbit")
        self.inflation = burst_inflation(self.loss, burst_packets)
        # Loss that is independent from one probe to the next: no burst, with the length known.
        self.independent_loss = burst_packets is not None and burst_packets < 2
        self.notes: List[str] = []

    def percent_set(self, key: str, share: float) -> str:
        return f"{self.setting.get(key) or 0}%" if share else "Not set"

    def unjudged(self, metric: str, set_text: str, measured: str, why: str = "") -> Dict[str, Any]:
        """A row that is shown with its numbers but gets no status."""

        if not self.sent:
            why = "No probe was sent."
        elif self.reason:
            why = "Not judged: " + self.reason
        return _row(metric, set_text, measured, "not-judged", why or "Not judged.")


def _need_text(need: Optional[int]) -> str:
    if need:
        return f"Use at least {need} probes to tell the set value from none."
    return f"More than {MAX_PROBES} probes would be needed to tell the set value from none."


def _share_sentence(status: str, direction: Optional[str], expected: float, noun: str,
                    need: Optional[int]) -> str:
    if status == "matches":
        return (f"The share of {noun} matches the profile." if expected
                else f"No more {noun} than a healthy link has.")
    if status == "less":
        return f"Fewer {noun} than the profile sets."
    if status == "more":
        return f"More {noun} than the profile sets."
    if status == "borderline":
        return ("Probably " + ("fewer" if direction == "low" else "more") + f" {noun} than set, "
                "but not by enough to be sure. Run it again with more probes.")
    return "No clear difference from none yet. " + _need_text(need)


def _loss_row(burst: _Burst) -> Tuple[Dict[str, Any], Optional[int]]:
    stats, sent = burst.stats, burst.sent
    band = expected_loss_range(burst.loss, burst.duplicate, burst.corrupt)
    status, direction, interval = share_status(stats["lost"], sent, band, burst.inflation)
    measured = (
        f"{_percent_text(stats['lost'] / sent if sent else None)}% "
        f"({stats['lost']} of {sent} lost, 95% range {_percent_text(interval[0])} to "
        f"{_percent_text(interval[1])}%)")
    set_text = burst.percent_set("loss_percent", burst.loss)
    if burst.burst_packets and burst.burst_packets >= 2 and burst.loss:
        set_text += f", in bursts of about {burst.burst_packets}"
    affects_loss = bool(burst.loss or burst.corrupt)
    need = probes_needed(band[0], inflation=burst.inflation) if affects_loss else None
    if not burst.judging:
        return burst.unjudged("loss", set_text, measured), need
    if burst.duplicate and burst.corrupt and burst.loss and not burst.independent_loss:
        return _row("loss", set_text, measured, "not-judged",
                    "Not judged: duplication, corruption and bursty loss together make the "
                    "loss uncertain."), need
    if status == "less":
        sentence = "Less loss than the profile sets, so the loss is not fully in effect."
    elif status == "more":
        sentence = ("More loss than the profile sets. The Wi-Fi link or the device may be "
                    "losing packets too.")
    elif status == "matches" and not affects_loss:
        sentence = "No more loss than a healthy Wi-Fi link has."
    else:
        sentence = _share_sentence(status, direction, band[0], "loss", need)
    return _row("loss", set_text, measured, status, sentence, direction), need


def _delay_row(burst: _Burst) -> Dict[str, Any]:
    stats, delay, jitter = burst.stats, burst.delay, burst.jitter
    median = stats["rtt_median_ms"]
    measured = ("no answers" if median is None else
                f"median {_ms_text(median)} (lowest {_ms_text(stats['rtt_min_ms'])}, "
                f"95th percentile {_ms_text(stats['rtt_p95_ms'])})")
    set_text = (f"{int(delay)} ms" + (f" plus or minus {int(jitter)} ms" if jitter else "")
                if delay else "Not set")
    if median is not None and median > SLOW_ROUND_TRIP_MS and not delay:
        burst.notes.append(POWER_SAVE_NOTE)
    if not burst.judging:
        return burst.unjudged("delay", set_text, measured)
    if burst.reorder:
        return _row("delay", set_text, measured, "not-judged",
                    "Not judged: the profile reorders packets, so some probes skip the delay.")
    if not delay:
        return _row("delay", set_text, measured, "not-judged",
                    "No delay is set. A plain Wi-Fi link answers in a few milliseconds.")
    if burst.received < MIN_REPLIES_FOR_DELAY:
        return _row("delay", set_text, measured, "not-judged",
                    "Too few answers to judge the delay.")
    low, high = expected_delay_range(delay, jitter, burst.bandwidth)
    if median < low - DELAY_LOW_SLACK_MS:
        return _row("delay", set_text, measured, "less",
                    "The round trip is below the shortest delay the profile allows, so the "
                    "delay is not in effect.")
    if median > high:
        # The floor holds, so the delay is in effect. The rest is the link or the device.
        burst.notes.append(POWER_SAVE_NOTE)
        return _row("delay", set_text, measured, "extra",
                    f"The delay is in effect. The round trip is about {_ms_text(median - delay)} "
                    "above it, which the Wi-Fi link or the device adds. This is not counted "
                    "against the profile.")
    return _row("delay", set_text, measured, "matches",
                "The round trip is in the range the set delay gives.")


def _jitter_row(burst: _Burst) -> Tuple[Dict[str, Any], Optional[float]]:
    stats, jitter = burst.stats, burst.jitter
    measured = ("-" if stats["jitter_ms"] is None else
                f"{_ms_text(stats['jitter_ms'])} average change between two probes")
    set_text = f"{int(jitter)} ms" if jitter else "Not set"
    if not burst.judging:
        return burst.unjudged("jitter", set_text, measured), None
    if burst.reorder:
        return _row("jitter", set_text, measured, "not-judged",
                    "Not judged: the profile reorders packets, so some probes skip the delay."
                    ), None
    if not jitter:
        return _row("jitter", set_text, measured, "not-judged", "No jitter is set."), None
    if burst.duplicate or burst.corrupt:
        return _row("jitter", set_text, measured, "not-judged",
                    "Not judged: duplication or corruption changes which answer comes first, "
                    "so the jitter cannot be compared."), None
    if burst.received < MIN_REPLIES_FOR_JITTER or stats["jitter_pairs"] < MIN_PAIRS_FOR_JITTER:
        return _row("jitter", set_text, measured, "not-judged",
                    "Too few answers in a row to judge the jitter."), None
    expected = simulated_timing(burst.delay, jitter, 0.0, burst.loss)["jitter_ms"]
    about = f" (about {_ms_text(expected)})"
    low = JITTER_LOW_FACTOR * expected
    high = JITTER_HIGH_FACTOR * expected + JITTER_SLACK_MS
    if stats["jitter_ms"] < low:
        status = "less"
        sentence = "The round trip varies less than the profile's jitter gives" + about + "."
    elif stats["jitter_ms"] > high:
        status = "extra"
        sentence = ("The jitter is in effect. The round trip varies more than the profile gives"
                    + about + ", which the Wi-Fi link or the device adds. This is not counted "
                    "against the profile.")
    else:
        status = "matches"
        sentence = "The round trip varies as much as the profile's jitter gives" + about + "."
    return _row("jitter", set_text, measured, status, sentence), expected


def _duplicates_are_predictable(burst: _Burst) -> bool:
    """The exact share of doubled answers needs independent loss and no corruption.

    With bursty loss or corruption the clone and the original are not independent (the clone can
    be lost in the same burst or damaged), so the share cannot be stated.
    """

    if burst.corrupt:
        return False
    return not burst.loss or burst.independent_loss


def _duplicate_row(burst: _Burst) -> Tuple[Dict[str, Any], Optional[int]]:
    stats, received = burst.stats, burst.received
    band = expected_duplicate_range(burst.loss, burst.duplicate)
    status, direction, interval = share_status(stats["answered_twice"], received, band)
    measured = (
        f"{_percent_text(stats['answered_twice'] / received if received else None)}% "
        f"({stats['answered_twice']} of {received} answered twice, 95% range "
        f"{_percent_text(interval[0])} to {_percent_text(interval[1])}%)")
    set_text = burst.percent_set("duplicate_percent", burst.duplicate)
    need = (probes_needed(band[0], answered_factor=max(0.0, 1 - burst.loss))
            if burst.duplicate and not burst.corrupt else None)
    if not burst.judging:
        return burst.unjudged("duplicate", set_text, measured), need
    if not received:
        return _row("duplicate", set_text, measured, "not-judged", "No probe was answered."), need
    if burst.duplicate and not _duplicates_are_predictable(burst):
        return _row("duplicate", set_text, measured, "not-judged",
                    "Not judged: duplication is set together with bursty loss, an unknown burst "
                    "length or corruption, which makes the share of doubled answers uncertain."
                    ), need
    sentence = _share_sentence(status, direction, burst.duplicate, "duplicated answers", need)
    return _row("duplicate", set_text, measured, status, sentence, direction), need


def _reorder_row(burst: _Burst) -> Dict[str, Any]:
    stats, received = burst.stats, burst.received
    measured = (f"{_percent_text(stats['reordered'] / received if received else None)}% "
                f"({stats['reordered']} of {received} out of order)")
    set_text = burst.percent_set("reorder_percent", burst.reorder)
    if not burst.judging:
        return burst.unjudged("reorder", set_text, measured)
    if not received:
        return _row("reorder", set_text, measured, "not-judged", "No probe was answered.")
    if burst.duplicate or burst.corrupt:
        return _row("reorder", set_text, measured, "not-judged",
                    "Not judged: duplication or corruption changes which answer comes first, "
                    "so the order cannot be compared.")
    expected = (simulated_timing(burst.delay, burst.jitter, burst.reorder,
                                 burst.loss)["reorder_share"]
                if burst.reorder and burst.delay else 0.0)
    if burst.reorder and received * expected < MIN_EXPECTED_EVENTS:
        why = ("reordering needs a delay longer than the 100 ms between probes, so probes "
               "cannot overtake each other here." if burst.delay <= PROBE_INTERVAL_SECONDS * 1000
               else "too few probes would be reordered to tell. Use more probes.")
        return _row("reorder", set_text, measured, "not-judged", "Not judged: " + why)
    band = ((REORDER_LOW_FACTOR * expected,
             min(1.0, REORDER_HIGH_FACTOR * expected + WIFI_SHARE_ALLOWANCE))
            if expected else (0.0, WIFI_SHARE_ALLOWANCE))
    status, direction, _ = share_status(stats["reordered"], received, band)
    sentence = _share_sentence(status, direction, expected, "answers out of order", None)
    return _row("reorder", set_text, measured, status, sentence, direction)


def build_summary(
    records: Sequence[Dict[str, Any]], *, planned: int, source: str, protected: bool = False,
    impairment: Optional[Dict[str, Any]] = None, burst_packets: Optional[int] = None,
    service_rules: Optional[bool] = False, changed_reason: Optional[str] = None,
    unverified_reason: Optional[str] = None, fault_reason: Optional[str] = None,
    probe_status: str = "measured", probe_reason: Optional[str] = None,
    notes: Iterable[str] = (), seconds: Optional[float] = None,
    profile_unchanged: Optional[bool] = None, grace: Optional[float] = None,
) -> Dict[str, Any]:
    """Every number of a finished burst, the judgement of each and the overall verdict.

    `source` is where the profile lives: `device` (its own queues), `everyone`, `everyone-else` or
    `none`. `impairment` is the applied download impairment of the device's own queue. It is
    only given for a device with its own profile that is not protected: nothing else is shaped.
    `service_rules` is True or None when a service rule applies or could not be confirmed, which
    withholds the verdict for a shaped device. `changed_reason` and `unverified_reason` say that
    the profile changed during the burst or that the end state could not be read: no verdict.
    """

    stats = probe_statistics(records)
    shaped = source == "device" and not protected and impairment is not None
    setting = impairment or {}
    too_much = (window_limit(float(setting.get("delay_ms") or 0), float(
        setting.get("jitter_ms") or 0), setting.get("bandwidth_kbit")) if shaped else None)
    withheld, reason = None, None
    # A change or a fault comes first for every run: a baseline of a device that left the Wi-Fi
    # or whose profile was applied during the burst is not a baseline.
    if changed_reason is not None:
        withheld, reason = "changed", changed_reason
    elif fault_reason is not None:
        withheld, reason = "fault", fault_reason
    elif not shaped:
        reason = "no profile shapes the Pi's probes for this device."
    elif unverified_reason is not None:
        withheld, reason = "unverified", unverified_reason
    elif too_much is not None:
        withheld, reason = "out-of-range", too_much
    elif service_rules is True:
        withheld, reason = "service-rules", "a service rule applies to this device."
    elif service_rules is None:
        withheld, reason = "service-rules-unknown", "the service rules could not be confirmed."
    burst = _Burst(stats, setting, reason=reason, burst_packets=burst_packets)
    loss_row, loss_need = _loss_row(burst)
    delay_row = _delay_row(burst)
    jitter_row, expected_jitter = _jitter_row(burst)
    duplicate_row, duplicate_need = _duplicate_row(burst)
    rows = [loss_row, delay_row, jitter_row, duplicate_row, _reorder_row(burst)]
    if burst.corrupt:
        rows.append(_row("corrupt", f"{setting.get('corrupt_percent')}%", "counted in the loss",
                         "not-judged", "A damaged ping is dropped by the device, so corruption "
                         "shows up as loss."))
    if burst.bandwidth:
        rows.append(_row("cap", f"{burst.bandwidth} kbit/s", "not tested by probes", "not-judged",
                         f"A burst of probes is about {PROBE_DEMAND_KBIT:.0f} kbit/s, far below "
                         "the cap. Use Test speed to check the cap."))
    statuses = [row["status"] for row in rows if row["id"] in JUDGED_IDS]
    # The numbers the profile really sets. Only these can show that the profile is in effect: a
    # number that is not set matching "nothing" says only that the link is healthy.
    set_ids = {name for name, value in (
        ("loss", burst.loss or burst.corrupt), ("delay", burst.delay), ("jitter", burst.jitter),
        ("duplicate", burst.duplicate), ("reorder", burst.reorder)) if value}
    confirmed = [row["status"] for row in rows if row["id"] in set_ids]
    verdict = _overall(
        statuses, confirmed, received=stats["received"], sent=stats["sent"], source=source,
        shaped=shaped, protected=protected, withheld=withheld, probe_status=probe_status,
        loss_status=loss_row["status"], only_cap=bool(burst.bandwidth) and not set_ids)
    notes = list(notes) + burst.notes
    if verdict == "no-answer":
        notes.insert(0, NO_ANSWER_NOTE)
    elif verdict == "not-comparable":
        notes.insert(0, SHARED_NOTE)
    elif verdict == "baseline":
        notes.insert(0, BASELINE_NOTE)
    elif verdict in ("changed", "fault", "unverified", "out-of-range"):
        notes.insert(0, reason or "")
    if verdict == "inconclusive" and burst.bandwidth and not set_ids:
        notes.append("This profile sets only a speed cap, which a burst of probes cannot "
                     "measure. Use Test speed to check it.")
    if shaped:
        notes.append(UPLOAD_NOTE)
        if burst.loss and burst_packets is None:
            notes.append("The burst length of the set loss is not known, so the loss range may "
                         "be narrower than it should be.")
    needs = [(burst.loss or burst.corrupt, loss_need), (burst.duplicate, duplicate_need)]
    wanted = [need for share, need in needs if share and need]
    out_of_reach = any(share and need is None for share, need in needs)
    if verdict in ("inconclusive", "borderline") and (wanted or out_of_reach):
        notes.append(_need_text(max(wanted)) if wanted and not out_of_reach
                     else _need_text(None))
    return {
        "verdict": verdict,
        "verdict_text": VERDICT_LABELS[verdict],
        "metrics": rows,
        "probes": {
            "planned": planned, "sent": stats["sent"], "received": stats["received"],
            "lost": stats["lost"],
            "loss_percent": _round(100 * stats["lost"] / stats["sent"]) if stats["sent"] else None,
            "answered_twice": stats["answered_twice"], "extra_copies": stats["extra_copies"],
            "reordered": stats["reordered"],
        },
        "rtt": {key: stats[key] for key in (
            "rtt_min_ms", "rtt_mean_ms", "rtt_median_ms", "rtt_p95_ms", "rtt_max_ms",
            "jitter_ms")},
        "expected": _impairment_text(setting) if shaped else None,
        "expected_jitter_ms": _round(expected_jitter, 2),
        "burst_packets": burst_packets,
        "probes_needed": max(wanted) if wanted else None,
        "probes_needed_over_max": out_of_reach,
        "shaped": shaped,
        "protected": protected,
        "service_rules": service_rules,
        "probe_status": probe_status,
        "probe_reason": probe_reason,
        "profile_unchanged": profile_unchanged,
        "grace_seconds": grace,
        "seconds": seconds,
        "notes": _unique(notes),
    }


def _unique(values: Iterable[str]) -> List[str]:
    seen: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def _overall(statuses: Sequence[str], confirmed: Sequence[str], *, received: int, sent: int,
             source: str, shaped: bool, protected: bool, withheld: Optional[str],
             probe_status: str, loss_status: str, only_cap: bool) -> str:
    if withheld in ("changed", "fault", "unverified"):
        return withheld
    if probe_status == "unavailable":
        return "unavailable"
    if withheld == "out-of-range":
        return withheld
    if sent > 0 and received == 0 and loss_status != "matches":
        return "no-answer"
    if source in ("everyone", "everyone-else"):
        return "not-comparable"
    if protected or source == "none" or not shaped:
        return "baseline"
    if withheld is not None:
        return withheld
    less, more = "less" in statuses, "more" in statuses
    if less and more:
        return "differs"
    if more:
        return "more"
    if less:
        return "less"
    if "borderline" in statuses:
        return "borderline"
    if only_cap:
        return "inconclusive"
    if confirmed:
        # A delay or jitter above the set value still shows that the profile is in effect.
        return "matches" if any(status in ("matches", "extra") for status in confirmed
                                ) else "inconclusive"
    # The profile sets nothing: the link looks as healthy as that.
    return "matches" if "matches" in statuses else "inconclusive"


# Saved results: the table of the export.

CHECK_EXPORT_COLUMNS = (
    "id", "started_at", "finished_at", "device_name", "mac_address", "label", "status", "source",
    "protected", "profile_name",
    "set_loss_percent", "set_delay_ms", "set_jitter_ms", "set_duplicate_percent",
    "set_reorder_percent", "set_corrupt_percent", "set_bandwidth_kbit", "set_burst_packets",
    "probes_planned", "probes_sent", "probes_received", "loss_percent", "answered_twice",
    "reordered", "rtt_min_ms", "rtt_mean_ms", "rtt_median_ms", "rtt_p95_ms", "rtt_max_ms",
    "jitter_ms", "verdict", "loss_status", "delay_status", "jitter_status", "duplicate_status",
    "reorder_status",
)


def check_export_row(result: Dict[str, Any]) -> Dict[str, Any]:
    summary = result.get("summary") or {}
    profile = result.get("profile") or {}
    probes = summary.get("probes") or {}
    rtt = summary.get("rtt") or {}
    expected = summary.get("expected") or {}
    row: Dict[str, Any] = {
        key: result.get(key)
        for key in ("id", "started_at", "finished_at", "device_name", "mac_address", "label",
                    "status", "source", "protected")
    }
    row["profile_name"] = profile.get("profile_name")
    for key, name in (("loss_percent", "set_loss_percent"), ("delay_ms", "set_delay_ms"),
                      ("jitter_ms", "set_jitter_ms"),
                      ("duplicate_percent", "set_duplicate_percent"),
                      ("reorder_percent", "set_reorder_percent"),
                      ("corrupt_percent", "set_corrupt_percent"),
                      ("bandwidth_kbit", "set_bandwidth_kbit"),
                      ("loss_burst_packets", "set_burst_packets")):
        row[name] = expected.get(key)
    row["probes_planned"] = probes.get("planned")
    row["probes_sent"] = probes.get("sent")
    row["probes_received"] = probes.get("received")
    row["loss_percent"] = probes.get("loss_percent")
    row["answered_twice"] = probes.get("answered_twice")
    row["reordered"] = probes.get("reordered")
    for key in ("rtt_min_ms", "rtt_mean_ms", "rtt_median_ms", "rtt_p95_ms", "rtt_max_ms",
                "jitter_ms"):
        row[key] = rtt.get(key)
    row["verdict"] = summary.get("verdict")
    statuses = {metric.get("id"): metric.get("status") for metric in summary.get("metrics") or []}
    for metric in ("loss", "delay", "jitter", "duplicate", "reorder"):
        row[f"{metric}_status"] = statuses.get(metric)
    return row


def _csv_cell(value: Any) -> Any:
    # A spreadsheet runs a cell that starts with one of these as a formula.
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return "" if value is None else value


def check_export_csv(results: Sequence[Dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CHECK_EXPORT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for result in results:
        writer.writerow({key: _csv_cell(value) for key, value in check_export_row(result).items()})
    return output.getvalue()


def check_export_json(results: Sequence[Dict[str, Any]]) -> str:
    return json.dumps(
        {"format": "jittersim-connection-checks", "version": 1, "results": list(results)},
        indent=2, allow_nan=False,
    ) + "\n"

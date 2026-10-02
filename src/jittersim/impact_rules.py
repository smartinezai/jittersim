"""The Impact card's verdict rules in Python for the terminal command `jittersim impact`.

The dashboard computes each row in the browser from queue counter samples five seconds
apart. These functions copy `impactSample` up to `impactRows` of the dashboard script in
`api/server.py` one to one with the same constants and wording. The terminal takes its own
samples and feeds them in here. `tests/unit/test_impact_rules.py` checks the numbers of the
dashboard's node tests and runs both versions on the same samples.
"""

import json
from typing import Any, Dict, List

from jittersim.remote_client import js_number, js_round

IMPACT_WINDOW_SECONDS = 60
IMPACT_MIN_PACKETS = 50
IMPACT_CAP_RATIO = 0.9
IMPACT_MAX_SAMPLES = 13
IMPACT_MIN_EXPECTED_DROPS = 3
IMPACT_MIN_QUEUE_WAIT_MS = 10
IMPACT_NOT_MEASURED = " · configured, not measured"

IMPACT_VERDICT_LABELS = {
    "affected": "Affected in the last minute",
    "idle": "No traffic in the last minute",
    "too-few": "Too few packets to judge",
    "clean": "No drops or cap hit seen",
    "unmeasured": "Effect not measured here",
    "collecting": "Collecting…",
}
IMPACT_NOTHING_SLOWED = (
    "Nothing is slowed down right now. Apply a profile to a device to see its impact here."
)
IMPACT_NOTE = (
    "Counts come from the Pi's queues. Delay, jitter, reorder, duplicate and corrupt are shown "
    "as configured because queue counters cannot measure them."
)

_IMPAIRMENT_FIELDS = (
    "delay_ms", "jitter_ms", "loss_percent", "bandwidth_kbit", "reorder_percent",
    "duplicate_percent", "corrupt_percent",
)


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _stringify(value: Any) -> str:
    """JSON.stringify for comparing two impairments: keys keep their order."""

    return json.dumps(value, separators=(",", ":"))


def impact_sample(entry: Dict[str, Any], now_seconds: float) -> Dict[str, Any]:
    """impactSample."""

    return {
        "t": now_seconds,
        "packets": entry.get("packets"),
        "drops": entry.get("drops"),
        "bytes": entry.get("bytes"),
        "overlimits": entry.get("overlimits"),
        "backlog_bytes": entry.get("backlog_bytes"),
        "qlen": entry.get("qlen") or 0,
    }


def impact_queue_replaced(
    previous: Dict[str, Any], entry: Dict[str, Any], sample: Dict[str, Any]
) -> bool:
    """impactQueueReplaced: a new apply or other counters start the history again."""

    samples = previous["samples"]
    last = samples[-1] if samples else None
    latest = previous["latest"]
    return (
        not last
        or sample["t"] <= last["t"]
        or sample["packets"] < last["packets"]
        or sample["drops"] < last["drops"]
        or sample["bytes"] < last["bytes"]
        or latest.get("generation") != entry.get("generation")
        or latest.get("device_id") != entry.get("device_id")
        or latest.get("direction") != entry.get("direction")
        or _stringify(latest.get("impairment")) != _stringify(entry.get("impairment"))
    )


def record_impact_sample(
    history: Dict[str, Any], response: Dict[str, Any], now_seconds: float
) -> Dict[str, Any]:
    """recordImpactSample: add one /api/impact answer to the history without changing it."""

    following: Dict[str, Any] = {}
    for entry in response.get("queues") or []:
        key = str(entry.get("interface")) + "/" + str(entry.get("handle"))
        sample = impact_sample(entry, now_seconds)
        previous = history.get(key)
        kept = (
            previous["samples"]
            if previous and not impact_queue_replaced(previous, entry, sample)
            else []
        )
        following[key] = {
            "latest": entry,
            "samples": (list(kept) + [sample])[-IMPACT_MAX_SAMPLES:],
        }
    return following


def impact_has_no_impairment(impairment: Dict[str, Any]) -> bool:
    """impactHasNoImpairment."""

    return not any(impairment.get(field) for field in _IMPAIRMENT_FIELDS)


def format_impact_rate(kbit: Any) -> str:
    """formatImpactRate."""

    if kbit >= 1000:
        return js_number(js_round(kbit / 10) / 100) + " Mbit/s"
    return js_number(kbit) + " kbit/s"


def impact_intervals(used: List[Dict[str, Any]]) -> List[Dict[str, float]]:
    """impactIntervals: what each step between two samples offered and moved."""

    intervals = []
    for index in range(1, len(used)):
        before = used[index - 1]
        after = used[index]
        seconds = after["t"] - before["t"]
        if seconds <= 0:
            continue
        intervals.append({
            "offered": after["packets"] - before["packets"] + after["drops"] - before["drops"],
            "kbit": (after["bytes"] - before["bytes"]) * 8 / 1000 / seconds,
        })
    return intervals


def impact_verdict(
    impairment: Dict[str, Any], offered: float, packets: float, drops: float, cap_reached: bool
) -> str:
    """impactVerdict."""

    if drops > 0 or cap_reached:
        return "affected"
    if offered == 0 and packets == 0:
        return "idle"
    has_loss = _positive(impairment.get("loss_percent"))
    if not has_loss and not impairment.get("bandwidth_kbit"):
        return "unmeasured"
    if offered < IMPACT_MIN_PACKETS or (
        has_loss and offered * impairment["loss_percent"] / 100 < IMPACT_MIN_EXPECTED_DROPS
    ):
        return "too-few"
    return "clean"


def impact_loss_line(
    impairment: Dict[str, Any], drops: float, offered: float, loss_percent: float
) -> str:
    """impactLossLine."""

    if _positive(impairment.get("loss_percent")):
        head = "Loss: set " + js_number(impairment["loss_percent"]) + "%"
    elif impairment.get("bandwidth_kbit"):
        head = "Dropped (queue full at the speed cap)"
    else:
        head = "Dropped (queue full)"
    return (
        head + " · actual " + js_number(loss_percent) + "% (" + js_number(drops) + " of "
        + js_number(offered) + " packets dropped)"
        + (
            " (may read low because duplicated packets are counted)"
            if _positive(impairment.get("duplicate_percent"))
            else ""
        )
    )


def impact_row(key: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """impactRow: the figures and the verdict of one queue over the last minute."""

    latest = entry["latest"]
    impairment = latest["impairment"]
    row: Dict[str, Any] = {
        "key": key,
        "name": latest.get("name"),
        "direction": latest.get("direction"),
        "configured": impairment,
        "windowSeconds": 0,
        "offered": 0,
        "packets": 0,
        "drops": 0,
        "lossPercent": 0,
        "rateKbit": 0,
        "peakKbit": 0,
        "capReached": False,
        "queueWaitMs": None,
        "verdict": "collecting",
        "lines": ["Collecting…"],
    }
    samples = entry["samples"]
    newest = samples[-1]
    used = [sample for sample in samples if newest["t"] - sample["t"] <= IMPACT_WINDOW_SECONDS]
    first = used[0]
    seconds = newest["t"] - first["t"]
    if len(used) < 2 or seconds <= 0:
        return row
    packets = newest["packets"] - first["packets"]
    drops = newest["drops"] - first["drops"]
    # Drops are counted when a packet arrives and packets when they leave. A packet that
    # still waits in the queue has arrived so the queue length change is added.
    waiting = newest["qlen"] - first["qlen"]
    offered = max(0, packets + drops + waiting)
    intervals = impact_intervals(used)
    limit = impairment.get("bandwidth_kbit")
    no_delay = not impairment.get("delay_ms") and not impairment.get("jitter_ms")
    # Without a delay a backlog of at least 10 ms of cap time can only come from the cap.
    backlog_seen = bool(limit) and no_delay and any(
        js_round(sample["backlog_bytes"] * 8 / limit) >= IMPACT_MIN_QUEUE_WAIT_MS
        for sample in used
    )
    cap_reached = bool(limit) and (
        backlog_seen
        or any(
            interval["offered"] > 0 and js_round(interval["kbit"]) >= IMPACT_CAP_RATIO * limit
            for interval in intervals
        )
    )
    row["windowSeconds"] = seconds
    row["offered"] = offered
    row["packets"] = packets
    row["drops"] = drops
    row["lossPercent"] = js_round(drops * 1000 / offered) / 10 if offered else 0
    row["rateKbit"] = js_round((newest["bytes"] - first["bytes"]) * 8 / 1000 / seconds)
    row["peakKbit"] = js_round(max([0] + [interval["kbit"] for interval in intervals]))
    row["capReached"] = cap_reached
    row["queueWaitMs"] = (
        js_round(newest["backlog_bytes"] * 8 / limit) if limit and no_delay and cap_reached
        else None
    )
    row["verdict"] = impact_verdict(impairment, offered, packets, drops, cap_reached)
    lines: List[str] = []
    if row["verdict"] == "unmeasured":
        lines.append(
            js_number(packets) + (" packet" if packets == 1 else " packets")
            + " passed in the last minute"
        )
    if _positive(impairment.get("loss_percent")) or drops > 0:
        lines.append(impact_loss_line(impairment, drops, offered, row["lossPercent"]))
    if limit:
        lines.append(
            "Speed: set " + format_impact_rate(limit) + " · using "
            + format_impact_rate(row["rateKbit"]) + " · peak " + format_impact_rate(row["peakKbit"])
            + (" · cap reached" if cap_reached else " · below the cap")
        )
    if row["queueWaitMs"] is not None and row["queueWaitMs"] >= IMPACT_MIN_QUEUE_WAIT_MS:
        lines.append("Queue wait now: about " + js_number(row["queueWaitMs"]) + " ms")
    if _positive(impairment.get("delay_ms")) or _positive(impairment.get("jitter_ms")):
        jitter = impairment.get("jitter_ms")
        lines.append(
            "Delay: set " + js_number(impairment.get("delay_ms")) + " ms"
            + (" ± " + js_number(jitter) if _positive(jitter) else "")
            + IMPACT_NOT_MEASURED
        )
    for field, word in (
        ("reorder_percent", "Reorder"),
        ("duplicate_percent", "Duplicate"),
        ("corrupt_percent", "Corrupt"),
    ):
        if _positive(impairment.get(field)):
            lines.append(word + ": set " + js_number(impairment[field]) + "%" + IMPACT_NOT_MEASURED)
    row["lines"] = lines
    return row


def _name_key(name: Any) -> tuple:
    """Close to the browser's localeCompare for ordinary device names: case is ignored first."""

    text = str(name)
    return (text.casefold(), text.swapcase())


def impact_rows(history: Dict[str, Any]) -> List[Dict[str, Any]]:
    """impactRows: every queue with an impairment by name and upload before download."""

    rows = [
        impact_row(key, history[key])
        for key in history
        if not impact_has_no_impairment(history[key]["latest"]["impairment"])
    ]
    return sorted(
        rows, key=lambda row: (_name_key(row["name"]), 0 if row["direction"] == "upload" else 1)
    )


def verdict_label(verdict: str) -> str:
    return IMPACT_VERDICT_LABELS[verdict]


def two_sample_rows(first: Dict[str, Any], second: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Feed two /api/impact answers through the rules in order."""

    history = record_impact_sample({}, first, first["sampled_at"])
    history = record_impact_sample(history, second, second["sampled_at"])
    return impact_rows(history)

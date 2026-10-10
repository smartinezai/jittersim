"""Measure one device's speed through the shaped path while it runs a public speed test.

See docs/implementation-plan-device-speed-test-2026-10-09.md. The Pi cannot run a speed test
as the phone, so the person opens a public speed test page on the device and the Pi watches
the device's queues once per second with the same read-only counters as the Impact card. This
module holds the arithmetic and the small ICMP prober. It has no HTTP and no database code.
"""

import csv
import io
import ipaddress
import json
import math
import os
import select
import socket
import struct
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

DEFAULT_DURATION_SECONDS = 45
MIN_DURATION_SECONDS = 15
MAX_DURATION_SECONDS = 90
SAMPLE_INTERVAL_SECONDS = 1.0
MAX_SAMPLES = MAX_DURATION_SECONDS + 2
MAX_ACTIVE_RUNS = 2
# A second counts as busy from this share of the run's peak, so idle seconds before and after
# the speed test do not pull the average down.
BUSY_SHARE_OF_PEAK = 0.1
MIN_BUSY_SECONDS = 3
MIN_BYTES = 64 * 1024
CAP_REACHED_RATIO = 0.9
CAP_EXCEEDED_RATIO = 1.1
MAX_PROBES_KEPT = 120

DIRECTIONS = ("download", "upload")
SOURCE_LABELS = {
    "device": "This device's own queues",
    "everyone": "Shared queue of the profile for everyone",
    "everyone-else": "Shared queue of Everyone else",
    "none": "Not measured on the Pi",
}
VERDICT_LABELS = {
    "too-little-traffic": "Too little traffic: run the speed test on the device during the window",
    "no-cap": "No speed limit set",
    "at-cap": "Matches the cap",
    "above-cap": "Faster than the cap: the limit did not hold",
    "below-cap": "Slower than the cap: the internet or the device was the limit",
    "not-measured": "Not measured on the Pi",
    "service-rules": "Not judged: a service rule sends some traffic through another queue",
}
# The verdicts that say how the speed compares with the cap. A service rule withholds them.
CAP_VERDICTS = frozenset(("at-cap", "above-cap", "below-cap"))
SHARED_SOURCES = ("everyone", "everyone-else")
SHARED_LATENCY_NOTE = (
    "The round trip does not include the profile's delay: the Pi's own ping does not pass "
    "the shared queue."
)
SERVICE_RULES_NOTE = (
    "A service rule or service list applies to this device, so some of its traffic goes through "
    "other queues. The reading covers the ordinary queue only. Compare the numbers typed in "
    "from the device's speed test page."
)

# The public speed test pages a person may open on the device. The QR codes are fixed because
# the addresses are fixed: each was made once with macOS CoreImage CIQRCodeGenerator (error
# correction M), checked by decoding it with CoreImage CIDetector, and stored here as one hex
# number per row without the quiet zone. The page draws them as SVG.
PROVIDERS: Tuple[Dict[str, Any], ...] = (
    {
        "id": "cloudflare",
        "name": "Cloudflare speed test",
        "url": "https://speed.cloudflare.com/",
        "note": "Free, no account. Cloudflare keeps each result for its own statistics.",
        "qr_size": 29,
        "qr_rows": (
            "1fcc427f", "10548341", "1744255d", "1747965d", "1751965d", "104b7e41",
            "1fd5557f", "000a2f00", "154d1f12", "1d15b1c9", "1ad879f7", "1b1fd982",
            "1f79694b", "021f69c9", "135c8dbb", "03012fca", "02c419cb", "0331b1cd",
            "13c67853", "0d3cdf6a", "105969f0", "001c6117", "1fce835b", "104d2d18",
            "17531df2", "1744b114", "1759fa39", "104b9f52", "1fd2b75b",
        ),
    },
    {
        "id": "librespeed",
        "name": "LibreSpeed",
        "url": "https://librespeed.org/",
        "note": "Open source, no account. Pick a server near you on the page.",
        "qr_size": 25,
        "qr_rows": (
            "1fc117f", "104f741", "175e35d", "1750a5d", "175795d", "1057f41", "1fd557f",
            "0015300", "17c2e7c", "07a6922", "10fbe0b", "16ae6f1", "1f6bfd7", "1f8452a",
            "1247b7b", "1132031", "106aff4", "0011118", "1fc0d57", "105551a", "1755bf7",
            "17588df", "1757c8d", "104a779", "1fdf03f",
        ),
    },
)
PROVIDER_IDS = tuple(provider["id"] for provider in PROVIDERS)
DEFAULT_PROVIDER = "cloudflare"


def provider_payload() -> List[Dict[str, Any]]:
    return [{**provider, "qr_rows": list(provider["qr_rows"])} for provider in PROVIDERS]


def percentile(values: Sequence[float], share: float) -> Optional[float]:
    """Nearest-rank percentile like jittersim.measure, or None for no values."""

    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def _round(value: Optional[float], digits: int = 1) -> Optional[float]:
    return None if value is None else round(value, digits)


def per_second_rates(samples: Sequence[Dict[str, Any]], direction: str) -> List[float]:
    """kbit/s between each pair of samples of one direction."""

    rates = []
    for before, after in zip(samples, samples[1:]):
        seconds = after["t"] - before["t"]
        if seconds <= 0:
            continue
        moved = after[direction]["bytes"] - before[direction]["bytes"]
        rates.append(max(0, moved) * 8 / 1000 / seconds)
    return rates


def cap_verdict(cap_kbit: Optional[float], median: float, p90: float) -> str:
    if not cap_kbit:
        return "no-cap"
    if median > CAP_EXCEEDED_RATIO * cap_kbit:
        return "above-cap"
    if p90 >= CAP_REACHED_RATIO * cap_kbit:
        return "at-cap"
    return "below-cap"


def summarize_direction(
    samples: Sequence[Dict[str, Any]], direction: str, cap_kbit: Optional[float]
) -> Dict[str, Any]:
    """What one direction moved during the window and how it compares with the cap."""

    rates = per_second_rates(samples, direction)
    summary: Dict[str, Any] = {
        "cap_kbit": cap_kbit,
        "seconds": len(rates),
        "bytes": 0,
        "packets": 0,
        "drops": 0,
        "peak_kbit": None,
        "average_kbit": None,
        "busy_seconds": 0,
        "busy_average_kbit": None,
        "median_kbit": None,
        "p90_kbit": None,
        "verdict": "too-little-traffic",
    }
    if len(samples) < 2:
        return summary
    first, last = samples[0][direction], samples[-1][direction]
    elapsed = samples[-1]["t"] - samples[0]["t"]
    summary["bytes"] = max(0, last["bytes"] - first["bytes"])
    summary["packets"] = max(0, last["packets"] - first["packets"])
    summary["drops"] = max(0, last["drops"] - first["drops"])
    if not rates or elapsed <= 0:
        return summary
    peak = max(rates)
    busy = [rate for rate in rates if peak > 0 and rate >= BUSY_SHARE_OF_PEAK * peak]
    summary.update(
        peak_kbit=_round(peak),
        average_kbit=_round(summary["bytes"] * 8 / 1000 / elapsed),
        busy_seconds=len(busy),
    )
    if busy:
        median = percentile(busy, 0.5)
        p90 = percentile(busy, 0.9)
        summary.update(
            busy_average_kbit=_round(sum(busy) / len(busy)),
            median_kbit=_round(median),
            p90_kbit=_round(p90),
        )
        if len(busy) >= MIN_BUSY_SECONDS and summary["bytes"] >= MIN_BYTES:
            summary["verdict"] = cap_verdict(cap_kbit, median, p90)
    return summary


def unmeasured_direction() -> Dict[str, Any]:
    return {"cap_kbit": None, "seconds": 0, "bytes": 0, "verdict": "not-measured"}


def summarize_probes(probes: Dict[int, Dict[str, Any]], status: str) -> Dict[str, Any]:
    """Round trip and loss from the Pi to the device over the test Wi-Fi."""

    sent = len(probes)
    replies = [probe["rtt_ms"] for probe in probes.values() if probe.get("rtt_ms") is not None]
    if status == "measured" and sent and not replies:
        status = "no-answer"
    return {
        "status": status,
        "sent": sent,
        "received": len(replies),
        "loss_percent": _round(100 * (sent - len(replies)) / sent) if sent else None,
        "rtt_min_ms": _round(min(replies), 2) if replies else None,
        "rtt_median_ms": _round(percentile(replies, 0.5), 2),
        "rtt_p90_ms": _round(percentile(replies, 0.9), 2),
        "rtt_max_ms": _round(max(replies), 2) if replies else None,
    }


COMPARE_FIELDS = (
    ("download_busy_kbit", ("summary", "download", "busy_average_kbit")),
    ("download_p90_kbit", ("summary", "download", "p90_kbit")),
    ("download_peak_kbit", ("summary", "download", "peak_kbit")),
    ("upload_busy_kbit", ("summary", "upload", "busy_average_kbit")),
    ("upload_p90_kbit", ("summary", "upload", "p90_kbit")),
    ("upload_peak_kbit", ("summary", "upload", "peak_kbit")),
    ("rtt_median_ms", ("summary", "latency", "rtt_median_ms")),
    ("loss_percent", ("summary", "latency", "loss_percent")),
    ("reported_download_mbit", ("reported", "download_mbit")),
    ("reported_upload_mbit", ("reported", "upload_mbit")),
    ("reported_latency_ms", ("reported", "latency_ms")),
    ("reported_jitter_ms", ("reported", "jitter_ms")),
)


def _lookup(result: Dict[str, Any], path: Sequence[str]) -> Optional[float]:
    value: Any = result
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def compare_results(baseline: Dict[str, Any], later: Dict[str, Any]) -> Dict[str, Any]:
    """The change in each number from a baseline result to a later one."""

    changes = []
    for name, path in COMPARE_FIELDS:
        before, after = _lookup(baseline, path), _lookup(later, path)
        change = None if before is None or after is None else round(after - before, 2)
        percent = (
            round(100 * (after - before) / before, 1)
            if change is not None and before not in (0, 0.0) else None
        )
        changes.append(
            {"field": name, "baseline": before, "result": after, "change": change,
             "percent": percent}
        )
    warnings = []
    if baseline.get("mac_address") != later.get("mac_address"):
        warnings.append("The two results are from different devices.")
    for result in (baseline, later):
        if result.get("source") in ("everyone", "everyone-else"):
            warnings.append(
                f"Result {result.get('id')} was measured on a shared queue: other devices' traffic "
                "is in the same counters."
            )
        if result.get("status") == "changed":
            warnings.append(f"The profile changed during result {result.get('id')}.")
        if result.get("status") == "fault":
            warnings.append(
                f"Result {result.get('id')} stopped after a fault and may be incomplete."
            )
        if (result.get("summary") or {}).get("service_rules"):
            warnings.append(
                f"Result {result.get('id')} had service rules in force: the cap conclusion is "
                "withheld. Use the numbers typed in from the device's speed test page."
            )
    return {
        "baseline": baseline,
        "result": later,
        "changes": changes,
        "verdicts": {
            direction: {
                "baseline": _verdict(baseline, direction),
                "result": _verdict(later, direction),
            }
            for direction in DIRECTIONS
        },
        "warnings": warnings,
    }


def _verdict(result: Dict[str, Any], direction: str) -> Optional[str]:
    summary = result.get("summary") or {}
    entry = summary.get(direction) if isinstance(summary, dict) else None
    verdict = entry.get("verdict") if isinstance(entry, dict) else None
    # Service rules put some traffic in other queues, so the cap conclusion cannot be trusted.
    if verdict in CAP_VERDICTS and summary.get("service_rules"):
        return "service-rules"
    return verdict


EXPORT_COLUMNS = (
    "id", "started_at", "finished_at", "device_name", "mac_address", "label", "status",
    "provider", "source", "protected", "profile_name", "applied_download_cap_kbit",
    "applied_upload_cap_kbit",
    "download_busy_kbit", "download_p90_kbit", "download_peak_kbit", "download_bytes",
    "download_drops", "download_verdict",
    "upload_busy_kbit", "upload_p90_kbit", "upload_peak_kbit", "upload_bytes", "upload_drops",
    "upload_verdict",
    "latency_status", "rtt_median_ms", "rtt_p90_ms", "loss_percent",
    "reported_download_mbit", "reported_upload_mbit", "reported_latency_ms", "reported_jitter_ms",
)


def export_row(result: Dict[str, Any]) -> Dict[str, Any]:
    summary = result.get("summary") or {}
    profile = result.get("profile") or {}
    reported = result.get("reported") or {}
    row: Dict[str, Any] = {
        key: result.get(key)
        for key in ("id", "started_at", "finished_at", "device_name", "mac_address", "label",
                    "status", "provider", "source", "protected")
    }
    row["profile_name"] = profile.get("profile_name")
    for direction in DIRECTIONS:
        values = summary.get(direction) or {}
        row[f"applied_{direction}_cap_kbit"] = values.get("cap_kbit")
        row[f"{direction}_busy_kbit"] = values.get("busy_average_kbit")
        row[f"{direction}_p90_kbit"] = values.get("p90_kbit")
        row[f"{direction}_peak_kbit"] = values.get("peak_kbit")
        row[f"{direction}_bytes"] = values.get("bytes")
        row[f"{direction}_drops"] = values.get("drops")
        row[f"{direction}_verdict"] = values.get("verdict")
    latency = summary.get("latency") or {}
    row["latency_status"] = latency.get("status")
    row["rtt_median_ms"] = latency.get("rtt_median_ms")
    row["rtt_p90_ms"] = latency.get("rtt_p90_ms")
    row["loss_percent"] = latency.get("loss_percent")
    for field in ("download_mbit", "upload_mbit", "latency_ms", "jitter_ms"):
        row[f"reported_{field}"] = reported.get(field)
    return row


def _csv_cell(value: Any) -> Any:
    # A spreadsheet runs a cell that starts with one of these as a formula.
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return "" if value is None else value


def export_csv(results: Sequence[Dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for result in results:
        writer.writerow({key: _csv_cell(value) for key, value in export_row(result).items()})
    return output.getvalue()


def export_json(results: Sequence[Dict[str, Any]]) -> str:
    return json.dumps(
        {"format": "jittersim-speed-tests", "version": 1, "results": list(results)},
        indent=2, allow_nan=False,
    ) + "\n"


# ICMP echo from the dashboard process. An unprivileged ICMP datagram socket needs no helper,
# no subprocess and no capability; Linux allows it for the groups in net.ipv4.ping_group_range
# (all groups by systemd's default). Nothing is sent unless the Pi itself has an address in the
# device's /24, so a development machine never pings a real home network.
ICMP_ECHO_REQUEST = 8
ICMP_ECHO_REPLY = 0


def icmp_checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def echo_request(sequence: int, nonce: bytes) -> bytes:
    header = struct.pack("!BBHHH", ICMP_ECHO_REQUEST, 0, 0, 0, sequence & 0xFFFF)
    checksum = icmp_checksum(header + nonce)
    return struct.pack("!BBHHH", ICMP_ECHO_REQUEST, 0, checksum, 0, sequence & 0xFFFF) + nonce


def parse_echo_reply(packet: bytes, nonce: bytes) -> Optional[int]:
    """The sequence number of one of our echo replies, or None."""

    # macOS hands the IP header over too; Linux gives the ICMP message alone.
    if len(packet) >= 20 and packet[0] >> 4 == 4:
        packet = packet[(packet[0] & 0x0F) * 4:]
    if len(packet) < 8 + len(nonce) or packet[0] != ICMP_ECHO_REPLY or packet[1] != 0:
        return None
    if packet[8:8 + len(nonce)] != nonce:
        return None
    return struct.unpack("!H", packet[6:8])[0]


def same_subnet(local: str, target: str, prefix: int = 24) -> bool:
    try:
        network = ipaddress.ip_network(f"{local}/{prefix}", strict=False)
        address = ipaddress.ip_address(target)
    except ValueError:
        return False
    return address in network and local != target


class IcmpProber:
    """Send one echo per tick to one device and collect the replies until the next tick."""

    def __init__(self, target: str, *, socket_factory: Callable[[], socket.socket] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.target = target
        self.clock = clock
        self.probes: Dict[int, Dict[str, Any]] = {}
        self.status = "measured"
        self.reason: Optional[str] = None
        self.nonce = os.urandom(8)
        self._sequence = 0
        self._socket: Optional[socket.socket] = None
        factory = socket_factory or (
            lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
        )
        try:
            ipaddress.IPv4Address(target)
            sock = factory()
        except (OSError, ValueError):
            self._unavailable("ICMP is not allowed for the dashboard")
            return
        try:
            sock.setblocking(False)
            # connect() on a datagram socket sends nothing. It picks the local address.
            sock.connect((target, 0))
            local = sock.getsockname()[0]
        except OSError:
            sock.close()
            self._unavailable("The device's address cannot be reached from the dashboard")
            return
        if not same_subnet(local, target):
            sock.close()
            self._unavailable("The dashboard is not on the device's network")
            return
        self._socket = sock

    def _unavailable(self, reason: str) -> None:
        self.status = "unavailable"
        self.reason = reason

    def send(self) -> None:
        if self._socket is None:
            return
        self._sequence = (self._sequence + 1) & 0xFFFF
        try:
            self._socket.send(echo_request(self._sequence, self.nonce))
        except OSError:
            # A full buffer or an unreachable host counts as a lost probe.
            pass
        self.probes[self._sequence] = {"sent": self.clock(), "rtt_ms": None}
        while len(self.probes) > MAX_PROBES_KEPT:
            self.probes.pop(next(iter(self.probes)))

    def collect(self, until: float) -> None:
        """Read replies until the given monotonic time."""

        if self._socket is None:
            return
        while True:
            remaining = until - self.clock()
            if remaining <= 0:
                return
            try:
                ready, _, _ = select.select([self._socket], [], [], remaining)
            except (OSError, ValueError):
                return
            if not ready:
                return
            try:
                packet = self._socket.recv(2048)
            except OSError:
                continue
            sequence = parse_echo_reply(packet, self.nonce)
            probe = self.probes.get(sequence) if sequence is not None else None
            if probe is not None and probe["rtt_ms"] is None:
                probe["rtt_ms"] = (self.clock() - probe["sent"]) * 1000

    def summary(self) -> Dict[str, Any]:
        # A copy: the sampling thread may still add a probe while a request ends the run.
        result = summarize_probes(dict(self.probes), self.status)
        if self.reason:
            result["reason"] = self.reason
        return result

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None


class NoProber:
    """Stands in when the device has no address or a test turns probing off."""

    def __init__(self, reason: str = "The device has no address yet"):
        self.reason = reason

    def send(self) -> None:
        pass

    def collect(self, until: float) -> None:
        pass

    def summary(self) -> Dict[str, Any]:
        return {**summarize_probes({}, "unavailable"), "reason": self.reason}

    def close(self) -> None:
        pass

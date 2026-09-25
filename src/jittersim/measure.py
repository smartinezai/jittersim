"""Measure delay, jitter and loss from a test device, and compare the runs.

Run it on a device connected to the JitterSim test Wi-Fi, once with a normal
profile and once with an impaired one. It needs only Python 3.9 and the
system ping command, so this file also runs on its own:

    python3 measure.py measure --label normal
    python3 measure.py compare jittersim-normal.json jittersim-3g.json
"""

import argparse
import csv
import io
import json
import math
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

MEASUREMENT_FORMAT = "jittersim-measurement"
MEASUREMENT_VERSION = 1
# The test Wi-Fi reaches only public addresses, not the router or the Pi's LAN.
DEFAULT_TARGET = "1.1.1.1"

_REPLY = re.compile(r"(?:icmp_)?seq=(\d+)\b.*?\btime[=<]([\d.]+) ?ms")
_TRANSMITTED = re.compile(r"(\d+) packets transmitted")
_COLUMNS = (
    "Run", "Target", "Sent", "Loss", "RTT avg", "RTT p95", "Jitter", "Reordered", "Download"
)


class MeasurementError(RuntimeError):
    """Raised when a measurement cannot run or a result file cannot be read."""


def parse_ping_output(output: str) -> List[Tuple[int, float]]:
    """Return (sequence, round trip in ms) for every reply, in arrival order."""

    return [(int(sequence), float(rtt)) for sequence, rtt in _REPLY.findall(output)]


def ping_command(
    target: str, *, count: int, interval: float, platform: str = sys.platform
) -> List[str]:
    """Build a ping command; macOS takes the reply wait in milliseconds."""

    wait = "2000" if platform == "darwin" else "2"
    return ["ping", "-n", "-c", str(count), "-i", f"{interval:g}", "-W", wait, target]


def _rounded(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)


def summarize(replies: Sequence[Tuple[int, float]], *, sent: int) -> Dict[str, Any]:
    """Summarize loss, round trips, jitter, reordering and duplicates."""

    first: Dict[int, float] = {}
    duplicates = 0
    reordered = 0
    highest: Optional[int] = None
    for sequence, rtt in replies:
        if sequence in first:
            duplicates += 1
            continue
        if highest is not None and sequence < highest:
            reordered += 1
        highest = sequence if highest is None else max(highest, sequence)
        first[sequence] = rtt
    in_send_order = [first[sequence] for sequence in sorted(first)]
    ordered = sorted(in_send_order)
    changes = [abs(later - earlier) for earlier, later in zip(in_send_order, in_send_order[1:])]
    received = len(first)
    return {
        "sent": sent,
        "received": received,
        "loss_percent": round(100 * (sent - received) / sent, 1) if sent else 0.0,
        "duplicates": duplicates,
        "reordered": reordered,
        "rtt_min_ms": _rounded(ordered[0] if ordered else None),
        "rtt_avg_ms": _rounded(statistics.fmean(ordered) if ordered else None),
        "rtt_median_ms": _rounded(statistics.median(ordered) if ordered else None),
        "rtt_p95_ms": _rounded(ordered[math.ceil(0.95 * len(ordered)) - 1] if ordered else None),
        "rtt_max_ms": _rounded(ordered[-1] if ordered else None),
        # The mean change between replies in the order they were sent.
        "jitter_ms": _rounded(statistics.fmean(changes) if changes else None),
    }


def measure_download(url: str, *, timeout: float = 30.0, max_bytes: int = 100 * 2**20) -> Dict:
    """Download a file and report the throughput a device experienced."""

    received = 0
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            while received < max_bytes:
                chunk = response.read(min(65_536, max_bytes - received))
                if not chunk:
                    break
                received += len(chunk)
    except (OSError, urllib.error.URLError) as error:
        raise MeasurementError(f"download failed: {error}") from error
    seconds = max(time.monotonic() - started, 1e-6)
    return {
        "url": url,
        "bytes": received,
        "seconds": round(seconds, 3),
        "mbit_per_s": round(received * 8 / seconds / 1_000_000, 2),
    }


def measure(
    target: str,
    *,
    count: int,
    interval: float,
    label: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    now: Optional[int] = None,
    platform: str = sys.platform,
    download_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Ping a target from this device and describe what came back."""

    started_at = int(time.time()) if now is None else now
    command = ping_command(target, count=count, interval=interval, platform=platform)
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            timeout=count * interval + 30,
            check=False,
        )
    except FileNotFoundError as error:
        raise MeasurementError("the ping command is not available") from error
    except subprocess.TimeoutExpired as error:
        raise MeasurementError("ping did not finish") from error
    # ping exits with 1 or 2 when replies are missing, so trust its summary line.
    transmitted = _TRANSMITTED.search(result.stdout)
    if transmitted is None:
        detail = (result.stderr or result.stdout).strip() or f"exit status {result.returncode}"
        raise MeasurementError(f"ping failed: {detail}")
    replies = parse_ping_output(result.stdout)
    run: Dict[str, Any] = {
        "format": MEASUREMENT_FORMAT,
        "version": MEASUREMENT_VERSION,
        "label": label,
        "target": target,
        "started_at": started_at,
        "count": count,
        "interval_seconds": interval,
        "samples": [{"sequence": sequence, "rtt_ms": rtt} for sequence, rtt in replies],
        "summary": summarize(replies, sent=int(transmitted.group(1))),
    }
    if download_url:
        run["download"] = measure_download(download_url)
    return run


def load_run(path: Path) -> Dict[str, Any]:
    """Read one saved measurement."""

    try:
        run = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MeasurementError(f"could not read {path}: {error}") from error
    if (
        not isinstance(run, dict)
        or run.get("format") != MEASUREMENT_FORMAT
        or run.get("version") != MEASUREMENT_VERSION
        or not isinstance(run.get("summary"), dict)
    ):
        raise MeasurementError(f"{path} is not a JitterSim measurement")
    return run


def _milliseconds(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.2f} ms"


def comparison_table(runs: Sequence[Dict[str, Any]], *, table_format: str = "markdown") -> str:
    """Put runs side by side as a Markdown or CSV table."""

    rows = []
    for run in runs:
        summary = run["summary"]
        download = run.get("download")
        rows.append(
            [
                str(run.get("label", "")),
                str(run.get("target", "")),
                str(summary.get("sent", "")),
                f"{summary.get('loss_percent', 0.0):.1f}%",
                _milliseconds(summary.get("rtt_avg_ms")),
                _milliseconds(summary.get("rtt_p95_ms")),
                _milliseconds(summary.get("jitter_ms")),
                str(summary.get("reordered", 0)),
                f"{download['mbit_per_s']:.2f} Mbit/s" if download else "—",
            ]
        )
    if table_format == "csv":
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(_COLUMNS)
        writer.writerows(rows)
        return output.getvalue()
    lines = ["| " + " | ".join(_COLUMNS) + " |", "| " + " | ".join("---" for _ in _COLUMNS) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jittersim",
        description="Measure a test device's connection and compare the runs.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("measure", help="ping a target and save the result")
    run.add_argument("--label", default="run", help="a name for this run, such as normal")
    run.add_argument("--target", default=DEFAULT_TARGET, help="a public host to ping")
    run.add_argument("--count", type=int, default=50, help="pings to send (1 to 1000)")
    run.add_argument(
        "--interval", type=float, default=0.2, help="seconds between pings (0.01 to 10)"
    )
    run.add_argument("--download", metavar="URL", help="also time the download of this file")
    run.add_argument("--output", type=Path, help="where to save the result as JSON")
    compare = commands.add_parser("compare", help="compare saved runs in a table")
    compare.add_argument("files", nargs="+", type=Path)
    compare.add_argument("--format", choices=("markdown", "csv"), default="markdown")
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    """Run `measure` or `compare`."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "compare":
            runs = [load_run(path) for path in arguments.files]
            print(comparison_table(runs, table_format=arguments.format), end="")
            return 0
        if not 1 <= arguments.count <= 1000:
            parser.error("--count must be between 1 and 1000")
        if not 0.01 <= arguments.interval <= 10:
            parser.error("--interval must be between 0.01 and 10 seconds")
        run = measure(
            arguments.target,
            count=arguments.count,
            interval=arguments.interval,
            label=arguments.label,
            runner=runner,
            download_url=arguments.download,
        )
    except MeasurementError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", arguments.label).strip("-") or "run"
    output = arguments.output or Path(f"jittersim-{safe_label}.json")
    output.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    summary = run["summary"]
    print(
        f"{arguments.label}: {summary['sent']} sent, {summary['loss_percent']:.1f}% loss, "
        f"RTT avg {_milliseconds(summary['rtt_avg_ms'])}, "
        f"p95 {_milliseconds(summary['rtt_p95_ms'])}, "
        f"jitter {_milliseconds(summary['jitter_ms'])}"
    )
    print(f"Saved {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
import urllib.parse
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
    """Build a ping command that reports every reply, however slow."""

    command = ["ping", "-n", "-c", str(count), "-i", f"{interval:g}"]
    # On Linux, -W only shortens the wait when nothing answers. macOS leaves a
    # reply slower than -W out of its output, so it gets no -W.
    if platform != "darwin":
        command += ["-W", "2"]
    return command + [target]


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


def measure_download(
    url: str,
    *,
    timeout: float = 30.0,
    max_bytes: int = 100 * 2**20,
    max_seconds: float = 20.0,
) -> Dict:
    """Download a file for at most max_seconds and report the throughput."""

    received = 0
    complete = False
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            # A slow profile would take minutes for a large file, so stop early.
            while received < max_bytes and time.monotonic() - started < max_seconds:
                # read1 returns what has arrived, so the time limit holds on slow links.
                chunk = response.read1(min(65_536, max_bytes - received))
                if not chunk:
                    complete = True
                    break
                received += len(chunk)
            # With a Content-Length, the whole file may have arrived at the cap.
            complete = complete or getattr(response, "length", None) == 0
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise MeasurementError(f"download failed: {error}") from error
    seconds = max(time.monotonic() - started, 1e-6)
    return {
        "url": url,
        "bytes": received,
        "seconds": round(seconds, 3),
        "mbit_per_s": round(received * 8 / seconds / 1_000_000, 2),
        "complete": complete,
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

    if platform == "win32":
        raise MeasurementError("measuring needs macOS or Linux; Windows ping works differently")
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


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    )


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_measurement(run: Any) -> bool:
    """Check every field the tables and charts use, as the dashboard does."""

    if (
        not isinstance(run, dict)
        or run.get("format") != MEASUREMENT_FORMAT
        or run.get("version") != MEASUREMENT_VERSION
        or not isinstance(run.get("label"), str)
        or not isinstance(run.get("target"), str)
    ):
        return False
    summary = run.get("summary")
    if (
        not isinstance(summary, dict)
        or not _is_count(summary.get("sent"))
        or not _is_number(summary.get("loss_percent"))
        or not _is_count(summary.get("reordered", 0))
        or any(
            summary.get(key) is not None and not _is_number(summary.get(key))
            for key in ("rtt_avg_ms", "rtt_p95_ms", "jitter_ms")
        )
    ):
        return False
    samples = run.get("samples")
    if samples is None:
        samples = []
    if not isinstance(samples, list) or any(
        not isinstance(sample, dict) or not _is_number(sample.get("rtt_ms")) for sample in samples
    ):
        return False
    download = run.get("download")
    return download is None or (
        isinstance(download, dict) and _is_number(download.get("mbit_per_s"))
    )


def load_run(path: Path) -> Dict[str, Any]:
    """Read one saved measurement."""

    try:
        run = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MeasurementError(f"could not read {path}: {error}") from error
    if not _is_measurement(run):
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
    # A pipe in a label would start a new column.
    lines.extend("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def _safe_label(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-") or "run"


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
    run.add_argument(
        "--download", metavar="URL", help="also download this file, for at most 20 seconds"
    )
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
        # Check what comes after the pings before spending time on them.
        if arguments.download and urllib.parse.urlsplit(arguments.download).scheme not in (
            "http",
            "https",
        ):
            parser.error("--download needs an http:// or https:// URL")
        output = arguments.output or Path(f"jittersim-{_safe_label(arguments.label)}.json")
        if not output.parent.is_dir():
            parser.error(f"the folder for {output} does not exist")
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
    try:
        output.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        print(f"error: could not save {output}: {error}", file=sys.stderr)
        return 1
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

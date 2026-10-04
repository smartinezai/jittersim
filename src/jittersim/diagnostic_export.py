"""Root-only, bounded summaries from two local journal boots."""

import json
import os
import re
import selectors
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from .diagnostic_records import validate_event

MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 1000
MAX_LINE = 16 * 1024
MAX_INPUT = 4 * 1024 * 1024
READ_SECONDS = 10.0
UNITS = frozenset((
    "jittersim-dashboard.service", "jittersim-helper.service", "jittersim-ap.service",
    "jittersim-access-boot.service", "jittersim-control-firewall.service", "jittersim-dns.service",
    "jittersim-browser-proxy.service", "jittersim-service-watch.service", "jittersim-update.service",
    "jittersim-update-fetch.service", "jittersim-adblocking.service", "jittersim-mdns.service",
))
ACTION_NAMES = frozenset((
    "/api/apply", "/api/reset", "/api/normal-access", "/api/access-point", "/api/access-point/reset-name",
    "/api/router/wifi", "/api/router/sync-ap", "/api/general-profile",
))
FIELDS = "__REALTIME_TIMESTAMP,__MONOTONIC_TIMESTAMP,_BOOT_ID,_SYSTEMD_UNIT,_TRANSPORT,PRIORITY,SYSLOG_IDENTIFIER,JITTERSIM_EVENT,JITTERSIM_DROPPED,MESSAGE"


class UnsupportedHostError(RuntimeError):
    """The command needs Linux journal support and a root identity check."""


def _number(value, maximum=2**63-1):
    if isinstance(value, str) and len(value) <= 20 and value.isascii() and value.isdigit():
        number = int(value)
        if number <= maximum:
            return number
    return None


def summarize(entry):
    if not isinstance(entry, dict):
        return None
    boot = entry.get("_BOOT_ID")
    if not isinstance(boot, str) or not re.fullmatch("[a-f0-9]{32}", boot):
        return None
    real = _number(entry.get("__REALTIME_TIMESTAMP"))
    elapsed = _number(entry.get("__MONOTONIC_TIMESTAMP"))
    if real is None or elapsed is None:
        return None
    unit = entry.get("_SYSTEMD_UNIT")
    if unit is not None and not isinstance(unit, str):
        return None
    event = None
    if unit in UNITS and entry.get("SYSLOG_IDENTIFIER") == "jittersim-diagnostics":
        raw = entry.get("JITTERSIM_EVENT")
        if isinstance(raw, str) and len(raw) <= 2048:
            try:
                event = validate_event(json.loads(raw))
            except (ValueError, TypeError):
                pass
        if event is None:
            return None
    elif unit in UNITS and entry.get("SYSLOG_IDENTIFIER") == "jittersim" and entry.get("PRIORITY") in ("0", "1", "2", "3", "4"):
        event = {"category": "service-failure" if entry["PRIORITY"] != "4" else "service-warning"}
    elif entry.get("_TRANSPORT") == "kernel":
        message = entry.get("MESSAGE")
        if not isinstance(message, str):
            return None
        if re.match(r"^(?:[A-Za-z0-9_.:-]{1,32}\s+)?[Ll]ink is [Dd]own(?:\s|$)", message):
            event = {"category": "link-down"}
        elif re.match(r"^(?:[A-Za-z0-9_.:-]{1,32}\s+)?[Ll]ink is [Uu]p(?:\s|$)", message):
            event = {"category": "link-up"}
        elif message.startswith(("Out of memory:", "oom-kill:", "Kernel panic -")):
            event = {"category": "memory-or-kernel-failure"}
        else:
            return None
        unit = "kernel"
    elif unit == "NetworkManager.service":
        message = entry.get("MESSAGE")
        if not isinstance(message, str) or not re.search(r"\bstate change: (?:[a-z-]{1,24}) -> (?:[a-z-]{1,24})\b", message):
            return None
        event = {"category": "network-state-change"}
    else:
        return None
    dropped = _number(entry.get("JITTERSIM_DROPPED"), maximum=2**31-1)
    if dropped is not None:
        event = {**event, "omitted_before": dropped}
    return {"boot": boot, "realtime_us": real, "elapsed_us": elapsed, "source": unit, "event": event}


def _journal(boot, deadline, accept, popen=subprocess.Popen):
    command = ["/usr/bin/journalctl", "--quiet", "--no-pager", "--output=json", "--output-fields=" + FIELDS,
               "--boot=" + str(boot), "--lines=" + str(MAX_ENTRIES)]
    command += ["_SYSTEMD_UNIT=" + unit for unit in sorted(UNITS | {"NetworkManager.service"})]
    command += ["+", "_TRANSPORT=kernel"]
    report = {"boot_offset": boot, "read": 0, "omitted": 0, "status": "unavailable"}
    process = None
    read_deadline = deadline - .1
    try:
        if time.monotonic() >= read_deadline:
            report["status"] = "deadline"
            return report
        process = popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, shell=False)
        os.set_blocking(process.stdout.fileno(), False)
        buffer = bytearray()
        received = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            report["status"] = "complete"
            while True:
                remaining = read_deadline - time.monotonic()
                if remaining <= 0:
                    report["status"] = "deadline"
                    break
                if not selector.select(remaining):
                    report["status"] = "deadline"
                    break
                block = os.read(process.stdout.fileno(), 4096)
                if not block:
                    if buffer:
                        report["omitted"] += 1
                    break
                received += len(block)
                if received > MAX_INPUT:
                    report["status"] = "input-limit"
                    break
                buffer.extend(block)
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    report["read"] += 1
                    if len(line) > MAX_LINE:
                        report["status"] = "line-limit"
                        return report
                    try:
                        item = summarize(json.loads(line))
                    except (ValueError, TypeError, RecursionError):
                        item = None
                    if item is None:
                        report["omitted"] += 1
                    elif not accept(item):
                        report["status"] = "output-limit"
                        return report
                    if report["read"] >= MAX_ENTRIES:
                        report["status"] = "entry-limit"
                        return report
                if len(buffer) > MAX_LINE:
                    report["status"] = "line-limit"
                    break
        remaining = read_deadline - time.monotonic()
        if report["status"] == "complete" and process.poll() is None and remaining <= 0:
            report["status"] = "deadline"
        if report["status"] == "complete" and process.poll() is None and remaining > 0:
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                report["status"] = "deadline"
        if process.poll() not in (None, 0) and report["status"] == "complete":
            report["status"] = "unavailable"
    except (OSError, subprocess.SubprocessError):
        report["status"] = "unavailable"
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            # Reap only within the export deadline. journalctl starts no child commands.
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                report["cleanup"] = "pending"
            process.stdout.close()
    return report


def _actions(database, deadline):
    records = []
    connection = None
    try:
        if database.is_symlink() or not database.is_file() or time.monotonic() >= deadline:
            return records, "unavailable"
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0.0)
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
        rows = connection.execute(
            "SELECT happened_at, substr(action,1,101), status_code FROM action_history ORDER BY id DESC LIMIT 100"
        )
        for happened, action, status in rows:
            if action in ACTION_NAMES and type(happened) is int and 0 <= happened <= 2**53-1 and type(status) is int and 100 <= status <= 599:
                records.append({"happened_at": happened, "action": action, "status": status})
        return records, "complete"
    except (OSError, sqlite3.Error, ValueError):
        return records, "unavailable"
    finally:
        if connection:
            connection.close()


def build_export(database=Path("/var/lib/jittersim/jittersim.db"), reader=_journal):
    root_identity = getattr(os, "geteuid", None)
    if not sys.platform.startswith("linux") or not callable(root_identity):
        raise UnsupportedHostError("Diagnostic export requires Linux. Run this command on the Pi.")
    if root_identity() != 0:
        raise PermissionError("Run sudo jittersim diagnostics export.")
    deadline = time.monotonic() + READ_SECONDS
    result = {"format": 1, "entries": [], "boots": [], "actions": [],
              "notice": "Missing outcomes do not prove success. Unknown message text is omitted. Time may be unsynchronized."}
    size = 0
    def accept(item):
        nonlocal size
        encoded = json.dumps(item, separators=(",", ":")).encode("utf-8")
        if size + len(encoded) + 1 > MAX_BYTES - 16384:
            return False
        size += len(encoded) + 1
        result["entries"].append(item)
        return True
    for boot in (0, -1):
        result["boots"].append(reader(boot, deadline, accept))
    actions, status = _actions(database, deadline)
    result["actions"], result["action_status"] = actions, status
    raw = json.dumps(result, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(raw) > MAX_BYTES:
        raise RuntimeError("Export size limit exceeded.")
    return raw

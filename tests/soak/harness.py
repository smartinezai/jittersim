"""Run the real dashboard for a long time under browser-like load and judge its resources.

    python -m tests.soak.harness --minutes 240 --out /tmp/soak-run
    python -m tests.soak.harness --minutes 30 --actions-every 0      # polling only

It starts tests/soak/target.py (the real ControlServer, helper service and scheduler over a
temporary SQLite file), then several simulated browsers poll the routes the dashboard page
polls at the page's own intervals (connected devices, general profile and state every 5 s,
health and the open page's jobs every 5 s, traffic every 2 s, impact every 5 s). A browser
moves to another page now and then, and each page load checks the token and reads every
route once, like the page does after Check token. One more thread acts like the owner:
it saves a profile and a device, applies, presses Panic, restores a saved version, imports
an export and downloads a backup, one action every --actions-every seconds.

The target writes a sample every --sample-seconds: resident memory, open descriptors and
sockets, threads, CPU time, block writes, the database and its journal, the state folder, the
helper connections and calls, and every write it made with the task that made it. At the end
the harness compares the start of the run (after --warmup-seconds) with its end and fails on
growth beyond the limits below, on any server error, on requests that did not get an answer,
and on latency that got much worse. The report goes to OUT/report.json and to stdout.
"""

import argparse
import http.client
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from array import array
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from tests.soak.target import TOKEN

REPOSITORY = Path(__file__).resolve().parents[2]

# What the page reads by itself, from dashboard.js: (seconds between reads, routes).
ALWAYS = (
    (5.0, ("/api/connected-devices", "/api/connected-device-labels")),  # pollLiveStatus devices
    (5.0, ("/api/general-profile",)),                                   # pollLiveStatus general
    (5.0, ("/api/state",)),                                             # pollLiveStatus state
    (5.0, ("/api/health", "/api/system-health")),                       # pollDashboard loadHealth
)
# pagePollJobs, read every 5 s while the page is shown. Traffic and impact have their own timers.
PAGES = {
    "devices": (("/api/devices", "/api/conditions", "/api/device-workspace/alice",
                 "/api/dashboard-access/status"), ("/api/impact",)),
    "traffic": (("/api/observations",), ("/api/traffic",)),
    "schedules": (("/api/timers", "/api/scenarios", "/api/progressions", "/api/schedules",
                   "/api/data-limits", "/api/service-lists"), ()),
    "access": (("/api/observations", "/api/dashboard-access/status"), ()),
    "activity": (("/api/activity", "/api/activity/latest"), ()),
    "measurements": (("/api/test-sessions",), ()),
    "router": (("/api/uplink", "/api/gateway-persistence"), ()),
    "updates": (("/api/updates",), ()),
}
PAGE_TIMERS = {"/api/traffic": 2.0, "/api/impact": 5.0}
# Read once on each page load after the token check (the dashboard's full refresh).
PAGE_LOAD = (
    "/", "/api/health", "/api/state", "/api/profiles", "/api/devices", "/api/groups",
    "/api/schedules", "/api/scenarios", "/api/timers", "/api/progressions", "/api/data-limits",
    "/api/group-data-limits", "/api/service-quotas", "/api/service-assignments",
    "/api/service-lists", "/api/time-quotas", "/api/policies", "/api/everyone-else",
    "/api/ad-blocking", "/api/unaffected-devices", "/api/general-profile",
    "/api/connected-device-labels", "/api/traffic-settings", "/api/gateway-persistence",
    "/api/plan", "/api/history", "/api/activity", "/api/router",
    "/api/trust", "/api/brand", "/api/access", "/api/dashboard-access", "/api/updates",
    "/api/action-history", "/api/pattern-history", "/api/observations", "/api/uplink",
)

# A read that meets the helper while it applies a change answers 502 ("network changed during
# client scan") or 503 and the page reads again on its next poll. Counted, judged by share.
BUSY_STATUSES = (502, 503)

# Growth from the start to the end of the run (after the warm-up) that fails the run.
DEFAULT_LIMITS = {
    "rss_mb": 24.0,          # resident memory
    "heap_share": 0.25,      # Python heap blocks, as a share of the start
    "fds": 8,                # open descriptors
    "threads": 4,            # Python threads
    "native_threads": 4,     # threads of the process
    "db_mb": 4.0,            # the database file
    "state_mb": 8.0,         # the whole state folder (database, journal, JSON state files)
    "helper_open": 4,        # helper connections accepted but not closed, at the end
    "latency_factor": 3.0,   # p95 at the end over p95 at the start
    "latency_slack_ms": 100.0,
    "unanswered_share": 0.01,  # requests that got no HTTP answer at all
    "busy_share": 0.02,        # reads answered 502 or 503 because the helper was busy
}


@dataclass
class Results:
    started: float = field(default_factory=time.monotonic)
    statuses: Counter = field(default_factory=Counter)
    by_route: dict = field(default_factory=lambda: defaultdict(Counter))
    minutes: dict = field(default_factory=lambda: defaultdict(lambda: array("f")))
    route_ms: dict = field(default_factory=lambda: defaultdict(lambda: array("f")))
    unanswered: Counter = field(default_factory=Counter)
    busy: Counter = field(default_factory=Counter)
    server_errors: list = field(default_factory=list)
    actions: dict = field(default_factory=lambda: defaultdict(Counter))
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, method, route, status, milliseconds, body=b""):
        minute = int((time.monotonic() - self.started) // 60)
        with self.lock:
            self.statuses[status] += 1
            self.by_route[method + " " + route][status] += 1
            self.minutes[minute].append(milliseconds)
            self.route_ms[method + " " + route].append(milliseconds)
            if status in BUSY_STATUSES and method == "GET":
                self.busy[method + " " + route] += 1
            elif status >= 500 and len(self.server_errors) < 50:
                self.server_errors.append((method, route, status, body[:300].decode("utf-8", "replace")))

    def record_unanswered(self, method, route, error):
        with self.lock:
            self.unanswered[f"{method} {route}: {type(error).__name__}"] += 1


class Client:
    def __init__(self, port, results, token=TOKEN, timeout=60.0, tls=False):
        self.port, self.results, self.token, self.timeout = port, results, token, timeout
        self.tls = None
        if tls:
            import ssl
            # The target's certificate is a throwaway one: the client checks nothing.
            self.tls = ssl.create_default_context()
            self.tls.check_hostname = False
            self.tls.verify_mode = ssl.CERT_NONE

    def _connection(self):
        if self.tls is not None:
            return http.client.HTTPSConnection("127.0.0.1", self.port, timeout=self.timeout,
                                               context=self.tls)
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)

    def call(self, method, path, body=None, *, token=None, record=True):
        headers = {"Authorization": "Bearer " + (token or self.token)}
        if body is not None:
            body = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        route = path.split("?")[0]
        begin = time.monotonic()
        connection = self._connection()
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
        except (OSError, http.client.HTTPException) as error:
            if record:
                self.results.record_unanswered(method, route, error)
            return None, b""
        finally:
            connection.close()
        if record:
            self.results.record(method, route, response.status, (time.monotonic() - begin) * 1000, data)
        return response.status, data

    def json(self, method, path, body=None):
        status, data = self.call(method, path, body)
        try:
            return status, json.loads(data or b"null")
        except ValueError:
            return status, None


def browser(client, number, stop, speed, page_seconds, rng):
    """One open dashboard tab. Polls like dashboard.js, moves pages, reloads now and then."""

    pages = list(PAGES)
    page = pages[number % len(pages)]

    def load_page():
        client.call("GET", "/api/state", token="not-the-token")  # a stale token in another tab
        for route in PAGE_LOAD:
            if stop.is_set():
                return
            client.call("GET", route)

    def timers_for(current):
        timers = [(interval, routes) for interval, routes in ALWAYS]
        jobs, own = PAGES[current]
        if jobs:
            timers.append((5.0, jobs))
        timers.extend((PAGE_TIMERS[route], (route,)) for route in own)
        return timers

    load_page()
    timers = timers_for(page)
    now = time.monotonic()
    due = [now + rng.uniform(0, interval / speed) for interval, _ in timers]
    next_page = now + page_seconds / speed
    while not stop.is_set():
        index = min(range(len(due)), key=due.__getitem__)
        if stop.wait(max(0.0, due[index] - time.monotonic())):
            return
        for route in timers[index][1]:
            client.call("GET", route)
        due[index] = time.monotonic() + timers[index][0] / speed
        if time.monotonic() >= next_page:
            page = pages[(pages.index(page) + 1) % len(pages)]
            if rng.random() < 0.25:
                load_page()  # a reload
            timers = timers_for(page)
            now = time.monotonic()
            due = [now + rng.uniform(0, interval / speed) for interval, _ in timers]
            next_page = now + page_seconds / speed


def _action_save_profile(client, step):
    status, data = client.json("GET", "/api/profiles")
    profile = dict(next(item for item in data["profiles"] if item["id"] == "slow"))
    profile["name"] = f"Slow connection {step % 7}"
    return client.call("POST", "/api/profiles", profile)[0]


def _action_save_device(client, step):
    status, data = client.json("GET", "/api/devices")
    saved = next(item for item in data["devices"] if item["id"] == "alice")
    device = {key: saved[key] for key in ("id", "ipv4_address", "mac_address")}
    device["name"] = f"Test phone {step % 5}"
    device["profile_id"] = "slow" if step % 2 else "normal"
    return client.call("POST", "/api/devices", device)[0]


def _action_apply(client, step):
    return client.call("POST", "/api/apply", {})[0]


def _action_panic(client, step):
    return client.call("POST", "/api/reset", {})[0]


def _action_restore(client, step):
    status, history = client.json("GET", "/api/history")
    versions = (history or {}).get("versions") or []
    if len(versions) < 2:
        return "skipped"
    chosen = versions[1]["id"]
    status, preview = client.json("POST", "/api/history/preview", {"id": chosen})
    if status != 200:
        return status
    return client.call("POST", "/api/history/restore",
                       {"id": chosen, "current_version": preview["current_version"]})[0]


def _action_import(client, step):
    status, document = client.json("GET", "/api/export")
    return client.call("POST", "/api/import", document)[0]


def _action_backup(client, step):
    return client.call("GET", "/api/backup")[0]


def _action_session(client, step):
    """Start a test session, or finish the open one (only one can be open)."""

    status, data = client.json("GET", "/api/test-sessions")
    open_ones = [item for item in (data or {}).get("sessions", ()) if item["ended_at"] is None]
    if not open_ones:
        return client.call("POST", "/api/test-sessions/start",
                           {"name": f"Soak session {step}", "target": "device:alice"})[0]
    status, data = client.json("GET", "/api/test-sessions/" + open_ones[0]["id"])
    session = data["session"]
    return client.call("POST", "/api/test-sessions/save", {
        "id": session["id"], "revision": session["revision"], "finish": True,
        "notes": f"finished {step}"})[0]


ACTIONS = (
    ("save profile", _action_save_profile), ("apply", _action_apply),
    ("save device", _action_save_device), ("apply", _action_apply),
    ("backup", _action_backup), ("panic", _action_panic), ("restore", _action_restore),
    ("apply", _action_apply), ("import", _action_import), ("test session", _action_session),
)


def owner(client, stop, every, results):
    step = 0
    while not stop.wait(every):
        name, action = ACTIONS[step % len(ACTIONS)]
        try:
            outcome = action(client, step)
        except Exception as error:  # a broken action is a finding, not a harness crash
            outcome = type(error).__name__
        with results.lock:
            results.actions[name][str(outcome)] += 1
        step += 1


def percentile(values, share):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(share * len(ordered)))], 1)


def _median(samples, key):
    return statistics.median(sample[key] for sample in samples)


def judge(samples, final, results, *, warmup, limits, seconds):
    """Return (problems, figures) for one finished run."""

    problems, figures = [], {}
    settled = [sample for sample in samples if sample["t"] >= warmup] or samples
    if len(settled) < 2:
        return ["too few samples to judge (run longer or sample more often)"], figures
    window = max(1, len(settled) // 10)
    first, last = settled[:window], settled[-window:]
    span_hours = max(1e-9, (last[-1]["t"] - first[0]["t"]) / 3600)
    keys = ("rss_kb", "heap_blocks", "gc_objects", "fds", "sockets", "threads", "native_threads", "db_bytes", "journal_bytes",
            "wal_bytes", "state_bytes", "helper_calls", "network_commands", "writes", "blocks_out")
    for key in keys:
        start, end = _median(first, key), _median(last, key)
        figures[key] = {"first": samples[0][key], "start": start, "end": end,
                        "max": max(sample[key] for sample in samples),
                        "per_hour": round((end - start) / span_hours, 1)}
    cpu = [sample["cpu_user"] + sample["cpu_system"] for sample in settled]
    figures["cpu_share"] = round((cpu[-1] - cpu[0]) / max(1e-9, settled[-1]["t"] - settled[0]["t"]), 4)

    def grow(key, divisor=1):
        return (figures[key]["end"] - figures[key]["start"]) / divisor

    checks = (("rss_kb", 1024, "rss_mb", "resident memory grew by %.1f MB"),
              ("fds", 1, "fds", "open descriptors grew by %d"),
              ("threads", 1, "threads", "Python threads grew by %d"),
              ("native_threads", 1, "native_threads", "process threads grew by %d"),
              ("db_bytes", 1 << 20, "db_mb", "the database grew by %.1f MB"),
              ("state_bytes", 1 << 20, "state_mb", "the state folder grew by %.1f MB"))
    if grow("heap_blocks") > limits["heap_share"] * max(1, figures["heap_blocks"]["start"]):
        problems.append("the Python heap grew by %d blocks (%.0f%%)" % (
            grow("heap_blocks"), 100 * grow("heap_blocks") / max(1, figures["heap_blocks"]["start"])))
    for key, divisor, limit, message in checks:
        if grow(key, divisor) > limits[limit]:
            problems.append(message % grow(key, divisor))
    end = final.get("sample", samples[-1])
    if end["helper_accepted"] - end["helper_closed"] > limits["helper_open"]:
        problems.append("helper connections left open: %d"
                        % (end["helper_accepted"] - end["helper_closed"]))
    total = sum(results.statuses.values()) + sum(results.unanswered.values())
    figures["requests"] = total
    figures["requests_per_second"] = round(total / max(1.0, seconds), 1)
    figures["statuses"] = {str(key): value for key, value in sorted(results.statuses.items())}
    if results.server_errors:
        problems.append("server errors: " + "; ".join(
            f"{m} {r} -> {s}" for m, r, s, _ in results.server_errors[:5]))
    figures["busy_reads"] = dict(results.busy)
    if total and sum(results.busy.values()) / total > limits["busy_share"]:
        problems.append("too many busy answers: %s" % dict(results.busy))
    if total and sum(results.unanswered.values()) / total > limits["unanswered_share"]:
        problems.append("requests without an answer: %s" % dict(results.unanswered))
    minutes = sorted(results.minutes)
    settled_minutes = [m for m in minutes if m * 60 >= warmup] or minutes
    edge = max(1, len(settled_minutes) // 10)
    early = [v for m in settled_minutes[:edge] for v in results.minutes[m]]
    late = [v for m in settled_minutes[-edge:] for v in results.minutes[m]]
    figures["latency_ms"] = {
        "p50": percentile([v for m in minutes for v in results.minutes[m]], 0.5),
        "p95": percentile([v for m in minutes for v in results.minutes[m]], 0.95),
        "p99": percentile([v for m in minutes for v in results.minutes[m]], 0.99),
        "p95_start": percentile(early, 0.95), "p95_end": percentile(late, 0.95)}
    if early and late and len(settled_minutes) >= 3:
        p95_early, p95_late = figures["latency_ms"]["p95_start"], figures["latency_ms"]["p95_end"]
        if p95_late > p95_early * limits["latency_factor"] + limits["latency_slack_ms"]:
            problems.append(f"latency p95 went from {p95_early} ms to {p95_late} ms")
    figures["slowest_routes_p95_ms"] = dict(sorted(
        ((route, percentile(values, 0.95)) for route, values in results.route_ms.items()),
        key=lambda item: -item[1])[:8])
    if final.get("outside_attempts"):
        problems.append("the target tried to reach outside: %s" % final["outside_attempts"][:3])
    return problems, figures


def writes_per_hour(final):
    """Group the target's writes by task and give each a rate per hour, busiest first."""

    hours = max(1e-9, final.get("seconds", 0) / 3600)
    table = defaultdict(Counter)
    for row in final.get("writes", ()):
        table[(row["task"], row["what"])][row["kind"]] += row["count"]
    rows = []
    for (task, what), kinds in table.items():
        rows.append({"task": task, "what": what, **{kind: round(count / hours, 1)
                                                     for kind, count in kinds.items()}})
    disk = ("db commits changed", "file replaces", "fsync calls", "file opens for writing")
    rows.sort(key=lambda row: -sum(row.get(kind, 0) for kind in disk))
    return rows


def run(*, minutes, browsers, out, speed=1.0, actions_every=30.0, sample_seconds=10.0,
        warmup_seconds=120.0, page_seconds=120.0, limits=None, tracemalloc=False, seed=1,
        apply_first=True, tls=True):
    """Run one soak and return the report (a dict). report["problems"] is empty on a pass."""

    limits = {**DEFAULT_LIMITS, **(limits or {})}
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-W", "ignore", "-m", "tests.soak.target", str(out / "target"),
               "--sample-seconds", str(sample_seconds)] + (["--tracemalloc"] if tracemalloc else []) \
        + (["--tls"] if tls else [])
    errors = open(out / "target-stderr.txt", "wb")
    process = subprocess.Popen(command, cwd=str(REPOSITORY), stdout=subprocess.PIPE, stderr=errors,
                               env={**os.environ, "PYTHONPATH": os.pathsep.join(
                                   [str(REPOSITORY), str(REPOSITORY / "src")])})
    try:
        line = process.stdout.readline()
        if not line:
            raise RuntimeError("the soak target did not start; see " + str(out / "target-stderr.txt"))
        ready = json.loads(line)
        results = Results()
        if apply_first:
            # Like a Pi in use: a device has a slow profile and the network is applied, so the
            # scheduler, the traffic counters and ad blocking run as they do after Apply.
            setup = Client(ready["port"], results, tls=tls)
            _action_save_device(setup, 1)
            # Quotas make the scheduler sample counters every 5 s, as on a Pi that uses them.
            setup.call("POST", "/api/data-limits",
                       {"device_id": "alice", "profile_id": "slow", "limit_bytes": 10 ** 12})
            setup.call("POST", "/api/groups", {"id": "family", "name": "Family", "device_ids": ["alice"]})
            setup.call("POST", "/api/group-data-limits",
                       {"group_id": "family", "profile_id": "slow", "limit_bytes": 10 ** 12})
            _action_apply(setup, 0)
        stop = threading.Event()
        threads = []
        for number in range(browsers):
            client = Client(ready["port"], results, tls=tls)
            threads.append(threading.Thread(
                target=browser, name=f"browser-{number}", daemon=True,
                args=(client, number, stop, speed, page_seconds, random.Random(seed + number))))
        if actions_every > 0:
            threads.append(threading.Thread(target=owner, name="owner", daemon=True,
                                            args=(Client(ready["port"], results, tls=tls), stop,
                                                  actions_every, results)))
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + minutes * 60
        while time.monotonic() < deadline and process.poll() is None:
            time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
        alive = process.poll() is None
        stop.set()
        for thread in threads:
            thread.join(timeout=70)
        seconds = time.monotonic() - results.started
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        errors.close()
    samples = [json.loads(text) for text in (out / "target" / "samples.jsonl").read_text().splitlines()
               if text.strip()]
    final_path = out / "target" / "final.json"
    final = json.loads(final_path.read_text()) if final_path.exists() else {}
    problems, figures = judge(samples, final, results, warmup=warmup_seconds, limits=limits,
                              seconds=seconds)
    if not alive:
        problems.insert(0, "the target process stopped during the run (exit %s)" % process.returncode)
    if not final:
        problems.append("the target wrote no final report")
    report = {
        "problems": problems, "figures": figures, "minutes": minutes, "browsers": browsers,
        "speed": speed, "actions_every": actions_every, "limits": limits, "tls": tls,
        "actions": {name: dict(outcomes) for name, outcomes in results.actions.items()},
        "unanswered": dict(results.unanswered), "server_errors": results.server_errors,
        "routes": {route: dict(statuses) for route, statuses in sorted(results.by_route.items())},
        "writes_per_hour": writes_per_hour(final),
        "helper_actions": final.get("helper_actions", {}),
        "helper_busy": final.get("helper_busy", {}),
        "network_commands": final.get("network_commands", {}),
        "tracemalloc_growth": final.get("tracemalloc_growth", []),
        "table_rows": {name: [final.get("tables_start", {}).get(name), count]
                       for name, count in final.get("tables_end", {}).items()},
        "journal_mode": final.get("journal_mode"), "synchronous": final.get("synchronous"),
        "target_seconds": final.get("seconds"),
    }
    (out / "report.json").write_text(json.dumps(report, indent=1))
    return report


def summary(report):
    figures = report["figures"]
    lines = ["Soak: %s min, %s browsers, speed %s, owner action every %s s"
             % (report["minutes"], report["browsers"], report["speed"], report["actions_every"])]
    for key in ("rss_kb", "heap_blocks", "gc_objects", "fds", "sockets", "threads", "native_threads", "db_bytes", "journal_bytes",
                "state_bytes", "helper_calls", "network_commands", "writes", "blocks_out"):
        if key in figures:
            item = figures[key]
            lines.append(f"  {key:16} first {item['first']:>10}  start {item['start']:>10}  "
                         f"end {item['end']:>10}  max {item['max']:>10}  per hour {item['per_hour']}")
    for key in ("cpu_share", "requests", "requests_per_second", "statuses", "busy_reads", "latency_ms",
                "slowest_routes_p95_ms"):
        if key in figures:
            lines.append(f"  {key}: {figures[key]}")
    lines.append("  owner actions: %s" % report["actions"])
    lines.append("  helper calls: %s" % report["helper_actions"])
    lines.append("  helper busy refusals the fixture waited out: %s" % report["helper_busy"])
    grown = {name: rows for name, rows in report.get("table_rows", {}).items() if rows[0] != rows[1]}
    lines.append("  table rows that changed (start, end): %s" % grown)
    lines.append("  busiest writers per hour:")
    for row in report["writes_per_hour"][:12]:
        lines.append("    %s" % row)
    lines.append("RESULT: " + ("PASS" if not report["problems"] else "FAIL: " + "; ".join(report["problems"])))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--browsers", type=int, default=4)
    parser.add_argument("--speed", type=float, default=1.0,
                        help="poll this many times faster than a real page (1 = real)")
    parser.add_argument("--actions-every", type=float, default=30.0,
                        help="seconds between owner actions, 0 for polling only")
    parser.add_argument("--sample-seconds", type=float, default=10.0)
    parser.add_argument("--warmup-seconds", type=float, default=120.0)
    parser.add_argument("--page-seconds", type=float, default=120.0)
    parser.add_argument("--tracemalloc", action="store_true",
                        help="record where memory grew (slower; for leak hunting)")
    parser.add_argument("--no-apply-first", dest="apply_first", action="store_false",
                        help="start from a network that was never applied")
    parser.add_argument("--plain-http", dest="tls", action="store_false",
                        help="serve plain HTTP (the Pi serves HTTPS, the default here too)")
    parser.add_argument("--out", type=Path)
    for name, value in DEFAULT_LIMITS.items():
        parser.add_argument("--limit-" + name.replace("_", "-"), type=type(value), default=value,
                            dest="limit_" + name)
    arguments = parser.parse_args(argv)
    out = arguments.out or Path(tempfile.mkdtemp(prefix="jittersim-soak-"))
    report = run(minutes=arguments.minutes, browsers=arguments.browsers, out=out,
                 speed=arguments.speed, actions_every=arguments.actions_every,
                 sample_seconds=arguments.sample_seconds, warmup_seconds=arguments.warmup_seconds,
                 page_seconds=arguments.page_seconds, tracemalloc=arguments.tracemalloc,
                 apply_first=arguments.apply_first, tls=arguments.tls,
                 limits={name: getattr(arguments, "limit_" + name) for name in DEFAULT_LIMITS})
    print(summary(report))
    print("Report:", out / "report.json")
    return 0 if not report["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())

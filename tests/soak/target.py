"""The process a soak run measures.

It is the browser fixture's dashboard (the real ControlServer over a real SQLite file in a
temporary folder), the real helper service behind a Unix socket with a fake network executor,
and the real RuntimeScheduler, like an installed Pi runs them. On the Pi the helper is a
separate root process. Here it is a thread of the same process, so the numbers below are the
sum of both. Nothing reaches outside this machine.

Run by tests/soak/harness.py:

    python -m tests.soak.target OUTPUT_FOLDER [--sample-seconds 5] [--tracemalloc]

It prints one JSON line (port, pid, database path) when it serves, writes one JSON line per
sample to OUTPUT_FOLDER/samples.jsonl, and on SIGTERM writes OUTPUT_FOLDER/final.json with the
write counters, the helper calls and the fake network commands.
"""

import argparse
import json
import os
import signal
import sys
import threading
import time
from collections import Counter
from pathlib import Path

from tests.soak import instrument

TOKEN = "soak-test-token"


class _BoundedCommands(list):
    """The fake executor's command log, kept short so it does not look like a leak."""

    total = 0

    def append(self, item):
        type(self).total += 1
        super().append(item)
        if len(self) > 400:
            del self[:200]


def _rss_kb():
    try:
        with open("/proc/self/statm", "rb") as handle:  # Linux
            return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") // 1024
    except OSError:
        pass
    import subprocess
    output = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())],
                            capture_output=True, text=True, check=False).stdout
    return int(output.strip() or 0)


def _native_threads():
    try:
        with open("/proc/self/status", "rb") as handle:
            for line in handle:
                if line.startswith(b"Threads:"):
                    return int(line.split()[1])
    except OSError:
        pass
    import subprocess
    output = subprocess.run(["ps", "-M", "-p", str(os.getpid())],
                            capture_output=True, text=True, check=False).stdout
    return max(0, len(output.strip().splitlines()) - 1)


def _descriptors():
    """Return (open descriptors, of which sockets)."""

    import stat
    folder = "/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"
    names = os.listdir(folder)
    sockets = 0
    for name in names:
        try:
            if stat.S_ISSOCK(os.fstat(int(name)).st_mode):
                sockets += 1
        except (OSError, ValueError):
            pass
    return len(names), sockets


def _size(path):
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


def _tree_size(root):
    total = 0
    for folder, _, files in os.walk(root):
        for name in files:
            total += _size(os.path.join(folder, name))
    return total


def _table_rows(database):
    """Row count of every table, read over a separate read-only connection."""

    import sqlite3
    connection = sqlite3.connect("file:%s?mode=ro" % database, uri=True)
    try:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        return {name: connection.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]
                for name in sorted(names)}
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--sample-seconds", type=float, default=5.0)
    parser.add_argument("--tracemalloc", action="store_true")
    parser.add_argument("--tls", action="store_true", help="serve HTTPS like the Pi does")
    arguments = parser.parse_args(argv)
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Opened before the wrappers so the samples themselves are not counted as writes.
    samples_fd = os.open(str(output / "samples.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    instrument.install()
    instrument.ignore_paths_under(output)
    if arguments.tracemalloc:
        import tracemalloc
        tracemalloc.start(8)

    import gc
    import logging
    import resource
    import tempfile

    os.environ["JITTERSIM_BROWSER_TEST_SEED"] = "1"
    os.environ["JITTERSIM_BROWSER_TEST_OBSERVATIONS"] = "1"
    from jittersim.config.storage import ConfigRepository
    from jittersim.engine.runtime import RuntimeScheduler
    from jittersim.networking.adblocking import AdRouting, AdStore
    from jittersim.networking.applier import NetworkApplier
    from jittersim.networking.dashboard_access import AccessStore
    from jittersim.networking.helper import UnixHelperServer
    from jittersim.networking.topology import Topology
    from tests.adblocking_support import provision_guard
    from tests.browser import fixture
    from tests.qa.dashboard_support import QAHelperService, _no_outside_network

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    helper_actions = Counter()
    helper_open = [0, 0]  # accepted, closed

    class CountingService(QAHelperService):
        def _handle_payload(self, payload):
            if isinstance(payload, dict):
                helper_actions[str(payload.get("action"))] += 1
            return super()._handle_payload(payload)

    class CountingHelperServer(UnixHelperServer):
        def process_request(self, request, client_address):
            helper_open[0] += 1
            return super().process_request(request, client_address)

        def shutdown_request(self, request):
            helper_open[1] += 1
            return super().shutdown_request(request)

    busy = Counter()

    class BusyCountingClient(fixture.PatientHelperClient):
        """The fixture waits when the helper is busy. Count each wait: the installed dashboard
        does not wait, so each one is a read that a real page would have seen fail."""

        def _send(self, payload, *, timeout_seconds=None):
            from jittersim.networking.helper import HelperClientError
            try:
                return super()._send(payload, timeout_seconds=timeout_seconds)
            except HelperClientError as error:
                if error.reason == "helper-busy":
                    busy[str(payload.get("action"))] += 1
                raise

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    attempts = []
    with _no_outside_network(attempts), tempfile.TemporaryDirectory(prefix="jittersim-soak-") as directory:
        root = Path(directory)
        executor = fixture.DashboardWifiExecutor()
        executor.commands = _BoundedCommands()
        commands_by_tool = Counter()
        real_run = executor.run

        def counted_run(command):
            commands_by_tool[command.executable] += 1
            return real_run(command)
        executor.run = counted_run
        topology = Topology("eth0", "wlan0")
        ad_blocking = AdStore(
            AdRouting("wlan0", executor), path=root / "ad-policy.json", lock_path=root / "ad-lock",
            active_path=root / "ad-active.json", status_path=root / "ad-status.json",
            intent_path=root / "ad-intent.json", panic_guard=provision_guard(root))
        access_manager = fixture.FixtureAccessManager(
            executor=executor, topology=topology, directory=root / "access",
            runtime_path=root / "access-policy.json", hostapd_path=root / "hostapd",
            ad_blocking_store=ad_blocking)
        service = CountingService(
            NetworkApplier(executor=executor, topology=topology),
            router_factory=lambda *_: fixture.DashboardRouter(),
            access_store=AccessStore(root / "access.json"),
            ad_blocking_store=ad_blocking, access_manager=access_manager)
        database = root / "config.db"
        with CountingHelperServer(root / "wifi.sock", service) as helper:
            threading.Thread(target=helper.serve_forever, name="soak-helper", daemon=True).start()
            with ConfigRepository(database) as repository:
                fixture.seed_observations(repository)
                fixture.seed_demo_data(repository)
                helper_client = BusyCountingClient(socket_path=root / "wifi.sock")
                scheduler = RuntimeScheduler(database_path=database, helper_client=helper_client)
                tls = {}
                if arguments.tls:
                    from tests.remote_support import make_certificate
                    certificate, key = make_certificate(root)
                    tls = {"tls_certfile": certificate, "tls_keyfile": key}
                server = fixture.FixtureServer(("127.0.0.1", 0), repository, admin_token=TOKEN,
                                               helper_client=helper_client, scheduler=scheduler,
                                               **tls)
                server.recover_saved_network_state()
                threading.Thread(target=server.serve_forever, name="soak-dashboard", daemon=True).start()
                started = time.time()
                tables_start = _table_rows(database)
                print(json.dumps({"port": server.server_address[1], "pid": os.getpid(),
                                  "database": str(database), "root": str(root)}), flush=True)
                tracemalloc_first = None

                def sample():
                    usage = resource.getrusage(resource.RUSAGE_SELF)
                    fds, sockets = _descriptors()
                    return {
                        "t": round(time.time() - started, 2), "rss_kb": _rss_kb(),
                        # Python's own heap. macOS compresses and pages out idle memory, so
                        # its resident size can fall under load: these two do not.
                        "heap_blocks": sys.getallocatedblocks(), "gc_objects": len(gc.get_objects()),
                        "max_rss": usage.ru_maxrss, "fds": fds, "sockets": sockets,
                        "threads": threading.active_count(), "native_threads": _native_threads(),
                        "cpu_user": round(usage.ru_utime, 3), "cpu_system": round(usage.ru_stime, 3),
                        "blocks_out": usage.ru_oublock,
                        "db_bytes": _size(database), "wal_bytes": _size(str(database) + "-wal"),
                        "journal_bytes": _size(str(database) + "-journal"),
                        "state_bytes": _tree_size(root),
                        "helper_accepted": helper_open[0], "helper_closed": helper_open[1],
                        "helper_calls": sum(helper_actions.values()),
                        "helper_busy": sum(busy.values()),
                        "network_commands": sum(commands_by_tool.values()),
                        "writes": sum(row["count"] for row in instrument.snapshot()
                                      if row["kind"] != "sql write statements"),
                    }

                try:
                    while not stop.wait(arguments.sample_seconds):
                        os.write(samples_fd, (json.dumps(sample()) + "\n").encode())
                        if arguments.tracemalloc and tracemalloc_first is None \
                                and time.time() - started > 60:
                            import tracemalloc
                            tracemalloc_first = tracemalloc.take_snapshot()
                finally:
                    final = {"sample": sample(), "writes": instrument.snapshot(),
                             "helper_actions": dict(helper_actions), "helper_busy": dict(busy),
                             "network_commands": dict(commands_by_tool),
                             "outside_attempts": [str(item) for item in attempts],
                             "tables_start": tables_start, "tables_end": _table_rows(database),
                             "journal_mode": repository._connection.execute(
                                 "PRAGMA journal_mode").fetchone()[0],
                             "synchronous": repository._connection.execute(
                                 "PRAGMA synchronous").fetchone()[0],
                             "seconds": round(time.time() - started, 1)}
                    if tracemalloc_first is not None:
                        import tracemalloc
                        top = tracemalloc.take_snapshot().compare_to(tracemalloc_first, "lineno")
                        final["tracemalloc_growth"] = [str(item) for item in top[:30]]
                    server.shutdown()
                    scheduler.begin_shutdown()
                    server.server_close()
                    scheduler.stop()
                    (output / "final.json").write_text(json.dumps(final, indent=1))
            helper.shutdown()
    os.close(samples_fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Measure what each JitterSim process costs before it does any work.

    python -m tests.soak.budget [--idle-seconds 60]

For each entry point it starts a fresh interpreter and reports the import time (from
`python -X importtime`), the resident memory after the import, and the number of modules
loaded. Then it runs the browser proxy and the dashboard (with its helper and scheduler, as
tests/soak/target.py runs them) without any client for --idle-seconds and reports the CPU time
they used, which is the cost of their timers alone. Run it on the Pi itself for real numbers;
on a Mac multiply the CPU times by about 5 to 8.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
ENTRY_POINTS = (
    ("dashboard", "jittersim.api.server"),
    ("helper", "jittersim.networking.helper"),
    ("browser proxy", "jittersim.networking.browser_proxy"),
    ("service watch", "jittersim.networking.dns_watch"),
    ("ad blocking", "jittersim.networking.adblocking"),
    ("mdns", "jittersim.mdnsname"),
    ("command line", "jittersim.command"),
)
_REPORT = (
    "import resource, sys, json; import {module}; "
    "rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss; "
    "print(json.dumps({{'rss_kb': rss // 1024 if sys.platform == 'darwin' else rss, "
    "'modules': len(sys.modules), "
    "'jittersim_modules': sum(1 for name in sys.modules if name.startswith('jittersim'))}}))"
)
_PROXY = """
import resource, sys, threading, time
from pathlib import Path
from jittersim.networking.browser_proxy import BrowserProxyServer
server = BrowserProxyServer(("127.0.0.1", 0), active_plan_path=Path(sys.argv[1]),
                            client_network="127.0.0.0/8", mark_socket=lambda *a: None)
threading.Thread(target=server.serve_forever, daemon=True).start()
start = resource.getrusage(resource.RUSAGE_SELF)
time.sleep(float(sys.argv[2]))
end = resource.getrusage(resource.RUSAGE_SELF)
print(end.ru_utime + end.ru_stime - start.ru_utime - start.ru_stime)
"""


def _environment():
    return {**os.environ, "PYTHONPATH": os.pathsep.join([str(REPOSITORY), str(REPOSITORY / "src")])}


def imports():
    rows = []
    for name, module in ENTRY_POINTS:
        done = subprocess.run([sys.executable, "-W", "ignore", "-X", "importtime", "-c",
                               _REPORT.format(module=module)],
                              capture_output=True, text=True, env=_environment(), check=True)
        total = 0
        for line in done.stderr.splitlines():
            parts = line.split("|")
            if len(parts) == 3 and parts[0].startswith("import time:") and parts[1].strip().isdigit():
                total += int(parts[0].split(":")[1])
        rows.append({"process": name, "module": module, "import_ms": round(total / 1000),
                     **json.loads(done.stdout.strip().splitlines()[-1])})
    return rows


def idle_proxy(seconds):
    with tempfile.TemporaryDirectory() as directory:
        done = subprocess.run([sys.executable, "-W", "ignore", "-c", _PROXY,
                               str(Path(directory) / "plan.json"), str(seconds)],
                              capture_output=True, text=True, env=_environment(), check=True)
    return float(done.stdout.strip())


def idle_dashboard(seconds, *, applied):
    with tempfile.TemporaryDirectory() as directory:
        process = subprocess.Popen(
            [sys.executable, "-W", "ignore", "-m", "tests.soak.target", directory,
             "--sample-seconds", str(seconds)],
            cwd=str(REPOSITORY), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_environment())
        try:
            ready = json.loads(process.stdout.readline())
            if applied:
                import http.client
                from tests.soak.target import TOKEN
                connection = http.client.HTTPConnection("127.0.0.1", ready["port"], timeout=30)
                connection.request("POST", "/api/apply", body=b"{}", headers={
                    "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
                connection.getresponse().read()
                connection.close()
            samples = Path(directory) / "samples.jsonl"
            deadline = time.monotonic() + 4 * seconds + 30
            while time.monotonic() < deadline:
                lines = samples.read_text().splitlines() if samples.exists() else []
                if len(lines) >= 3:
                    # The first period holds the start (and the Apply). The second is idle.
                    first, second = json.loads(lines[1]), json.loads(lines[2])
                    return {"cpu_seconds": round(second["cpu_user"] + second["cpu_system"]
                                                 - first["cpu_user"] - first["cpu_system"], 3),
                            "over_seconds": round(second["t"] - first["t"], 1),
                            "rss_kb": second["rss_kb"], "threads": second["native_threads"]}
                time.sleep(1)
            raise RuntimeError("the dashboard wrote no samples")
        finally:
            process.terminate()
            process.wait(timeout=30)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--idle-seconds", type=float, default=60.0)
    arguments = parser.parse_args(argv)
    for row in imports():
        print("%(process)-14s import %(import_ms)5d ms  rss %(rss_kb)6d kB  modules %(modules)4d "
              "(jittersim %(jittersim_modules)d)" % row)
    print("browser proxy idle: %.3f s CPU in %s s" % (idle_proxy(arguments.idle_seconds),
                                                       arguments.idle_seconds))
    for applied in (False, True):
        print("dashboard, helper and scheduler idle, %s: %s" % (
            "after one Apply" if applied else "never applied",
            idle_dashboard(arguments.idle_seconds, applied=applied)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Measure the dashboard's presets through real kernel queues, for a report.

Run it in the kernel test container, which creates and deletes its own
network namespaces:

    docker build -f tests/kernel/Dockerfile -t jittersim-kernel-tests .
    docker run --rm --privileged jittersim-kernel-tests python -m tests.kernel.measure_profiles
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.measure import comparison_table, load_run
from jittersim.networking.applier import NetworkApplier

from .lab import (
    CLIENT,
    CLIENT_ADDRESS,
    PI,
    ROUTER,
    ROUTER_ADDRESS,
    TOPOLOGY,
    NamespaceExecutor,
    create_lab,
    remove_lab,
    run_in,
)

# The dashboard's presets of the same names.
PROFILES = (
    Profile(id="normal", name="Normal"),
    Profile(
        id="good-4g",
        name="Good 4G",
        upload=Impairment(delay_ms=25, jitter_ms=5, bandwidth_kbit=10_000),
        download=Impairment(delay_ms=25, jitter_ms=5, bandwidth_kbit=30_000),
    ),
    Profile(
        id="3g",
        name="3G",
        upload=Impairment(delay_ms=100, jitter_ms=20, bandwidth_kbit=330),
        download=Impairment(delay_ms=100, jitter_ms=20, bandwidth_kbit=780),
    ),
    Profile(
        id="lossy-wifi",
        name="Lossy Wi-Fi",
        upload=Impairment(delay_ms=5, jitter_ms=15, loss_percent=5, loss_burst_packets=3),
        download=Impairment(delay_ms=5, jitter_ms=15, loss_percent=5, loss_burst_packets=3),
    ),
    Profile(
        id="satellite",
        name="Satellite",
        upload=Impairment(
            delay_ms=300, jitter_ms=20, loss_percent=1, loss_burst_packets=3, bandwidth_kbit=2_000
        ),
        download=Impairment(
            delay_ms=300, jitter_ms=20, loss_percent=1, loss_burst_packets=3, bandwidth_kbit=10_000
        ),
    ),
)
DOWNLOAD_BYTES = 500_000


def _measure(profile: Profile, output: Path) -> None:
    # Under loss, TCP can stall past the download timeout now and then.
    for attempt in (1, 2):
        result = run_in(CLIENT, sys.executable, "-m", "jittersim.measure", "measure",
                        "--label", profile.name, "--target", ROUTER_ADDRESS,
                        "--count", "200", "--interval", "0.05",
                        "--download", f"http://{ROUTER_ADDRESS}:8000/file.bin",
                        "--output", str(output), check=False)
        if result.returncode == 0:
            return
    raise RuntimeError(f"measuring {profile.name} failed twice: {result.stderr.strip()}")


def main() -> int:
    runs = []
    create_lab()
    server = None
    try:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "file.bin").write_bytes(os.urandom(DOWNLOAD_BYTES))
            # A web server beyond the Pi, so downloads pass through the queues.
            server = subprocess.Popen(
                ["ip", "netns", "exec", ROUTER, sys.executable, "-m", "http.server", "8000",
                 "--bind", ROUTER_ADDRESS, "--directory", directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1)
            applier = NetworkApplier(executor=NamespaceExecutor(PI), topology=TOPOLOGY)
            for profile in PROFILES:
                device = Device("test-device", "Test device", CLIENT_ADDRESS, profile.id)
                applier.apply(plan=compile_plan(devices=(device,), profiles={profile.id: profile}))
                output = Path(directory, f"{profile.id}.json")
                _measure(profile, output)
                runs.append(load_run(output))
            applier.reset()
    finally:
        if server is not None:
            server.terminate()
            server.wait(timeout=5)
        remove_lab()
    print(comparison_table(runs), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

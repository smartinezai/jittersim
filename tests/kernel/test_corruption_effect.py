"""Isolate upload corruption from ordinary loss in the existing namespace lab."""

import json
import os
import re
import select
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.renderer import UPLOAD_DEVICE

from . import lab
from .test_upstream_safety import CAPTURE, CLIENT_PATTERN, PI_PATTERN, inspect_frames

ENABLED = (
    sys.platform.startswith("linux")
    and os.environ.get("JITTERSIM_KERNEL_TESTS") == "1"
    and os.geteuid() == 0
    and all(shutil.which(tool) for tool in ("ip", "tc", "nft", "ping"))
)
CLIENT_COUNT = 100
PI_COUNT = 20
# Reuse the established capture with a strict retention limit for this case.
BOUNDED_CAPTURE = CAPTURE.replace(
    "frames.append(data.hex())",
    'frames.append(data.hex())\n        if len(frames) > 1024:\n'
    '            raise RuntimeError("capture frame limit exceeded")',
)


def bounded_lab_run(*argv, check=True, stdin=None):
    """Keep existing lab setup and removal commands finite without changing it."""
    return subprocess.run(argv, input=stdin, capture_output=True, text=True,
                          check=check, timeout=5)


@unittest.skipUnless(ENABLED, "needs root on Linux with JITTERSIM_KERNEL_TESTS=1")
class CorruptionEffectTests(unittest.TestCase):
    def setUp(self):
        bounded_commands = patch.object(lab, "run", bounded_lab_run)
        bounded_commands.start()
        self.addCleanup(bounded_commands.stop)
        # Registered before creation so partial setup is removed as well.
        self.addCleanup(self.remove_namespaces)
        lab.create_lab()
        self.applier = NetworkApplier(executor=lab.NamespaceExecutor(lab.PI),
                                      topology=lab.TOPOLOGY)
        self.addCleanup(self.applier.reset)
        self.pi_mac = lab.run_in(lab.PI, "cat", "/sys/class/net/eth0/address").stdout.strip()
        self.router_mac = lab.run_in(lab.ROUTER, "cat", "/sys/class/net/up0/address").stdout.strip()

    def remove_namespaces(self):
        failures = []
        for namespace in (lab.PI, lab.ROUTER, lab.CLIENT, lab.OTHER_CLIENT):
            try:
                bounded_lab_run("ip", "netns", "del", namespace, check=False)
                result = bounded_lab_run("ip", "netns", "list")
                if any(line.split()[0] == namespace for line in result.stdout.splitlines()):
                    failures.append(namespace)
            except (OSError, subprocess.TimeoutExpired) as error:
                failures.append(f"{namespace}: {error}")
        self.assertEqual(failures, [], "namespace cleanup could not be confirmed")
        print("corruption-effect namespace cleanup confirmed", flush=True)

    @staticmethod
    def reap(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait(timeout=2)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    def phase(self, percent):
        self.applier.reset()
        upload = Impairment(corrupt_percent=percent)
        # Equality proves every other applied impairment retains its zero/default value.
        profile = Profile("corruption-only", "Corruption only", upload=upload)
        self.assertEqual(profile.download, Impairment())
        plan = compile_plan(
            devices=[Device("client", "Client", lab.CLIENT_ADDRESS, profile.id)],
            profiles={profile.id: profile},
        )
        for queue in plan.queues:
            self.assertEqual(queue.impairment,
                             upload if queue.direction.value == "upload" else Impairment())
        report = self.applier.apply(plan=plan)
        self.assertEqual(report.upload_corruption_applied, bool(percent))
        uplink = lab.run_in(lab.PI, "tc", "qdisc", "show", "dev", "eth0").stdout
        self.assertNotRegex(uplink, r"\b(?:corrupt|duplicate|reorder) [0-9.]*[1-9]")
        capture = subprocess.Popen(
            ["ip", "netns", "exec", lab.ROUTER, sys.executable, "-c",
             BOUNDED_CAPTURE, "up0", "4"], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        try:
            self.assertTrue(select.select([capture.stdout], [], [], 2)[0], "capture not ready")
            self.assertEqual(capture.stdout.readline().strip(), "ready")
            # Fixed send counts use a response wait and an outer subprocess deadline.
            for namespace, count, pattern in (
                (lab.CLIENT, CLIENT_COUNT, CLIENT_PATTERN), (lab.PI, PI_COUNT, PI_PATTERN)
            ):
                result = lab.run_in(namespace, "ping", "-n", "-q", "-c", str(count),
                                    "-i", "0.005", "-W", "1", "-s", "64", "-p",
                                    f"{pattern:02x}", lab.ROUTER_ADDRESS, check=False)
                self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
                self.assertIn(f"{count} packets transmitted", result.stdout, result.stdout)
            output, errors = capture.communicate(timeout=6)
            self.assertEqual(capture.returncode, 0, errors)
        finally:
            self.reap(capture)
        frames = [bytes.fromhex(frame) for frame in json.loads(output)]
        problems, echoes = inspect_frames(frames, pi_mac=self.pi_mac, router_mac=self.router_mac)
        self.assertEqual(problems, [], problems[:10])
        client = set(echoes[CLIENT_PATTERN])
        own = set(echoes[PI_PATTERN])
        self.assertEqual(len(own), PI_COUNT)
        if percent:
            ifb = lab.run_in(lab.PI, "tc", "-s", "qdisc", "show", "dev", UPLOAD_DEVICE).stdout
            self.assertRegex(ifb, r"\bcorrupt 100%(?:\s|$)")
            counts = [int(n) for n in re.findall(r"Sent \d+ bytes (\d+) pkt", ifb)]
            self.assertTrue(counts and max(counts) > CLIENT_COUNT // 2, ifb)
            # Allow ignored Ethernet flips and the first unmarked packet.
            self.assertLess(len(client), CLIENT_COUNT // 2)
        else:
            ifb = "not requested"
            self.assertEqual(len(client), CLIENT_COUNT)
        print(json.dumps({"corrupt_percent": percent, "offered": CLIENT_COUNT,
                          "router_received": len(client), "pi_control_received": len(own),
                          "router_frames_clean": True, "uplink": uplink, "ifb": ifb}), flush=True)
        return len(client)

    def test_upload_corruption_only_changes_delivery_and_keeps_router_clean(self):
        before = self.phase(0)
        damaged = self.phase(100)
        after = self.phase(0)
        self.assertGreater(before, damaged)
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()

"""Run JitterSim network commands inside a disposable Linux network namespace.

This script deliberately uses real nft and tc commands. Run it only in an
isolated container with NET_ADMIN and NET_RAW capabilities.
"""

import json
import socket
import subprocess
from dataclasses import replace
from pathlib import Path

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import ServiceAssignmentRule
from jittersim.networking.applier import ApplyError, NetworkApplier
from jittersim.networking.browser_proxy import _set_socket_mark
from jittersim.networking.executor import SubprocessExecutor
from jittersim.networking.topology import Topology


def main() -> None:
    if not Path("/.dockerenv").exists():
        raise RuntimeError("run this smoke check only inside a disposable container")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        _set_socket_mark(probe, 2)
        if probe.getsockopt(socket.SOL_SOCKET, socket.SO_MARK) != 2:
            raise AssertionError("socket mark did not reach the kernel")
    firewall = (
        Path("deploy/raspberry-pi/jittersim-control-firewall.nft")
        .read_text()
        .replace("@UPLINK_INTERFACE@", "eth0")
        .replace("@TEST_INTERFACE@", "jittersim0")
    )
    subprocess.run(["nft", "-c", "-f", "-"], input=firewall, text=True, check=True)
    subprocess.run(["ip", "link", "add", "jittersim0", "type", "dummy"], check=True)
    subprocess.run(["ip", "link", "set", "jittersim0", "up"], check=True)
    topology = Topology(uplink_interface="eth0", test_interface="jittersim0")
    applier = NetworkApplier(executor=SubprocessExecutor(), topology=topology)
    device = Device("alice", "Alice", "192.168.50.10", "normal")
    plan = compile_plan(
        devices=(device,),
        profiles={"normal": Profile(id="normal", name="Normal")},
        service_assignment_rules=(
            ServiceAssignmentRule(
                assignment_id="video",
                device_id=device.id,
                device_ip=device.ipv4_address,
                domains=("youtube.com",),
                mark=0,
                upload_impairment=Impairment(delay_ms=50),
                download_impairment=Impairment(delay_ms=50),
            ),
        ),
    )
    normal_plan = compile_plan(
        devices=(device,),
        profiles={"normal": Profile(id="normal", name="Normal")},
        service_assignment_rules=(
            replace(
                plan.service_assignment_rules[0],
                upload_impairment=Impairment(),
                download_impairment=Impairment(),
            ),
        ),
    )
    try:
        try:
            applier.apply(plan=plan)
        except ApplyError as error:
            raise AssertionError(f"first apply failed: {error.cause.stderr}") from error
        for interface in ("eth0", "jittersim0"):
            queues = json.loads(
                subprocess.check_output(["tc", "-j", "qdisc", "show", "dev", interface])
            )
            if not any(
                queue.get("parent") == "10:1" and queue.get("kind") == "fq_codel"
                for queue in queues
            ):
                raise AssertionError("ordinary traffic lost its fair queue")
        counters = json.loads(applier.traffic_counters())["nftables"]
        if not any(
            item.get("rule", {}).get("comment") == "usage-device alice upload"
            for item in counters
        ):
            raise AssertionError("Linux did not return the device traffic counters")
        applier.apply(plan=plan)
        try:
            applier.apply(plan=normal_plan)
        except ApplyError as error:
            raise AssertionError(
                f"normal profile transition failed: {error.cause.stderr}"
            ) from error
        for interface in ("eth0", "jittersim0"):
            queues = json.loads(
                subprocess.check_output(["tc", "-j", "qdisc", "show", "dev", interface])
            )
            # The service assignment has mark 2, so it uses class 10:3.
            if not any(
                queue.get("parent") == "10:3" and queue.get("kind") == "fq_codel"
                for queue in queues
            ):
                raise AssertionError("ordinary service traffic did not regain a fair queue")
        applier.apply(plan=plan)
        bob = Device("bob", "Bob", "192.168.50.11", "rate")
        two_rate_plan = compile_plan(
            devices=(replace(device, profile_id="rate"), bob),
            profiles={
                "rate": Profile(
                    id="rate",
                    name="Rate limit",
                    upload=Impairment(bandwidth_kbit=2_000),
                    download=Impairment(bandwidth_kbit=2_000),
                ),
            },
        )
        duplication_plan = compile_plan(
            devices=(replace(device, profile_id="duplicate"), replace(bob, profile_id="normal")),
            profiles={
                "normal": Profile(id="normal", name="Normal"),
                "duplicate": Profile(
                    id="duplicate",
                    name="Duplicate",
                    upload=Impairment(duplicate_percent=10),
                    download=Impairment(duplicate_percent=10),
                ),
            },
        )
        applier.apply(plan=two_rate_plan)
        try:
            applier.apply(plan=duplication_plan)
            applier.apply(plan=duplication_plan)
            applier.apply(plan=two_rate_plan)
        except ApplyError as error:
            raise AssertionError(
                f"packet duplication transition failed: {error.cause.stderr}"
            ) from error
        applier.reset()
        applier.reset()
        subprocess.run(
            ["tc", "qdisc", "replace", "dev", "jittersim0", "root", "handle", "20:", "pfifo"],
            check=True,
        )
        try:
            applier.apply(plan=plan)
        except ApplyError:
            pass
        else:
            raise AssertionError("foreign root queue was replaced")
        applier.reset()
        output = subprocess.check_output(["tc", "-j", "qdisc", "show", "dev", "jittersim0", "root"])
        if b'"handle":"20:"' not in output:
            raise AssertionError("reset removed a foreign root queue")
    finally:
        subprocess.run(
            ["nft", "delete", "table", "inet", "jittersim"],
            check=False,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(["ip", "link", "del", "jittersim0"], check=False)
    print("Linux network smoke check passed")


if __name__ == "__main__":
    main()

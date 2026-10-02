"""Verify plans with many shaped marks on the real kernel in an isolated container.

tc reads a class minor number as hexadecimal while `prio bands` is a decimal count so
a plan with nine or more shaped marks used to name classes the root queue did not have.
"""

import json
import subprocess
from pathlib import Path

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import MAX_SHAPED_MARKS_PER_PLAN, compile_plan
from jittersim.networking.applier import ApplyError, NetworkApplier
from jittersim.networking.executor import SubprocessExecutor
from jittersim.networking.renderer import ROOT_HANDLE, queue_handle, queue_parent
from jittersim.networking.topology import Topology

UPLINK = "up0"
TEST = "jittersim0"
# The root queue has one more band than there are shaped marks and band 1 stays ordinary.
DEVICE_COUNTS = (8, 9, MAX_SHAPED_MARKS_PER_PLAN)


def run(*args):
    subprocess.run(args, check=True, capture_output=True)


def tc_json(*args):
    return json.loads(subprocess.check_output(("tc", "-j") + args, text=True))


def upload_delay_ms(mark):
    return 100 + mark


def download_delay_ms(mark):
    return 200 + mark


def make_plan(count):
    """Give every device its own delay in each direction so a swapped queue is visible."""
    profiles = {}
    devices = []
    for mark in range(1, count + 1):
        profile_id = f"p{mark:02d}"
        profiles[profile_id] = Profile(
            profile_id,
            f"Profile {mark:02d}",
            upload=Impairment(delay_ms=upload_delay_ms(mark)),
            download=Impairment(delay_ms=download_delay_ms(mark)),
        )
        # Devices are marked in the order of their ids.
        devices.append(
            Device(f"d{mark:02d}", f"Device {mark:02d}", f"192.168.50.{10 + mark}", profile_id)
        )
    return compile_plan(devices=tuple(devices), profiles=profiles)


def apply_plan(applier, count):
    plan = make_plan(count)
    assert len(plan.queues) == 2 * count, plan.queues
    try:
        applier.apply(plan=plan)
    except ApplyError as error:
        step = error.failed_command_index + 1
        raise AssertionError(
            f"applying a plan with {count} devices failed at step {step}: "
            f"{error.cause} ({error.cause.stderr.strip()})"
        ) from error


def check_queues(interface, count, delay_ms):
    queues = tc_json("qdisc", "show", "dev", interface)
    roots = [queue for queue in queues if queue.get("root")]
    assert len(roots) == 1, roots
    assert roots[0]["kind"] == "prio" and roots[0]["handle"] == ROOT_HANDLE, roots
    assert roots[0]["options"]["bands"] == count + 1, roots
    leaves = [queue for queue in queues if queue["kind"] == "netem"]
    assert len(leaves) == count, (interface, leaves)
    by_handle = {queue["handle"]: queue for queue in leaves}
    assert set(by_handle) == {queue_handle(mark) for mark in range(1, count + 1)}, by_handle
    for mark in range(1, count + 1):
        leaf = by_handle[queue_handle(mark)]
        assert leaf["parent"] == queue_parent(mark), (interface, mark, leaf)
        # The device's own delay sits in its own class.
        assert round(leaf["options"]["delay"]["delay"] * 1000) == delay_ms(mark), (
            interface,
            mark,
            leaf,
        )
    return by_handle


def check_filters(interface, count):
    found = set()
    for item in tc_json("filter", "show", "dev", interface, "parent", ROOT_HANDLE):
        options = item.get("options")
        if item["kind"] == "fw" and options:
            mark = int(options["fw"]["mark"], 16)
            found.add((item["protocol"], item["pref"], mark, options["classid"]))
    for mark in range(1, count + 1):
        assert ("ip", mark, mark, queue_parent(mark)) in found, (interface, mark, found)
        assert ("ipv6", mark + 100, mark, queue_parent(mark)) in found, (interface, mark, found)


def check_plan_applied(count):
    for interface, delay_ms in ((UPLINK, upload_delay_ms), (TEST, download_delay_ms)):
        by_handle = check_queues(interface, count, delay_ms)
        check_filters(interface, count)
        # Spelled out because the kernel prints a class minor number in hexadecimal.
        if count >= 9:
            assert by_handle[queue_handle(9)]["parent"] == "10:a", by_handle[queue_handle(9)]
        if count == 15:
            assert by_handle["25:"]["parent"] == "10:10", by_handle["25:"]


def main():
    if not Path("/.dockerenv").exists():
        isolated = (
            Path("/proc/self/ns/net").stat().st_ino
            != Path("/proc/1/ns/net").stat().st_ino
        )
        interfaces = json.loads(
            subprocess.check_output(["ip", "-j", "link", "show"], text=True)
        )
        if not isolated or {item["ifname"] for item in interfaces} != {"lo"}:
            raise RuntimeError(
                "Run only in a disposable container or an empty separate network namespace"
            )
    for interface, peer in ((TEST, "client0"), (UPLINK, "remote0")):
        run("ip", "link", "add", interface, "type", "veth", "peer", "name", peer)
        run("ip", "link", "set", interface, "up")
        run("ip", "link", "set", peer, "up", "promisc", "on")
    applier = NetworkApplier(
        executor=SubprocessExecutor(), topology=Topology(UPLINK, TEST)
    )
    try:
        for count in DEVICE_COUNTS:
            applier.reset()
            apply_plan(applier, count)
            check_plan_applied(count)
            print(f"{count} shaped devices applied with {count} netem queues per direction")
        # Applying a smaller plan over a larger one must keep working too.
        apply_plan(applier, 9)
        for interface, delay_ms in ((UPLINK, upload_delay_ms), (TEST, download_delay_ms)):
            check_queues(interface, 9, delay_ms)
        print("9 shaped devices applied over a plan with 15")
        applier.reset()
        for interface in (UPLINK, TEST):
            roots = tc_json("qdisc", "show", "dev", interface, "root")
            assert all(queue["handle"] != ROOT_HANDLE for queue in roots), roots
        print("Queues above eight use hexadecimal classes and apply on the real kernel.")
    finally:
        applier.reset()
        run("ip", "link", "del", TEST)
        run("ip", "link", "del", UPLINK)


if __name__ == "__main__":
    main()

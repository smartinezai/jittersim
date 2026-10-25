"""Crash at every step: Panic and Reset remove every impairment in the kernel lab.

The unit tests pin Panic with fake tc and nft. These tests run the real code
path (HelperService "normal-access" and "reset", NetworkApplier.reset and the
household release in AccessManager) in-process against real tc, nftables, ifb
and Unbound in the lab's network namespaces, and judge the result from the
kernel state and from what the router receives.

A command shim sits between the helper and the lab. It counts every command
that changes kernel state and can, at a chosen one:

- crash: raise SimulatedCrash (a BaseException, so no cleanup code of the
  helper runs, like a killed helper process)
- fail: answer like a failed command, so the applier's own rollback runs
- hang: run `sleep` in its place, so only the Panic deadline ends it
- refuse an ifb device, like a kernel without the ifb module

After each case the tests check that:

- no JitterSim qdisc, filter, ingress redirect or ifb device is left, no
  experiment nftables table and no household restriction or transition guard
  (the household table may stay with its open, neutral rules)
- fresh pings from every lab address and a fresh UDP flow reach the router
  with no loss, no copies and lab latency, and every frame the router gets is
  clean (tests/kernel/test_upstream_safety.py's checks)

The matrix and its results are in docs/panic-crash-matrix-2026-10-18.md.
Each step prints one `MATRIX|...` line, which is where that table comes from.

These tests need root on Linux and JITTERSIM_KERNEL_TESTS=1. On a Mac, run
tests/kernel/run-in-docker.sh.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from dataclasses import replace
from ipaddress import IPv4Address
from pathlib import Path

from jittersim.config.models import (
    MAX_GLOBAL_BLOCKED_DOMAINS,
    AccessOptions,
    Device,
    Impairment,
    MacPolicy,
    Profile,
    SiteBlock,
    WebsitePolicy,
)
from jittersim.engine.access import AccessClient, AccessPlan
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import MAX_GLOBAL_BLOCKED_ADDRESSES, compile_plan
from jittersim.engine.plan import (
    EveryoneElseRule,
    GeneralProfileRule,
    ServiceAssignmentRule,
    ServiceListRule,
    ServiceQuotaRule,
    WebsiteFilter,
)
from jittersim.networking.access import (
    DNS_PORT, DNS_VIEW_PORT_FIRST, DNS_VIEW_PORT_LAST, AccessManager,
)
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.commands import CommandResult
from jittersim.networking.executor import CommandExecutionError
from jittersim.networking.helper import (
    PANIC_ANSWER_SECONDS,
    HelperService,
    restore_access_at_start,
)
from jittersim.networking.renderer import UPLOAD_DEVICE
from jittersim.panic_guard import PANIC_GUARD_FIELDS, PANIC_GUARD_FORMAT, PanicGuard

from .lab import (
    CLIENT,
    CLIENT_ADDRESS,
    CLIENT_MAC,
    OTHER_CLIENT,
    OTHER_CLIENT_MAC,
    PI,
    ROUTER,
    ROUTER_ADDRESS,
    TOPOLOGY,
    create_lab,
    remove_lab,
)
from .lab import run_in as _in
from .test_access import DnsServiceExecutor
from .test_upstream_safety import CAPTURE, inspect_frames

ENABLED = (
    sys.platform.startswith("linux")
    and os.environ.get("JITTERSIM_KERNEL_TESTS") == "1"
    and os.geteuid() == 0
    and all(shutil.which(tool) for tool in ("ip", "tc", "nft", "ping", "unbound", "dig"))
)

# Every packet setting at once, both directions. Corruption only uploads: it
# runs on the ifb device before the Pi forwards (see the upstream safety tests).
HARSH_UP = Impairment(delay_ms=60, jitter_ms=10, loss_percent=5, loss_burst_packets=3,
                      reorder_percent=30, duplicate_percent=30, corrupt_percent=30,
                      bandwidth_kbit=512)
# The kernel refuses a duplicating netem next to any other netem on one interface
# (check_netem_in_tree, see renderer.CHILD_HANDLE_BASES), so the heavy plan's many
# download queues do not duplicate. The Everyone plan has one download queue
# and duplicates there.
HARSH_DOWN = replace(HARSH_UP, corrupt_percent=0, duplicate_percent=0)
BLACKHOLE = Impairment(loss_percent=100)
PROFILES = {
    "harsh": Profile(id="harsh", name="Harsh", upload=HARSH_UP, download=HARSH_DOWN),
    "everyone": Profile(id="everyone", name="Everyone", upload=HARSH_UP,
                        download=replace(HARSH_UP, corrupt_percent=0)),
    "blackhole": Profile(id="blackhole", name="Blackhole", upload=BLACKHOLE, download=BLACKHOLE),
}
DEVICE_A, DEVICE_B, DEVICE_C = CLIENT_ADDRESS, "192.168.50.11", "192.168.50.12"
OTHER_ADDRESS = "192.168.50.20"
# A protected device that is not on the lab link, and a MAC the experiment denies.
PROTECTED_MAC = "02:00:00:00:50:99"
DENIED_MAC = "02:00:00:00:50:98"
# Household rules: a website block (DNS redirect to Unbound) for device a, DNS
# failures, and a MAC block of the other client.
HOUSEHOLD = AccessPlan(
    options=AccessOptions(dns_failure_percent=20),
    mac=MacPolicy("denylist", (), (OTHER_CLIENT_MAC,)),
    blocks=(SiteBlock("video", "Video", ("video.example.com",), device_id="a"),),
    clients=(AccessClient("a", CLIENT_ADDRESS, CLIENT_MAC, ("video",)),),
)


def heavy_plan(*, access=HOUSEHOLD, blocked=(IPv4Address("203.0.113.9"),)):
    """Devices, Everyone else, every service rule, blocks, a protected device, household rules."""

    a = IPv4Address(DEVICE_A)
    return compile_plan(
        devices=[Device("a", "a", DEVICE_A, "harsh", CLIENT_MAC),
                 Device("b", "b", DEVICE_B, "blackhole"),
                 Device("c", "c", DEVICE_C, "harsh")],
        profiles=PROFILES,
        protected_mac_addresses=(PROTECTED_MAC,),
        mac_policy=MacPolicy("denylist", (), (DENIED_MAC,)),
        global_blocked_addresses=blocked,
        website_filters=(WebsiteFilter(a, None, (IPv4Address("203.0.113.7"),)),),
        blocked_device_addresses=(IPv4Address(DEVICE_C),),
        service_quota_rules=[ServiceQuotaRule(
            quota_id="router-quota", device_id="a", device_ip=a,
            destination_addresses=(IPv4Address(ROUTER_ADDRESS),), mark=0,
            upload_impairment=HARSH_UP, download_impairment=HARSH_DOWN, active=True)],
        service_assignment_rules=[ServiceAssignmentRule(
            assignment_id="video", device_id="a", device_ip=a, domains=("video.example",),
            mark=0, upload_impairment=HARSH_UP, download_impairment=HARSH_DOWN)],
        service_list_rules=[
            ServiceListRule("router", ("router.example",), 0, HARSH_UP, HARSH_DOWN)],
        everyone_else=EveryoneElseRule(mark=0, upload_impairment=HARSH_UP,
                                       download_impairment=HARSH_DOWN),
        access=access,
    )


def full_block_list_plan():
    """The heavy plan with the largest global block list and the most blocked addresses."""

    names = tuple(f"ads{n}.tracker-{n % 97}.example.com" for n in range(MAX_GLOBAL_BLOCKED_DOMAINS))
    # 198.18.0.0/15 is for benchmarks. Every other address, so nft cannot merge ranges.
    blocked = tuple(IPv4Address(0xC6120000 + 2 * n) for n in range(MAX_GLOBAL_BLOCKED_ADDRESSES))
    # This probe requires a DNS answer; random DNS faults would instead reject
    # 20% of its queries before Unbound can answer the blocked name.
    access = replace(
        HOUSEHOLD,
        options=replace(HOUSEHOLD.options, dns_failure_percent=0),
        websites=WebsitePolicy(blocked_domains=names),
    )
    return heavy_plan(access=access, blocked=blocked)


def everyone_plan():
    """The profile for Everyone with a protected device on the lab link."""

    return compile_plan(devices=[], profiles=PROFILES, protected_mac_addresses=(OTHER_CLIENT_MAC,),
                        general_profile=GeneralProfileRule("everyone"))


class SimulatedCrash(BaseException):
    """The helper process ended here (killed, out of memory). No cleanup runs."""


def changes_state(command) -> bool:
    """Whether a command can change kernel or service state (not a read)."""

    arguments = command.arguments
    if command.executable in ("dig", "ping", "iw", "unbound-checkconf"):
        return False
    if command.executable == "systemctl":
        return arguments[:1] != ("is-active",)
    if command.executable in ("tc", "ip"):
        return "show" not in arguments
    if command.executable == "nft":
        return "list" not in arguments and "--check" not in arguments
    return True


def describe(command) -> str:
    if command.executable == "nft" and command.stdin:
        tables = sorted(set(re.findall(r"table (?:inet|netdev) (\w+)", command.stdin)))
        return f"nft -f ({', '.join(tables)})"
    return " ".join((command.executable,) + command.arguments)[:70]


class StepShim:
    """Count the state-changing commands and crash, fail or hang at a chosen one."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.arm()

    def arm(self, *, crash_at=None, fail_at=None, hang=None, ifb_absent=False) -> None:
        self.changes = 0
        self.crash_at, self.fail_at, self.hang = crash_at, fail_at, hang
        self.ifb_absent = ifb_absent
        self.record = []

    def run(self, command):
        if self.ifb_absent and command.arguments[-2:] == ("type", "ifb"):
            # What `ip link add ... type ifb` answers without the module.
            if command.ignore_failure:
                return CommandResult(return_code=2, stderr="Error: Unknown device type.\n")
            raise CommandExecutionError("ifb missing", return_code=2,
                                        stderr="Error: Unknown device type.\n")
        if changes_state(command):
            index = self.changes
            self.changes += 1
            self.record.append(command)
            if index == self.crash_at:
                raise SimulatedCrash(f"crashed before step {index}: {describe(command)}")
            if index == self.fail_at:
                if command.ignore_failure:
                    return CommandResult(return_code=2, stderr="simulated failure\n")
                raise CommandExecutionError(f"simulated failure: {command.purpose}",
                                            return_code=2, stderr="simulated failure\n")
        if self.hang is not None and self.hang(command):
            command = replace(command, executable="sleep", arguments=("60",), stdin=None)
        return self.inner.run(command)


PING_COUNT = 40
UDP_COUNT = 200
UDP_SENDER = r"""
import socket, struct, sys, time
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
    udp.bind((sys.argv[2], 0))
    for number in range(int(sys.argv[3])):
        udp.sendto(b"JSU" + struct.pack("!I", number) + bytes(40), (sys.argv[1], 9999))
        time.sleep(0.002)
"""
PING_SUMMARY = re.compile(r"(\d+) packets transmitted, (\d+) received(?:, \+(\d+) duplicates)?")
PING_RTT = re.compile(r"= [\d.]+/([\d.]+)/")
# How often the crash matrix also checks traffic (see run_matrix).
TRAFFIC_EVERY = 3
# The lab answers in well under a millisecond. The harsh profile adds 60 ms.
NORMAL_RTT_MS = 10.0


def household_restrictions(access):
    """Ignore only the neutral resolver privacy guard left by household release."""

    privacy_guard = (
        f'iifname != "{TOPOLOGY.test_interface}" iifname != "lo" '
        f'meta l4proto {{ tcp, udp }} th dport {{ {DNS_PORT}, '
        f'{DNS_VIEW_PORT_FIRST}-{DNS_VIEW_PORT_LAST} }} drop'
    )
    return [line.strip() for line in access.splitlines()
            if re.search(r"\b(drop|reject|redirect|accept|mark set)\b", line)
            and "policy accept" not in line and line.strip() != privacy_guard]


class ResolverPrivacyGuardTests(unittest.TestCase):
    guard = ('iifname != "wlan0" iifname != "lo" meta l4proto { tcp, udp } '
             'th dport { 5335, 5400-5656 } drop')

    def test_only_the_exact_neutral_guard_is_ignored(self):
        self.assertEqual(household_restrictions("  " + self.guard + "\n"), [])

    def test_a_guard_that_can_drop_test_or_loopback_traffic_is_not_ignored(self):
        for condition in ('iifname != "wlan0" ', 'iifname != "lo" '):
            with self.subTest(condition=condition):
                rule = self.guard.replace(condition, "")
                self.assertEqual(household_restrictions(rule), [rule])

    def test_redirects_drops_and_direct_view_guards_still_count_as_restrictions(self):
        rules = ['iifname "wlan0" udp dport 53 redirect to :5335',
                 'ether saddr 02:00:00:00:50:98 drop',
                 'iifname "wlan0" th dport 5400 ct original proto-dst != 53 drop']
        self.assertEqual(household_restrictions("\n".join([self.guard, *rules])), rules)


@unittest.skipUnless(ENABLED, "needs root on Linux with JITTERSIM_KERNEL_TESTS=1")
class PanicCrashMatrixTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        create_lab()
        cls.pi_mac = _in(PI, "cat", "/sys/class/net/eth0/address").stdout.strip()
        cls.router_mac = _in(ROUTER, "cat", "/sys/class/net/up0/address").stdout.strip()
        cls.temporary = tempfile.TemporaryDirectory()
        # The household directory: Unbound reads its configuration here before it
        # drops privileges.
        cls.directory = Path(cls.temporary.name)
        cls.directory.chmod(0o755)
        cls.executor = DnsServiceExecutor(cls.directory)
        cls.shim = StepShim(cls.executor)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.executor.stop()
        cls.executor.log.close()
        cls.temporary.cleanup()
        remove_lab()

    def setUp(self) -> None:
        self.shim.arm()
        self.addCleanup(self.normalize)

    def normalize(self) -> None:
        """Leave the lab clean for the next test whatever this one did."""

        self.shim.arm()
        self.fresh_state()
        self.helper().handle_payload({"action": "normal-access"})

    # The helper as the Pi builds it (run_helper), on the lab and in a test folder.

    def fresh_state(self) -> None:
        state = self.directory / "state"
        shutil.rmtree(state, ignore_errors=True)
        state.mkdir()
        PanicGuard(state / "control" / "panic-state.json", owner_uid=os.geteuid()).write(
            {"version": PANIC_GUARD_FORMAT, **dict.fromkeys(PANIC_GUARD_FIELDS, False)},
            initialize_only=True)

    def helper(self, *, restart=False) -> HelperService:
        """A helper, or with `restart` one that starts like the service after a crash."""

        state = self.directory / "state"
        access = AccessManager(executor=self.shim, topology=TOPOLOGY, directory=self.directory,
                               runtime_path=state / "access-runtime.json")
        service = HelperService(
            NetworkApplier(executor=self.shim, topology=TOPOLOGY),
            active_plan_path=state / "active-plan.json",
            gateway_state_path=state / "gateway.json",
            access_manager=access,
            panic_guard=PanicGuard(state / "control" / "panic-state.json", owner_uid=os.geteuid()),
        )
        if restart:
            restore_access_at_start(access, panic_stopped=service._household_stop_latched)
        return service

    # What is left after Panic or Reset.

    def leftovers(self, *, household=True):
        """Every JitterSim object still in the Pi's kernel, as short descriptions."""

        found = []
        for interface in (TOPOLOGY.uplink_interface, TOPOLOGY.test_interface):
            shown = _in(PI, "tc", "-j", "qdisc", "show", "dev", interface).stdout
            queues = json.loads(shown or "[]")
            for queue in queues:
                if queue.get("kind") not in ("noqueue", "pfifo_fast", "fq_codel", "mq") or \
                        queue.get("handle") not in ("0:", None):
                    found.append(f"{interface} qdisc {queue.get('kind')} {queue.get('handle')}")
            # Plain text: tc's JSON output of some redirect filters is not valid JSON.
            filters = _in(PI, "tc", "filter", "show", "dev", interface, "ingress", check=False)
            if filters.returncode == 0 and filters.stdout.strip():
                found.append(f"{interface} ingress filter")
        if _in(PI, "ip", "link", "show", UPLOAD_DEVICE, check=False).returncode == 0:
            found.append(f"{UPLOAD_DEVICE} device")
        tables = _in(PI, "nft", "list", "tables").stdout
        for family, table in (("inet", "jittersim"), ("netdev", "jittersim_download"),
                              ("inet", "jittersim_access_transition")):
            if re.search(rf"^table {family} {table}$", tables, re.M):
                found.append(f"nft table {family} {table}")
        if household:
            access = _in(PI, "nft", "list", "table", "inet", "jittersim_access", check=False).stdout
            # The release keeps the resolver port private. Nothing else may remain.
            rules = household_restrictions(access)
            found += [f"household rule: {rule}" for rule in rules]
            netdev = _in(PI, "nft", "list", "table", "netdev", "jittersim_access",
                         check=False).stdout
            if "chain" in netdev:
                found.append("household netdev chain")
        return found

    def traffic_problems(self, sources=None):
        """Fresh pings from each source and a fresh UDP flow, judged at the router."""

        if sources is None:
            sources = ((CLIENT, DEVICE_A), (CLIENT, DEVICE_B), (CLIENT, DEVICE_C),
                       (OTHER_CLIENT, OTHER_ADDRESS))
        for name in (PI, CLIENT, OTHER_CLIENT):
            _in(name, "ip", "neigh", "flush", "all", check=False)
        capture = subprocess.Popen(
            ["ip", "netns", "exec", ROUTER, sys.executable, "-c", CAPTURE, "up0", "2.0"],
            stdout=subprocess.PIPE, text=True)
        problems = []
        try:
            if capture.stdout.readline().strip() != "ready":
                return ["the router capture did not start"]
            pings = {
                source: subprocess.Popen(
                    ["ip", "netns", "exec", namespace, "ping", "-n", "-q", "-c", str(PING_COUNT),
                     "-i", "0.01", "-W", "1", "-s", "64", "-p", f"{0xA0 + index:02x}",
                     "-I", source, ROUTER_ADDRESS],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for index, (namespace, source) in enumerate(sources)
            }
            _in(CLIENT, sys.executable, "-c", UDP_SENDER, ROUTER_ADDRESS, DEVICE_A, str(UDP_COUNT))
            for source, process in pings.items():
                output = process.communicate(timeout=20)[0]
                summary, rtt = PING_SUMMARY.search(output), PING_RTT.search(output)
                if not summary:
                    problems.append(f"{source}: no ping summary")
                    continue
                sent, received, copies = int(summary[1]), int(summary[2]), int(summary[3] or 0)
                if received < sent:
                    problems.append(f"{source}: {sent - received}/{sent} pings lost")
                if copies:
                    problems.append(f"{source}: {copies} duplicate replies")
                if rtt and float(rtt[1]) > NORMAL_RTT_MS:
                    problems.append(f"{source}: average round trip {rtt[1]} ms")
            output, _ = capture.communicate(timeout=20)
        finally:
            if capture.poll() is None:
                capture.kill()
        frames = [bytes.fromhex(item) for item in json.loads(output)]
        bad, echoes = inspect_frames(frames, pi_mac=self.pi_mac, router_mac=self.router_mac)
        problems += sorted(set(bad))
        for index, (_, source) in enumerate(sources):
            requests = echoes[0xA0 + index]
            if len(requests) != PING_COUNT:
                problems.append(f"{source}: router got {len(requests)}/{PING_COUNT} echo requests")
        udp = [number for _, number in echoes["udp"]]
        if len(udp) < UDP_COUNT - 1:
            problems.append(f"UDP: router got {len(udp)}/{UDP_COUNT} datagrams")
        if [key for key, count in Counter(udp).items() if count > 1]:
            problems.append("UDP: copies reached the router")
        if udp != sorted(udp):
            problems.append("UDP: datagrams reached the router out of order")
        return problems

    def assert_normal(self, label, *, household=True, sources=None):
        leftovers = self.leftovers(household=household)
        traffic = self.traffic_problems(sources)
        self.assertEqual((leftovers, traffic), ([], []), label)

    # One case of the matrix.

    def apply_until(self, payload, service, **arm):
        self.shim.arm(**arm)
        try:
            answer = service.handle_payload(payload)
            outcome = "applied" if answer.get("ok") else "apply failed"
        except SimulatedCrash:
            outcome = "crashed"
        record = list(self.shim.record)
        self.shim.arm()
        return outcome, record

    def count_steps(self, payload):
        """Apply the whole plan once and return its state-changing commands."""

        self.fresh_state()
        outcome, record = self.apply_until(payload, self.helper())
        self.assertEqual(outcome, "applied")
        # The plan really impairs: device a's pings take the harsh delay.
        self.assertNotEqual(self.traffic_problems(((CLIENT, DEVICE_A),)), [])
        self.assertTrue(self.helper().handle_payload({"action": "normal-access"})["ok"])
        self.assert_normal("after the counting run")
        return record

    def run_matrix(self, name, payload, *, inject, stop, traffic=True):
        """Interrupt the apply before each state-changing command, then stop.

        `inject` is crash or fail, `stop` is panic, restart-panic or reset. The
        kernel state is checked after every case. With `traffic`, the traffic
        check runs after every TRAFFIC_EVERY-th case and the last one (it takes
        about two seconds; the kernel state check is what finds a leftover).
        """

        steps = self.count_steps(payload)
        failures = []
        started = time.monotonic()
        for step in range(len(steps) + 1):
            self.fresh_state()
            service = self.helper()
            outcome, _ = self.apply_until(payload, service, **{f"{inject}_at": step})
            before = ""
            if stop != "panic":
                service = self.helper(restart=True)
                # Recorded, not asserted: what the restarted helper holds before
                # anyone presses Panic or Reset.
                plan_file = (self.directory / "state" / "active-plan.json").exists()
                before = (f"before the stop: {len(self.leftovers(household=False))} "
                          f"experiment objects, active plan file "
                          f"{'present' if plan_file else 'absent'}")
            answer = service.handle_payload(
                {"action": "reset" if stop == "reset" else "normal-access"})
            household = stop != "reset"
            leftovers = self.leftovers(household=household)
            # Reset keeps household rules by design: the MAC-blocked client stays out.
            sources = ((CLIENT, DEVICE_A), (CLIENT, DEVICE_B), (CLIENT, DEVICE_C)) \
                if stop == "reset" else None
            checked = traffic and (step % TRAFFIC_EVERY == 0 or step == len(steps))
            traffic_problems = self.traffic_problems(sources) if checked else []
            command = describe(steps[step]) if step < len(steps) else "(none: apply completed)"
            verdict = "clean" if answer.get("ok") and not leftovers and not traffic_problems \
                else "DEFECT"
            print(f"MATRIX|{name}|{inject}|{stop}|{step}|{command}|{outcome}|"
                  f"{'ok' if answer.get('ok') else answer.get('error')}|"
                  f"{'; '.join(leftovers + traffic_problems) or '-'}|{verdict}"
                  f"{'|' + before if before else ''}", flush=True)
            if verdict != "clean":
                failures.append((step, command, outcome, answer, leftovers, traffic_problems))
            if stop == "reset":
                self.helper().handle_payload({"action": "normal-access"})
        print(f"MATRIX-TIME|{name}|{inject}|{stop}|{len(steps) + 1} cases|"
              f"{time.monotonic() - started:.0f}s", flush=True)
        self.assertEqual(failures, [])

    # The matrix.

    def test_crash_before_each_step_of_a_heavy_apply_then_panic(self) -> None:
        self.run_matrix("heavy", {"action": "apply", "plan": plan_to_payload(heavy_plan())},
                        inject="crash", stop="panic")

    def test_crash_before_each_step_of_a_heavy_apply_then_panic_after_a_restart(self) -> None:
        # The helper service restarts after the crash and reads its state files.
        self.run_matrix("heavy", {"action": "apply", "plan": plan_to_payload(heavy_plan())},
                        inject="crash", stop="restart-panic", traffic=False)

    def test_crash_before_each_step_of_a_heavy_apply_then_reset_after_a_restart(self) -> None:
        self.run_matrix("heavy", {"action": "apply", "plan": plan_to_payload(heavy_plan())},
                        inject="crash", stop="reset", traffic=False)

    def test_a_failed_command_at_each_step_of_a_heavy_apply_then_panic(self) -> None:
        # The applier and the household change roll back themselves first.
        self.run_matrix("heavy", {"action": "apply", "plan": plan_to_payload(heavy_plan())},
                        inject="fail", stop="panic", traffic=False)

    def test_crash_before_each_step_of_the_everyone_profile_then_panic(self) -> None:
        self.run_matrix("everyone", {"action": "apply", "plan": plan_to_payload(everyone_plan())},
                        inject="crash", stop="panic")

    def test_panic_twice_and_panic_with_nothing_running(self) -> None:
        payload = {"action": "apply", "plan": plan_to_payload(heavy_plan())}
        self.fresh_state()
        service = self.helper()
        self.assertTrue(service.handle_payload(payload)["ok"])
        for attempt in ("first", "second"):
            self.assertTrue(service.handle_payload({"action": "normal-access"})["ok"], attempt)
            self.assert_normal(f"after the {attempt} Panic")
        # A third one from a restarted helper with nothing left to remove.
        self.assertTrue(self.helper(restart=True).handle_payload({"action": "normal-access"})["ok"])
        self.assert_normal("after Panic with nothing running")

    def test_a_crash_at_each_step_of_panic_is_finished_by_the_restarted_helper(self) -> None:
        """Panic saved its stop and then the helper died part way through the removal.

        The restarted helper reads the stop (the restart latch): it releases the
        household rules at start, its first gateway check removes the rest
        without anyone pressing Panic again, and a plain apply is refused.
        """

        payload = {"action": "apply", "plan": plan_to_payload(heavy_plan())}
        self.fresh_state()
        service = self.helper()
        self.assertTrue(service.handle_payload(payload)["ok"])
        self.shim.arm()
        self.assertTrue(service.handle_payload({"action": "normal-access"})["ok"])
        steps = list(self.shim.record)
        failures = []
        for step in range(len(steps)):
            self.fresh_state()
            service = self.helper()
            self.assertTrue(service.handle_payload(payload)["ok"])
            self.shim.arm(crash_at=step)
            with self.assertRaises(SimulatedCrash):
                service.handle_payload({"action": "normal-access"})
            self.shim.arm()
            restarted = self.helper(restart=True)
            latched = restarted._panic_stop_latched and restarted._household_stop_latched
            refused = not restarted.handle_payload(payload)["ok"]
            restarted.monitor_gateway_once()
            leftovers = self.leftovers()
            traffic_problems = self.traffic_problems() if step % 4 == 0 else []
            verdict = "clean" if latched and refused and not leftovers and not traffic_problems \
                else "DEFECT"
            notes = leftovers + traffic_problems + ([] if latched else ["stop not latched"]) + \
                ([] if refused else ["plain apply accepted"])
            print(f"MATRIX|heavy|crash-in-panic|restart|{step}|{describe(steps[step])}|crashed|"
                  f"(no second Panic)|{'; '.join(notes) or '-'}|{verdict}", flush=True)
            if verdict != "clean":
                failures.append((step, describe(steps[step]), notes))
        self.assertEqual(failures, [])

    def test_panic_and_reset_remove_a_full_global_block_list_in_time(self) -> None:
        """The largest block list: in the kernel after apply, gone after Panic and Reset."""

        payload = {"action": "apply", "plan": plan_to_payload(full_block_list_plan())}
        last = MAX_GLOBAL_BLOCKED_DOMAINS - 1
        last_name = f"ads{last}.tracker-{last % 97}.example.com"
        for stop in ("normal-access", "reset"):
            with self.subTest(stop=stop):
                self.fresh_state()
                service = self.helper()
                started = time.monotonic()
                self.assertTrue(service.handle_payload(payload)["ok"])
                applied = time.monotonic() - started
                rules = _in(PI, "nft", "list", "chain", "inet", "jittersim", "forward").stdout
                self.assertIn("block global website destinations", rules)
                self.assertEqual(len(re.findall(r"198\.1[89]\.\d+\.\d+", rules)),
                                 MAX_GLOBAL_BLOCKED_ADDRESSES)
                answer = _in(CLIENT, "dig", "@8.8.8.8", last_name, "+time=2", "+tries=1",
                             check=False).stdout
                self.assertIn("status: NXDOMAIN", answer)
                started = time.monotonic()
                self.assertTrue(service.handle_payload({"action": stop})["ok"])
                took = time.monotonic() - started
                print(f"MEASURE|full block list|apply {applied:.1f}s|{stop} {took:.1f}s", flush=True)
                self.assertLess(took, PANIC_ANSWER_SECONDS)
                if stop == "reset":
                    # Reset keeps household rules by design: the MAC-blocked client stays out.
                    self.assertEqual(self.leftovers(household=False), [])
                    self.helper().handle_payload({"action": "normal-access"})
                self.assert_normal(f"after {stop} with a full block list")

    def test_a_stalled_tc_during_panic_still_releases_within_the_budget(self) -> None:
        payload = {"action": "apply", "plan": plan_to_payload(heavy_plan())}
        for name, stalls in (
            ("the uplink root removal", lambda command: command.arguments[:4] == (
                "qdisc", "del", "dev", TOPOLOGY.uplink_interface)),
            ("the upload intake removal", lambda command: command.arguments[:2] == (
                "qdisc", "del") and "ingress" in command.arguments),
            ("every tc command", lambda command: command.executable == "tc"),
        ):
            with self.subTest(stall=name):
                self.fresh_state()
                service = self.helper()
                self.assertTrue(service.handle_payload(payload)["ok"])
                self.shim.arm(hang=stalls)
                started = time.monotonic()
                answer = service.handle_payload({"action": "normal-access"})
                took = time.monotonic() - started
                self.shim.arm()
                # Recorded, not asserted: a queue that is still there still impairs.
                probe = self.traffic_problems(((CLIENT, DEVICE_A), (OTHER_CLIENT, OTHER_ADDRESS)))
                print(f"MATRIX|heavy|stall: {name}|panic|-|-|applied|"
                      f"{'ok' if answer['ok'] else answer['error']} after {took:.1f}s|"
                      f"{'; '.join(self.leftovers() + probe) or '-'}|-", flush=True)
                self.assertLess(took, PANIC_ANSWER_SECONDS)
                # A queue that could not be removed is not a confirmed recovery.
                self.assertFalse(answer["ok"])
                # Everything that did not stall is gone: the household rules, the
                # experiment's nftables tables and, for one stalled removal, the
                # other queues.
                left = self.leftovers()
                self.assertFalse([item for item in left if "household" in item or "nft" in item],
                                 left)
                if name == "the uplink root removal":
                    self.assertEqual([item for item in left if not item.startswith("eth0 ")], [],
                                     left)
                # The stall ends and Panic is pressed again.
                self.assertTrue(service.handle_payload({"action": "normal-access"})["ok"])
                self.assert_normal(f"after a stalled {name} and a second Panic")

    def test_a_restart_after_an_interrupted_apply_removes_what_nothing_owns(self) -> None:
        """Defect D1 (docs/panic-crash-matrix-2026-10-18.md), fixed in helper.py.

        The helper dies during an apply after the experiment table and most
        queues are in place. systemd restarts it. The restarted helper has no
        published plan (active-plan.json is written only after a complete
        apply). The apply-in-progress mark makes the start remove what the
        apply left (release_interrupted_apply_at_start, called by run_helper).
        """

        from jittersim.networking import helper as helper_module

        payload = {"action": "apply", "plan": plan_to_payload(heavy_plan())}
        steps = self.count_steps(payload)
        self.fresh_state()
        # Before the last queue command: the tables and nearly every queue are in.
        outcome, _ = self.apply_until(payload, self.helper(), crash_at=len(steps) - 1)
        self.assertEqual(outcome, "crashed")
        restarted = self.helper(restart=True)
        self.assertTrue(helper_module.release_interrupted_apply_at_start(restarted))
        self.assertIsNone(restarted._current_plan())
        # The saved household rules come back at start by design; only the
        # experiment must be gone.
        self.assertEqual(self.leftovers(household=False), [],
                         "the restarted helper keeps an unowned plan")
        self.assertEqual(self.traffic_problems(((CLIENT, DEVICE_A),)), [])

    def test_without_the_ifb_module(self) -> None:
        payload = {"action": "apply", "plan": plan_to_payload(heavy_plan())}
        self.fresh_state()
        service = self.helper()
        self.shim.arm(ifb_absent=True)
        answer = service.handle_payload(payload)
        self.assertTrue(answer["ok"])
        self.assertFalse(answer["upload_corruption_applied"])
        uplink = _in(PI, "tc", "qdisc", "show", "dev", TOPOLOGY.uplink_interface).stdout
        self.assertIn("netem", uplink)
        self.assertNotRegex(uplink, r"(corrupt|duplicate|reorder) [0-9.]*[1-9]")
        self.assertTrue(service.handle_payload({"action": "normal-access"})["ok"])
        self.shim.arm()
        self.assert_normal("after Panic without ifb")


if __name__ == "__main__":
    unittest.main()

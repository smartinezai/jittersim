"""Property tests of the plan compiler and the command renderer (QA, 2026-10-09).

Random but valid profiles and devices go through compile_plan and the renderer. Whatever the
input the plan must be the same every time, stay small, carry nothing from the user into a
command that a shell or the nft parser could read as syntax, and be fully removed by a reset
without touching a table or queue that belongs to something else.
"""

import json
import re
from dataclasses import replace
from ipaddress import IPv4Address

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.codec import plan_from_payload, plan_to_payload
from jittersim.engine.compiler import MAX_DEVICES_PER_PLAN, compile_plan
from jittersim.engine.plan import WebsiteFilter
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.commands import Command, CommandResult
from jittersim.networking.renderer import (
    DOWNLOAD_TABLE,
    PROJECT_TABLE,
    UPLOAD_DEVICE,
    corrupting_upload_queues,
    render_command_plan,
    render_reset_commands,
)
from jittersim.networking.topology import Topology

TOPOLOGY = Topology("eth0", "wlan0")
PROFILE = settings(
    max_examples=120, deadline=None, database=None, derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

identifiers = st.from_regex(r"[a-z0-9][a-z0-9_-]{0,30}", fullmatch=True)
# Free text that a person can type into a name field: quotes, braces, semicolons, newlines,
# shell and nft syntax. Names are only labels and must never reach a command.
hostile_names = st.one_of(
    st.sampled_from([
        '"; flush ruleset; #', "}\nflush ruleset\n{", "$(reboot)", "`reboot`", "a\nb", "x' ; rm -rf / ;'",
        "‮gnp.exe", "\x00", "../../etc/passwd", "{{7*7}}", "a" * 100,
    ]),
    st.text(min_size=1, max_size=100).filter(lambda value: value.strip()),
)


@st.composite
def impairments(draw):
    return Impairment(
        delay_ms=draw(st.integers(0, 60_000)),
        jitter_ms=draw(st.integers(0, 60_000)),
        loss_percent=draw(st.integers(0, 100)),
        bandwidth_kbit=draw(st.one_of(st.none(), st.integers(1, 1_000_000))),
        reorder_percent=draw(st.integers(0, 100)),
        duplicate_percent=draw(st.integers(0, 100)),
        corrupt_percent=draw(st.integers(0, 100)),
        loss_burst_packets=draw(st.integers(0, 100)),
    )


@st.composite
def scenarios(draw):
    """Up to MAX_DEVICES devices on distinct private addresses sharing a few profiles."""

    profile_ids = draw(st.lists(identifiers, min_size=1, max_size=4, unique=True))
    profiles = {
        profile_id: Profile(profile_id, draw(hostile_names), draw(impairments()), draw(impairments()))
        for profile_id in profile_ids
    }
    count = draw(st.integers(0, MAX_DEVICES_PER_PLAN))
    device_ids = draw(st.lists(identifiers, min_size=count, max_size=count, unique=True))
    octets = draw(st.lists(st.integers(2, 250), min_size=count, max_size=count, unique=True))
    network = draw(st.sampled_from(["192.168.50", "10.20.30", "172.16.5"]))
    devices = [
        Device(device_id, draw(hostile_names), f"{network}.{octet}", draw(st.sampled_from(profile_ids)))
        for device_id, octet in zip(device_ids, octets)
    ]
    return devices, profiles


def compiled(devices, profiles, **extra):
    return compile_plan(devices=devices, profiles=profiles, **extra)


TC_ARGUMENT = re.compile(r"[A-Za-z0-9:._%/+-]+")
NFT_COMMENT = re.compile(r'comment "([^"]*)"')
SAFE_COMMENT = re.compile(r"[a-z0-9 :_-]+")


def script_of(commands):
    return next(command.stdin for command in commands if command.executable == "nft")


@PROFILE
@given(scenarios())
def test_the_same_input_gives_the_same_plan_in_any_order(scenario):
    devices, profiles = scenario
    first = compiled(devices, profiles)
    second = compiled(list(reversed(devices)), dict(reversed(list(profiles.items()))))

    assert first == second
    assert render_command_plan(plan=first, topology=TOPOLOGY) == render_command_plan(
        plan=second, topology=TOPOLOGY
    )


@PROFILE
@given(scenarios())
def test_compiled_plans_round_trip_through_the_wire_codec(scenario):
    devices, profiles = scenario
    plan = compiled(devices, profiles)

    assert plan_from_payload(plan_to_payload(plan)) == plan


@PROFILE
@given(scenarios())
def test_the_plan_stays_small_and_carries_no_names(scenario):
    devices, profiles = scenario
    commands = render_command_plan(plan=compiled(devices, profiles), topology=TOPOLOGY)
    script = script_of(commands)

    # Two directions, a handful of commands per device and direction, plus fixed rules.
    assert len(commands) <= 1 + 2 * (2 + 6 * len(devices))
    assert len(script.encode("utf-8")) <= 16_000 + 3_000 * len(devices)
    assert re.fullmatch(r"[\x20-\x7e\n]*", script)
    def leaked(name):
        # A name is a label. A hostile one would end the nft statement or the quoted comment.
        # Names of one or two characters (a lone `"`) occur in any script by chance, so only
        # longer names can be told apart from the script's own text.
        return len(name) > 2 and name in script and not SAFE_COMMENT.fullmatch(name)

    for device in devices:
        assert not leaked(device.name)
    for profile in profiles.values():
        assert not leaked(profile.name)
    for comment in NFT_COMMENT.findall(script):
        assert SAFE_COMMENT.fullmatch(comment), comment


@PROFILE
@given(scenarios())
def test_no_command_argument_can_be_read_as_shell_or_option_syntax(scenario):
    devices, profiles = scenario
    plan = compiled(devices, profiles)
    for command in tuple(render_command_plan(plan=plan, topology=TOPOLOGY)) + tuple(
        render_reset_commands(topology=TOPOLOGY)
    ):
        assert re.fullmatch(r"[a-z]+", command.executable)
        if command.executable == "nft" and command.arguments == ("-f", "-"):
            continue
        for argument in command.arguments:
            assert TC_ARGUMENT.fullmatch(argument), (command.executable, argument)
            assert not argument.startswith("-") or argument in ("-f", "-"), argument


@PROFILE
@given(scenarios(), st.lists(st.integers(2, 250), max_size=6, unique=True))
def test_destination_filters_render_only_addresses(scenario, octets):
    devices, profiles = scenario
    if not devices:
        return
    filters = [
        WebsiteFilter(devices[0].ipv4_address, tuple(IPv4Address(f"93.184.216.{octet}") for octet in octets),
                      blocked_addresses=(IPv4Address("203.0.113.7"),))
    ]
    script = script_of(render_command_plan(
        plan=compiled(devices, profiles, website_filters=filters), topology=TOPOLOGY))
    for octet in octets:
        assert f"93.184.216.{octet}" in script
    assert re.fullmatch(r"[\x20-\x7e\n]*", script)


class KernelModel:
    """A tiny stateful stand-in for nft and tc that also holds state of other tools."""

    FOREIGN_TABLES = {("inet", "filter"), ("ip", "nat"), ("netdev", "other_egress")}

    def __init__(self):
        self.tables = set(self.FOREIGN_TABLES)
        self.roots = {}          # interface -> (handle, kind)
        self.children = {}       # interface -> set of (parent, handle)
        self.filters = {}        # interface -> set of (pref, handle)
        self.foreign_roots = {"br0": ("0:", "mq"), "eth1": ("8001:", "htb")}
        # Upload corruption adds an ifb device and an ingress queue on the test interface.
        self.links = set()
        self.ingress = set()
        self.log = []

    def project_state(self):
        return (
            {table for table in self.tables if table not in self.FOREIGN_TABLES},
            {key: value for key, value in self.roots.items()},
            {key: set(value) for key, value in self.children.items() if value},
            set(self.links) | {("ingress", interface) for interface in self.ingress},
        )

    def run(self, command: Command) -> CommandResult:
        self.log.append(command)
        arguments = command.arguments
        if command.executable == "tc":
            return self._tc(arguments)
        if command.executable == "nft":
            return self._nft(command)
        if command.executable == "ip":
            return self._ip(arguments)
        raise AssertionError(f"the kernel model has no answer for {command.argv}")

    def _ip(self, arguments):
        name = arguments[3] if arguments[:2] in (("link", "add"), ("link", "set")) else arguments[-1]
        if arguments[:3] == ("-j", "link", "show"):
            if name in self.links:
                return CommandResult(0, stdout=json.dumps([{"ifname": name}]))
            return CommandResult(1, stderr=f'Device "{name}" does not exist.')
        if arguments[:2] == ("link", "add"):
            assert arguments[-2:] == ("type", "ifb")
            if name in self.links:
                return CommandResult(2, stderr="RTNETLINK answers: File exists")
            self.links.add(name)
            return CommandResult(0)
        if arguments[:2] == ("link", "set"):
            return CommandResult(0 if name in self.links else 1)
        if arguments[:2] == ("link", "del"):
            assert name in self.links, "removed a device JitterSim did not create"
            self.links.discard(name)
            self.roots.pop(name, None)
            self.children.pop(name, None)
            self.filters.pop(name, None)
            return CommandResult(0)
        raise AssertionError(f"the kernel model has no answer for ip {arguments}")

    def _tc(self, arguments):
        if arguments[:3] == ("-j", "qdisc", "show") and arguments[-1] == "ingress":
            interface = arguments[arguments.index("dev") + 1]
            present = interface in self.ingress
            return CommandResult(0, stdout=json.dumps(
                [{"kind": "ingress", "handle": "ffff:"}] if present else []))
        if arguments[-1] == "ingress" and arguments[0] == "qdisc":
            interface = arguments[arguments.index("dev") + 1]
            if arguments[1] == "add":
                if interface in self.ingress:
                    return CommandResult(2, stderr="Error: Exclusivity flag on, cannot modify.")
                self.ingress.add(interface)
                return CommandResult(0)
            if arguments[1] == "del":
                if interface not in self.ingress:
                    return CommandResult(2, stderr="Error: Cannot find specified qdisc on specified device.")
                self.ingress.discard(interface)
                return CommandResult(0)
        if arguments[0] == "filter" and "ffff:" in arguments:
            interface = arguments[arguments.index("dev") + 1]
            assert interface in self.ingress, "a filter on a missing intake"
            return CommandResult(0)
        if arguments[:2] == ("qdisc", "show"):
            return CommandResult(0, stdout="")
        if arguments[:3] == ("-j", "qdisc", "show"):
            interface = arguments[arguments.index("dev") + 1]
            if interface in self.roots:
                handle, kind = self.roots[interface]
            else:
                handle, kind = self.foreign_roots.get(interface, ("0:", "fq_codel"))
            return CommandResult(0, stdout=json.dumps([{"handle": handle, "kind": kind}]))
        interface = arguments[arguments.index("dev") + 1]
        if arguments[0] == "qdisc" and arguments[1] in ("replace", "add") and "root" in arguments:
            assert interface not in self.foreign_roots, "touched a queue of another tool"
            self.roots[interface] = (arguments[arguments.index("handle") + 1], arguments[arguments.index("handle") + 2])
            self.children[interface] = set()
            self.filters[interface] = set()
            return CommandResult(0)
        if arguments[0] == "qdisc" and arguments[1] == "del" and "root" in arguments:
            assert interface not in self.foreign_roots, "removed a queue of another tool"
            if interface not in self.roots:
                return CommandResult(2, stderr="Error: Invalid handle.")
            del self.roots[interface]
            self.children.pop(interface, None)
            self.filters.pop(interface, None)
            return CommandResult(0)
        if interface not in self.roots:
            return CommandResult(2, stderr="Error: Cannot find specified qdisc on specified device.")
        parent = arguments[arguments.index("parent") + 1] if "parent" in arguments else None
        if arguments[0] == "qdisc" and arguments[1] in ("replace", "add", "change"):
            self.children[interface].add((parent, arguments[arguments.index("handle") + 1]))
            return CommandResult(0)
        if arguments[0] == "qdisc" and arguments[1] == "del":
            key = (parent, arguments[arguments.index("handle") + 1])
            if key not in self.children[interface]:
                return CommandResult(2, stderr="Error: Invalid handle.")
            self.children[interface].discard(key)
            return CommandResult(0)
        if arguments[0] == "filter":
            self.filters[interface].add((arguments[arguments.index("pref") + 1], arguments[arguments.index("handle") + 1]))
            return CommandResult(0)
        raise AssertionError(f"the kernel model has no answer for tc {arguments}")

    def _nft(self, command):
        arguments = command.arguments
        if arguments[:1] == ("delete",):
            table = (arguments[2], arguments[3])
            assert table not in self.FOREIGN_TABLES, "deleted a table of another tool"
            if table not in self.tables:
                return CommandResult(1, stderr="Error: No such file or directory")
            self.tables.discard(table)
            return CommandResult(0)
        assert arguments == ("-f", "-")
        staged = set(self.tables)
        for line in command.stdin.splitlines():
            parts = line.split()
            if not parts:
                continue
            verb, kind = parts[0], parts[1]
            if kind == "table":
                table = (parts[2], parts[3])
                assert table not in self.FOREIGN_TABLES, "wrote to a table of another tool"
                if verb == "add":
                    staged.add(table)
                elif verb == "delete":
                    staged.discard(table)
                else:
                    raise AssertionError(line)
            else:
                table = (parts[2], parts[3])
                assert table not in self.FOREIGN_TABLES, "wrote to a table of another tool"
                assert table in staged, f"{line!r} uses a table that does not exist"
        self.tables = staged
        return CommandResult(0)


@PROFILE
@given(st.lists(scenarios(), min_size=1, max_size=3))
def test_reset_removes_exactly_what_apply_created(sequence):
    kernel = KernelModel()
    foreign_before = (set(kernel.FOREIGN_TABLES), dict(kernel.foreign_roots))
    applier = NetworkApplier(executor=kernel, topology=TOPOLOGY)
    applied_any = False
    for devices, profiles in sequence:
        plan = compiled(devices, profiles)
        applier.apply(plan=plan)
        applied_any = True
        tables, roots, _, redirect = kernel.project_state()
        assert tables == {("inet", PROJECT_TABLE), ("netdev", DOWNLOAD_TABLE)}
        expected_roots = {
            interface for interface, direction in (("eth0", "upload"), ("wlan0", "download"))
            if any(queue.direction.value == direction for queue in plan.queues)
        }
        # Upload corruption runs on the ifb device behind the Wi-Fi intake, and only then.
        corrupting = bool(corrupting_upload_queues(plan))
        if corrupting:
            expected_roots.add(UPLOAD_DEVICE)
        assert set(roots) == expected_roots
        assert redirect == ({UPLOAD_DEVICE, ("ingress", "wlan0")} if corrupting else set())
        # Every queue the plan names exists, and nothing is left over from an earlier plan
        # that this one does not name. (A band that lost its device must not linger.)
        for queue in plan.queues:
            interface = "eth0" if queue.direction.value == "upload" else "wlan0"
            assert any(handle.endswith(":") for _, handle in kernel.children[interface])
    assert applied_any

    applier.reset()
    tables, roots, children, redirect = kernel.project_state()
    assert tables == set() and roots == {} and children == {} and redirect == set()
    assert (kernel.tables & kernel.FOREIGN_TABLES) == kernel.FOREIGN_TABLES
    assert (set(kernel.FOREIGN_TABLES), dict(kernel.foreign_roots)) == foreign_before


def test_reset_commands_name_only_project_objects():
    commands = render_reset_commands(topology=TOPOLOGY)
    removed = []
    for command in commands:
        assert command.ignore_failure, "a reset of something that is already gone must not fail"
        if command.executable == "nft":
            removed.append(("nft",) + command.arguments[2:])
        else:
            removed.append(("tc",) + command.arguments[3:])
    assert removed == [
        ("nft", "inet", PROJECT_TABLE), ("nft", "netdev", DOWNLOAD_TABLE),
        ("tc", "eth0", "root", "handle", "10:"), ("tc", "wlan0", "root", "handle", "10:"),
    ]


@pytest.mark.parametrize("name", ['"; flush ruleset; #', "}\nflush ruleset\n{", "$(reboot)", "\x00"])
def test_a_hostile_device_or_profile_name_never_reaches_a_command(name):
    profile = Profile("slow", name, Impairment(delay_ms=100), Impairment(loss_percent=5))
    device = Device("phone", name, "192.168.50.20", "slow")
    commands = render_command_plan(plan=compiled([device], {"slow": profile}), topology=TOPOLOGY)

    for command in commands:
        assert name not in command.arguments
        assert name not in (command.stdin or "")
        assert name not in command.purpose
    assert "flush ruleset" not in script_of(commands)
    assert replace(device, name="other").ipv4_address == device.ipv4_address

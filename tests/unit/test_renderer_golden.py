"""The exact commands the renderer writes for whole plans (mutation testing, 2026-10-18).

The renderer turns a plan into nft rules and tc commands that run as root. A mutation run showed
that the older tests held the shape of the output (which rules exist) but not its text: the
table and chain names, the hook priorities, the comments and the tc words could change without
a failure. Each file under tests/fixtures/renderer holds the whole output for one plan.

The files are not generated at test time. If the renderer changes on purpose, rewrite them and
read the difference in review like any other change:

    JITTERSIM_UPDATE_GOLDEN=1 python -m pytest tests/unit/test_renderer_golden.py
"""

import os
from dataclasses import replace
from ipaddress import IPv4Address as A
from pathlib import Path

import pytest

from jittersim.config.models import (
    AccessOptions,
    Device,
    Impairment,
    MacPolicy,
    Profile,
    SiteBlock,
    WebsitePolicy,
)
from jittersim.engine.access import AccessClient, AccessPlan
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import (
    EveryoneElseRule,
    GeneralProfileRule,
    ServiceAssignmentRule,
    ServiceListRule,
    ServiceQuotaRule,
    WebsiteFilter,
)
from jittersim.networking.renderer import (
    render_command_plan,
    render_live_queue_changes,
    render_reset_commands,
    render_service_address_commands,
)
from jittersim.networking.topology import Topology

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "renderer"
TOPOLOGY = Topology("eth0", "wlan0")
UPLOAD_DEVICE = "jittersim-ifb"

HEAVY_UP = Impairment(delay_ms=100, jitter_ms=20, loss_percent=3, loss_burst_packets=4,
                      bandwidth_kbit=1500, corrupt_percent=2, reorder_percent=7,
                      duplicate_percent=5)
HEAVY_DOWN = Impairment(delay_ms=50, reorder_percent=5, duplicate_percent=4, loss_percent=1,
                        corrupt_percent=3)
BURST_SHORT = Impairment(loss_percent=80, loss_burst_packets=2)
BURST_LONG = Impairment(loss_percent=10, loss_burst_packets=5, jitter_ms=5)
FULL_LOSS = Impairment(loss_percent=100, loss_burst_packets=3)
PLAIN = Impairment(bandwidth_kbit=800)

PROFILES = {
    "heavy": Profile("heavy", "Heavy", upload=HEAVY_UP, download=HEAVY_DOWN,
                     homepage_bounce_percent=30),
    "burst": Profile("burst", "Burst", upload=BURST_SHORT, download=BURST_LONG),
    "plain": Profile("plain", "Plain", upload=Impairment(delay_ms=3, corrupt_percent=2),
                     download=PLAIN),
    "dead": Profile("dead", "Dead", upload=FULL_LOSS, download=FULL_LOSS),
}
DEVICES = (
    Device("alice", "Alice", "192.168.50.10", "heavy"),
    Device("bob", "Bob", "192.168.50.11", "burst"),
    Device("carol", "Carol", "192.168.50.12", "plain"),
    Device("dave", "Dave", "192.168.50.13", "dead"),
)
LISTS = (
    ServiceListRule("games", ("games.example.com", "play.example.net"), 0,
                    Impairment(delay_ms=220), Impairment(loss_percent=7), outage_active=True),
    ServiceListRule("video", ("video.example.com",), 0, Impairment(), Impairment(delay_ms=9),
                    homepage_bounce_percent=20),
)
ADDRESSES = {"games": (A("203.0.113.9"), A("203.0.113.3"), A("203.0.113.3"))}
PROTECTED = ("02:00:00:00:00:bb", "02:00:00:00:00:aa")


def full_plan(**changes):
    values = dict(
        devices=DEVICES, profiles=PROFILES, protected_mac_addresses=PROTECTED,
        unverified_device_ids=("bob",),
        mac_policy=MacPolicy(mode="denylist", denylist=("aa:bb:cc:dd:ee:02",
                                                        "aa:bb:cc:dd:ee:01")),
        global_blocked_addresses=(A("203.0.113.8"), A("203.0.113.7")),
        global_allowed_addresses=(A("203.0.113.100"), A("203.0.113.101")),
        website_filters=(
            WebsiteFilter(A("192.168.50.10"), (A("203.0.113.20"), A("203.0.113.21")),
                          (A("203.0.113.30"),)),
            WebsiteFilter(A("192.168.50.11"), (), (A("203.0.113.31"),)),
            WebsiteFilter(A("192.168.50.12"), None, ()),
        ),
        blocked_device_addresses=(A("192.168.50.13"),),
        service_quota_rules=(ServiceQuotaRule(
            "alice-video", "alice", A("192.168.50.10"), (A("203.0.113.10"), A("203.0.113.5")), 0,
            Impairment(delay_ms=200), Impairment(loss_percent=5), active=True),),
        service_assignment_rules=(ServiceAssignmentRule(
            "bob-news", "bob", A("192.168.50.11"), ("news.example.com", "x.example.org"), 0,
            Impairment(delay_ms=210, duplicate_percent=3), Impairment(loss_percent=6)),),
        service_list_rules=LISTS,
        everyone_else=EveryoneElseRule(
            0, Impairment(delay_ms=230, corrupt_percent=1), Impairment(loss_percent=8),
            unaffected_macs=("02:00:00:00:00:02", "02:00:00:00:00:01")),
    )
    values.update(changes)
    return compile_plan(**values)


def access_plan():
    return AccessPlan(
        options=AccessOptions(enabled=True, reject_wifi_joins=True, dns_failure_percent=7),
        mac=MacPolicy(mode="denylist", denylist=("aa:bb:cc:dd:ee:ff",)),
        websites=WebsitePolicy(mode="allow_only", allowed_domains=("example.com",),
                               blocked_domains=("bad.example.org",)),
        blocks=(SiteBlock("video", "Video", ("video.example.com",), device_id="alice"),),
        clients=(AccessClient("alice", "192.168.50.10", "02:00:00:00:50:0a", ("video",)),),
    )


def general_plan(**rule):
    general = dict(profile_id="heavy", allowed_addresses=(A("203.0.113.9"), A("203.0.113.3")),
                   outage_active=True)
    general.update(rule)
    return compile_plan(
        devices=(), profiles=PROFILES, protected_mac_addresses=PROTECTED,
        general_profile=GeneralProfileRule(**general), service_list_rules=LISTS,
        mac_policy=MacPolicy(mode="allowlist", allowlist=("aa:bb:cc:dd:ee:01",)),
        global_blocked_addresses=(A("203.0.113.8"),),
    )


def dump(commands):
    """One block per command: the argument vector, the reason, its flags and its input."""

    blocks = []
    for command in commands:
        flags = [name for name in ("ignore_failure", "timeout_is_success", "sensitive")
                 if getattr(command, name)]
        if command.timeout_seconds is not None:
            flags.append(f"timeout={command.timeout_seconds}")
        head = f"$ {' '.join(command.argv)}  # {command.purpose}"
        if flags:
            head += f"  [{', '.join(flags)}]"
        lines = [head]
        if command.stdin is not None:
            lines.extend("| " + line for line in command.stdin.splitlines())
        blocks.append("\n".join(lines))
    return "\n".join(blocks) + "\n"


def change(plan, device_ids, directions=("upload", "download"), whole=None, **fields):
    """The plan with some fields (or a whole impairment) changed in some queues."""

    def new(queue):
        if queue.device_id not in device_ids or queue.direction.value not in directions:
            return queue
        impairment = whole if whole is not None else replace(queue.impairment, **{
            name: (queue.impairment.delay_ms + value if name == "delay_ms" else value)
            for name, value in fields.items()})
        return replace(queue, impairment=impairment)

    return replace(plan, queues=tuple(new(queue) for queue in plan.queues))


def live(previous, plan, upload_device=None):
    commands = render_live_queue_changes(
        previous=previous, plan=plan, topology=TOPOLOGY, upload_device=upload_device)
    return "replacement needed\n" if commands is None else dump(commands)


def scenarios():
    full = full_plan()
    without_corruption = full_plan(
        profiles={**PROFILES, "heavy": replace(
            PROFILES["heavy"], upload=replace(HEAVY_UP, corrupt_percent=0))},
        everyone_else=None,
    )
    return {
        "individual-with-upload-device": dump(render_command_plan(
            plan=full, topology=TOPOLOGY, service_addresses=ADDRESSES,
            upload_device=UPLOAD_DEVICE)),
        "individual-without-upload-device": dump(render_command_plan(
            plan=full, topology=TOPOLOGY)),
        "individual-without-corruption": dump(render_command_plan(
            plan=without_corruption, topology=TOPOLOGY, upload_device=UPLOAD_DEVICE)),
        "individual-allowlist-and-access": dump(render_command_plan(
            plan=full_plan(
                mac_policy=MacPolicy(mode="allowlist", allowlist=("aa:bb:cc:dd:ee:01",)),
                global_allowed_addresses=(), access=access_plan(),
                website_filters=(WebsiteFilter(A("192.168.50.10"), (), ()),)),
            topology=TOPOLOGY)),
        "individual-blocked-everyone-else": dump(render_command_plan(
            plan=full_plan(
                everyone_else=EveryoneElseRule(
                    0, Impairment(delay_ms=230), Impairment(loss_percent=8), blocked=True),
                global_allowed_addresses=None, mac_policy=MacPolicy(mode="allowlist",
                                                                      allowlist=("aa:bb:cc:dd:ee:01",))),
            topology=TOPOLOGY, upload_device=UPLOAD_DEVICE)),
        "general-with-outage-and-destinations": dump(render_command_plan(
            plan=general_plan(), topology=TOPOLOGY, service_addresses=ADDRESSES,
            upload_device=UPLOAD_DEVICE)),
        "general-with-an-empty-allowlist": dump(render_command_plan(
            plan=general_plan(allowed_addresses=(), outage_active=False), topology=TOPOLOGY)),
        "general-open": dump(render_command_plan(
            plan=general_plan(allowed_addresses=None, outage_active=True),
            topology=TOPOLOGY)),
        "everyone-else-alone": dump(render_command_plan(
            plan=compile_plan(
                devices=(), profiles=PROFILES,
                everyone_else=EveryoneElseRule(0, Impairment(delay_ms=5), Impairment())),
            topology=Topology("uplink0", "ap0"))),
        "no-rules": dump(render_command_plan(
            plan=compile_plan(devices=(), profiles=PROFILES), topology=TOPOLOGY)),
        "reset": dump(render_reset_commands(topology=TOPOLOGY)),
        "service-addresses": dump(render_service_address_commands(
            plan=full,
            additions={"games": (A("203.0.113.9"), A("203.0.113.3"), A("203.0.113.3")),
                       "video": (), "unknown": (A("203.0.113.1"),)},
            removals={"games": (A("203.0.113.4"), A("203.0.113.2")), "video": (),
                      "unknown": (A("203.0.113.1"),)})),
        "live-delay-change": live(full, change(full, ("bob", "carol"), delay_ms=17)),
        "live-delay-change-with-the-upload-device": live(
            full, change(full, ("bob", "carol"), delay_ms=17), upload_device=UPLOAD_DEVICE),
        "live-corruption-change-on-the-upload-device": live(
            full, change(full, ("carol",), directions=("upload",), corrupt_percent=9),
            upload_device=UPLOAD_DEVICE),
        "live-corruption-change-without-an-upload-device": live(
            full, change(full, ("carol",), directions=("upload",), corrupt_percent=9)),
        "live-change-of-the-set-of-corrupting-queues": live(
            full, change(full, ("carol",), directions=("upload",), corrupt_percent=0),
            upload_device=UPLOAD_DEVICE),
        "live-change-to-a-duplicating-queue": live(
            full, change(full, ("bob",), duplicate_percent=2)),
        "live-change-from-a-duplicating-queue": live(
            full, change(full, ("alice",), duplicate_percent=0)),
        "live-change-to-the-default-impairment": live(
            full, change(full, ("bob",), whole=Impairment())),
        "live-change-from-the-default-impairment": live(
            change(full, ("bob",), directions=("upload",), whole=Impairment()), full),
        "live-change-of-a-rule": live(full, full_plan(blocked_device_addresses=())),
        "live-no-change": live(full, full),
    }


SCENARIOS = scenarios()


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_renderer_writes_exactly_these_commands(name):
    path = FIXTURES / f"{name}.txt"
    text = SCENARIOS[name]
    if os.environ.get("JITTERSIM_UPDATE_GOLDEN") == "1":
        FIXTURES.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == text


def test_every_fixture_belongs_to_a_scenario():
    assert sorted(path.stem for path in FIXTURES.glob("*.txt")) == sorted(SCENARIOS)

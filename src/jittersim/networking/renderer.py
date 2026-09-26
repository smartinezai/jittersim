"""Render a network plan into inspectable Linux command operations."""

from typing import List, Sequence

from jittersim.config.models import Impairment
from jittersim.engine.plan import Direction, NetworkPlan, QueueSpec

from .commands import Command
from .topology import Topology

PROJECT_TABLE = "jittersim"
ROOT_HANDLE = "10:"
# prio class minors start at 1, so band 0 is class 10:1. Band 0 keeps unmarked
# traffic (the Pi itself, ARP, DHCP and unassigned clients) on a fair queue, and
# mark N uses band N, which is class 10:N+1. tc reads minors and handles as hex.
DEFAULT_CLASS = f"{ROOT_HANDLE}1"
# Each child kind has its own handles. "tc qdisc replace" then changes a child
# of the same kind in place, keeping the packets it holds, and swaps in a new
# child when a band changes kind; tc cannot change a netem into an fq_codel.
CHILD_HANDLE_BASES = {"netem": 0x40, "fq_codel": 0x60}


def band_class(mark: int) -> str:
    """Return the prio class that carries traffic with this mark."""

    return f"{ROOT_HANDLE}{mark + 1:x}"


def _child_handle(kind: str, mark: int) -> str:
    return f"{CHILD_HANDLE_BASES[kind] + mark:x}:"


def _render_set(values: Sequence[object]) -> str:
    return "{ " + ", ".join(str(value) for value in values) + " }"


# A rate far above any real link. netem only keeps packets in order when a
# rate serializes them, so jitter alone would reorder packets.
ORDERING_RATE = "10gbit"


def _loss_arguments(loss_percent: int, burst_packets: int) -> List[str]:
    if burst_packets < 2 or loss_percent >= 100:
        return ["loss", "random", f"{loss_percent}%"]
    # Gilbert model: the bad state loses every packet and the good state none.
    # Leaving the bad state with probability r gives bursts of 1/r packets, and
    # entering it with p = L * r / (1 - L) keeps the average loss at L.
    loss = loss_percent / 100
    exit_bad = 1 / burst_packets
    enter_bad = loss * exit_bad / (1 - loss)
    if enter_bad > 1:
        # Runs this short cannot lose this much. Leave the good state after
        # every packet and make the runs longer so the average stays L.
        enter_bad = 1.0
        exit_bad = (1 - loss) / loss
    return ["loss", "gemodel", f"{enter_bad * 100:.4f}%", f"{exit_bad * 100:.4f}%"]


def _format_impairment_arguments(queue: QueueSpec) -> List[str]:
    impairment = queue.impairment
    arguments = ["netem"]
    if impairment.delay_ms or impairment.jitter_ms:
        arguments.extend(["delay", f"{impairment.delay_ms}ms"])
        if impairment.jitter_ms:
            arguments.append(f"{impairment.jitter_ms}ms")
    if impairment.loss_percent:
        arguments.extend(_loss_arguments(impairment.loss_percent, impairment.loss_burst_packets))
    # netem reorders by sending some packets without the configured delay, so tc
    # rejects reordering without one. Randomness can also switch the delay off.
    if impairment.reorder_percent and impairment.delay_ms:
        arguments.extend(["reorder", f"{impairment.reorder_percent}%"])
    if impairment.duplicate_percent:
        arguments.extend(["duplicate", f"{impairment.duplicate_percent}%"])
    # "tc qdisc replace" changes an existing netem in place and keeps any
    # corrupt or rate setting it is not given, so always state both.
    arguments.extend(["corrupt", f"{impairment.corrupt_percent}%"])
    if impairment.bandwidth_kbit is not None:
        rate = f"{impairment.bandwidth_kbit}kbit"
    elif impairment.jitter_ms:
        rate = ORDERING_RATE
    else:
        rate = "0bit"
    arguments.extend(["rate", rate])
    return arguments


# The conntrack mark bit JitterSim owns for connections from unaffected clients.
# In the forward hook, download packets don't carry the client's MAC address
# yet, so the connection's upload packets set this bit and its download packets
# check it. Other bits of the mark are left alone.
UNAFFECTED_CONNECTION_BIT = 0x01000000


def _render_everyone_else_rules(plan: NetworkPlan, topology: Topology) -> List[str]:
    rule = plan.everyone_else
    test_interface = topology.test_interface
    unaffected = _render_set(rule.unaffected_macs)
    saved = _render_set([mark_rule.ipv4_address for mark_rule in plan.mark_rules])
    bit = f"{UNAFFECTED_CONNECTION_BIT:#010x}"
    other_bits = f"{0xFFFFFFFF ^ UNAFFECTED_CONNECTION_BIT:#010x}"
    upload = f"iifname \"{test_interface}\" meta nfproto ipv4"
    download = f"oifname \"{test_interface}\" meta nfproto ipv4"
    lines = []
    if rule.unaffected_macs:
        upload += f" ether saddr != {unaffected}"
        download += f" ct mark and {bit} != {bit}"
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward iifname \"{test_interface}\" "
            f"ether saddr {unaffected} ct mark set ct mark or {bit} "
            "comment \"mark connections of unaffected clients\""
        )
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward iifname \"{test_interface}\" "
            f"ether saddr != {unaffected} ct mark and {bit} == {bit} "
            f"ct mark set ct mark and {other_bits} "
            "comment \"clear connections of clients no longer unaffected\""
        )
    if plan.mark_rules:
        # Saved devices keep their own rules, counters and limits.
        upload += f" ip saddr != {saved}"
        download += f" ip daddr != {saved}"
    if rule.blocked:
        # A client asking for an address sends from 0.0.0.0 and may be a saved
        # device, so DHCP still reaches the Pi.
        lines.append(
            f"add rule inet {PROJECT_TABLE} input {upload} ip saddr != 0.0.0.0 drop "
            "comment \"temporarily disconnect everyone else\""
        )
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward {upload} drop "
            "comment \"temporarily disconnect everyone else\""
        )
    lines.append(
        f"add rule inet {PROJECT_TABLE} forward {upload} meta mark set {rule.mark} counter "
        "comment \"everyone else upload\""
    )
    lines.append(
        f"add rule inet {PROJECT_TABLE} forward {download} meta mark set {rule.mark} counter "
        "comment \"everyone else download\""
    )
    return lines


def _render_nft_script(plan: NetworkPlan, topology: Topology) -> str:
    test_interface = topology.test_interface
    lines = [
        # nft applies the script as one transaction: declaring, deleting and
        # rebuilding the table replaces it without a moment with no rules.
        f"add table inet {PROJECT_TABLE}",
        f"delete table inet {PROJECT_TABLE}",
        f"add table inet {PROJECT_TABLE}",
        (
            f"add chain inet {PROJECT_TABLE} forward "
            "{ type filter hook forward priority -150; policy accept; }"
        ),
        (
            f"add chain inet {PROJECT_TABLE} input "
            "{ type filter hook input priority -150; policy accept; }"
        ),
        (
            f"add chain inet {PROJECT_TABLE} output "
            "{ type filter hook output priority -150; policy accept; }"
        ),
    ]
    if plan.access is None and plan.mac_policy.mode == "denylist" and plan.mac_policy.denylist:
        lines.append(
            f"add rule inet {PROJECT_TABLE} input iifname \"{test_interface}\" "
            f"ether saddr {_render_set(plan.mac_policy.denylist)} drop "
            "comment \"deny listed test client\""
        )
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward iifname \"{test_interface}\" "
            f"ether saddr {_render_set(plan.mac_policy.denylist)} drop "
            "comment \"deny listed test client\""
        )
    if plan.access is None and plan.mac_policy.mode == "allowlist":
        lines.append(
            f"add rule inet {PROJECT_TABLE} input iifname \"{test_interface}\" "
            f"ether saddr != {_render_set(plan.mac_policy.allowlist)} drop "
            "comment \"allow only listed test clients\""
        )
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward iifname \"{test_interface}\" "
            f"ether saddr != {_render_set(plan.mac_policy.allowlist)} drop "
            "comment \"allow only listed test clients\""
        )
    if plan.global_blocked_addresses:
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward iifname \"{test_interface}\" "
            f"ip daddr {_render_set(plan.global_blocked_addresses)} drop "
            "comment \"block global website destinations\""
        )
    for website_filter in plan.website_filters:
        if website_filter.allowed_addresses:
            allowed = _render_set(website_filter.allowed_addresses)
            lines.append(
                f"add rule inet {PROJECT_TABLE} forward iifname \"{test_interface}\" "
                f"ip saddr {website_filter.device_ip} ip daddr != {allowed} drop "
                "comment \"enforce profile website allowlist\""
            )
        elif website_filter.allowed_addresses is not None:
            lines.append(
                f"add rule inet {PROJECT_TABLE} forward iifname \"{test_interface}\" "
                f"ip saddr {website_filter.device_ip} drop "
                "comment \"profile website allowlist has no resolved addresses\""
            )
        if website_filter.blocked_addresses:
            lines.append(
                f"add rule inet {PROJECT_TABLE} forward iifname \"{test_interface}\" "
                f"ip saddr {website_filter.device_ip} "
                f"ip daddr {_render_set(website_filter.blocked_addresses)} drop "
                "comment \"block progression website destinations\""
            )
    if plan.blocked_device_addresses:
        lines.append(
            f"add rule inet {PROJECT_TABLE} input iifname \"{test_interface}\" "
            f"ip saddr {_render_set(plan.blocked_device_addresses)} drop "
            "comment \"temporarily disconnect randomized clients\""
        )
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward iifname \"{test_interface}\" "
            f"ip saddr {_render_set(plan.blocked_device_addresses)} drop "
            "comment \"temporarily disconnect randomized clients\""
        )
    if plan.everyone_else is not None:
        lines.extend(_render_everyone_else_rules(plan, topology))
    for rule in plan.mark_rules:
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward "
            f"ip saddr {rule.ipv4_address} meta mark set {rule.mark} counter "
            f"comment \"usage-device {rule.device_id} upload\""
        )
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward "
            f"ip daddr {rule.ipv4_address} meta mark set {rule.mark} counter "
            f"comment \"usage-device {rule.device_id} download\""
        )
    for mark, device_id in (
        [(rule.mark, rule.device_id) for rule in plan.mark_rules]
        + [(rule.mark, rule.device_id) for rule in plan.service_assignment_rules]
    ):
        lines.append(
            f"add rule inet {PROJECT_TABLE} output "
            f"oifname \"{topology.uplink_interface}\" meta mark {mark} counter "
            f"comment \"usage-device {device_id} upload\""
        )
        lines.append(
            f"add rule inet {PROJECT_TABLE} output "
            f"oifname \"{test_interface}\" meta mark {mark} counter "
            f"comment \"usage-device {device_id} download\""
        )
    for quota_rule in plan.service_quota_rules:
        destinations = _render_set(quota_rule.destination_addresses)
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward "
            f"ip saddr {quota_rule.device_ip} ip daddr {destinations} counter "
            f"comment \"usage-service {quota_rule.quota_id} upload\""
        )
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward "
            f"ip daddr {quota_rule.device_ip} ip saddr {destinations} counter "
            f"comment \"usage-service {quota_rule.quota_id} download\""
        )
        if quota_rule.active:
            lines.append(
                f"add rule inet {PROJECT_TABLE} forward "
                f"ip saddr {quota_rule.device_ip} ip daddr {destinations} "
                f"meta mark set {quota_rule.mark} "
                f"comment \"apply-service-quota {quota_rule.quota_id} upload\""
            )
            lines.append(
                f"add rule inet {PROJECT_TABLE} forward "
                f"ip daddr {quota_rule.device_ip} ip saddr {destinations} "
                f"meta mark set {quota_rule.mark} "
                f"comment \"apply-service-quota {quota_rule.quota_id} download\""
            )
    return "\n".join(lines) + "\n"


def _render_tc_commands(*, queues: Sequence[QueueSpec], interface: str) -> List[Command]:
    commands = [
        Command(
            executable="tc",
            arguments=(
                "qdisc",
                "replace",
                "dev",
                interface,
                "root",
                "handle",
                ROOT_HANDLE,
                "prio",
                "bands",
                str(max(queue.mark for queue in queues) + 1),
                "priomap",
                *("0" for _ in range(16)),
            ),
            purpose=f"create project queue root on {interface}",
        ),
        Command(
            executable="tc",
            arguments=(
                "qdisc",
                "replace",
                "dev",
                interface,
                "parent",
                DEFAULT_CLASS,
                "handle",
                "100:",
                "fq_codel",
            ),
            purpose=f"preserve fair queuing for ordinary traffic on {interface}",
        ),
    ]
    for queue in queues:
        band = band_class(queue.mark)
        if queue.impairment != Impairment():
            child = (_child_handle("netem", queue.mark), *_format_impairment_arguments(queue))
            purpose = (
                f"apply {queue.direction.value} impairment for "
                f"device {queue.device_id} on {interface}"
            )
        else:
            child = (_child_handle("fq_codel", queue.mark), "fq_codel")
            purpose = f"preserve fair queuing for device {queue.device_id} on {interface}"
        commands.append(
            Command(
                executable="tc",
                arguments=("qdisc", "replace", "dev", interface, "parent", band, "handle", *child),
                purpose=purpose,
            )
        )
        for protocol in ("ip", "ipv6"):
            commands.append(
                Command(
                    executable="tc",
                    arguments=(
                        "filter",
                        "replace",
                        "dev",
                        interface,
                        "protocol",
                        protocol,
                        "parent",
                        ROOT_HANDLE,
                        "pref",
                        str(queue.mark if protocol == "ip" else queue.mark + 100),
                        "handle",
                        str(queue.mark),
                        "fw",
                        "flowid",
                        band,
                    ),
                    purpose=f"route {protocol} mark {queue.mark} on {interface}",
                )
            )
    return commands


def render_command_plan(*, plan: NetworkPlan, topology: Topology) -> Sequence[Command]:
    """Render a validated plan into commands for simulation or later execution."""

    commands = [
        Command(
            executable="nft",
            arguments=("-f", "-"),
            purpose="replace the project-owned nftables rules",
            stdin=_render_nft_script(plan, topology),
        ),
    ]
    upload_queues = tuple(queue for queue in plan.queues if queue.direction == Direction.UPLOAD)
    download_queues = tuple(queue for queue in plan.queues if queue.direction == Direction.DOWNLOAD)
    # A plan without queues is applied after a reset, which removes the roots.
    if upload_queues:
        commands.extend(
            _render_tc_commands(queues=upload_queues, interface=topology.uplink_interface)
        )
    if download_queues:
        commands.extend(
            _render_tc_commands(queues=download_queues, interface=topology.test_interface)
        )
    return tuple(commands)


def render_reset_commands(*, topology: Topology) -> Sequence[Command]:
    """Render cleanup operations limited to jittersim-owned state."""

    commands = [
        Command(
            executable="nft",
            arguments=("delete", "table", "inet", PROJECT_TABLE),
            purpose="remove the project-owned nftables table",
            ignore_failure=True,
        )
    ]
    for interface in (topology.uplink_interface, topology.test_interface):
        commands.append(
            Command(
                executable="tc",
                arguments=("qdisc", "del", "dev", interface, "root", "handle", ROOT_HANDLE),
                purpose=f"remove the project-owned root queue from {interface}",
                ignore_failure=True,
            )
        )
    return tuple(commands)

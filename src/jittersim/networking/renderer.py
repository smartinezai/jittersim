"""Render a network plan into inspectable Linux command operations."""

from typing import List, Sequence

from jittersim.config.models import Impairment
from jittersim.engine.plan import Direction, NetworkPlan, QueueSpec

from .commands import Command
from .topology import Topology

PROJECT_TABLE = "jittersim"
ROOT_HANDLE = "10:"
# prio class minors start at 1, so band 0 is class 10:1. Band 0 carries unmarked
# traffic (the Pi itself, ARP, DHCP and unassigned clients) without impairment,
# and mark N uses band N, which is class 10:N+1.
DEFAULT_CLASS = f"{ROOT_HANDLE}1"


def _band_class(mark: int) -> str:
    return f"{ROOT_HANDLE}{mark + 1}"


def _render_set(values: Sequence[object]) -> str:
    return "{ " + ", ".join(str(value) for value in values) + " }"


def _format_impairment_arguments(queue: QueueSpec) -> List[str]:
    impairment = queue.impairment
    arguments = ["netem"]
    if impairment.delay_ms or impairment.jitter_ms:
        arguments.extend(["delay", f"{impairment.delay_ms}ms"])
        if impairment.jitter_ms:
            arguments.append(f"{impairment.jitter_ms}ms")
    if impairment.loss_percent:
        arguments.extend(["loss", "random", f"{impairment.loss_percent}%"])
    if impairment.reorder_percent:
        arguments.extend(["reorder", f"{impairment.reorder_percent}%"])
    if impairment.duplicate_percent:
        arguments.extend(["duplicate", f"{impairment.duplicate_percent}%"])
    if impairment.corrupt_percent:
        arguments.extend(["corrupt", f"{impairment.corrupt_percent}%"])
    if impairment.bandwidth_kbit is not None:
        arguments.extend(["rate", f"{impairment.bandwidth_kbit}kbit"])
    return arguments


def _render_nft_script(plan: NetworkPlan) -> str:
    lines = [
        f"add table inet {PROJECT_TABLE}",
        (
            f"add chain inet {PROJECT_TABLE} forward "
            "{ type filter hook forward priority -150; policy accept; }"
        ),
        (
            f"add rule inet {PROJECT_TABLE} forward iifname \"wlan0\" "
            "oifname \"eth0\" meta nfproto ipv6 drop "
            'comment "disable IPv6 bypass while profiles are active"'
        ),
    ]
    if plan.mac_policy.denylist:
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward iifname \"wlan0\" "
            f"ether saddr {_render_set(plan.mac_policy.denylist)} drop "
            "comment \"deny listed test client\""
        )
    if plan.mac_policy.mode == "allowlist":
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward iifname \"wlan0\" "
            f"ether saddr != {_render_set(plan.mac_policy.allowlist)} drop "
            "comment \"allow only listed test clients\""
        )
    if plan.global_blocked_addresses:
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward iifname \"wlan0\" "
            f"ip daddr {_render_set(plan.global_blocked_addresses)} drop "
            "comment \"block global website destinations\""
        )
    for website_filter in plan.website_filters:
        allowed = _render_set(website_filter.allowed_addresses)
        if website_filter.allowed_addresses:
            lines.append(
                f"add rule inet {PROJECT_TABLE} forward iifname \"wlan0\" "
                f"ip saddr {website_filter.device_ip} ip daddr != {allowed} drop "
                "comment \"enforce profile website allowlist\""
            )
        else:
            lines.append(
                f"add rule inet {PROJECT_TABLE} forward iifname \"wlan0\" "
                f"ip saddr {website_filter.device_ip} drop "
                "comment \"profile website allowlist has no resolved addresses\""
            )
        if website_filter.blocked_addresses:
            lines.append(
                f"add rule inet {PROJECT_TABLE} forward iifname \"wlan0\" "
                f"ip saddr {website_filter.device_ip} "
                f"ip daddr {_render_set(website_filter.blocked_addresses)} drop "
                "comment \"block progression website destinations\""
            )
    if plan.blocked_device_addresses:
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward iifname \"wlan0\" "
            f"ip saddr {_render_set(plan.blocked_device_addresses)} drop "
            "comment \"temporarily disconnect randomized clients\""
        )
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


def _render_tc_commands(
    *,
    queues: Sequence[QueueSpec],
    interface: str,
    device_count: int,
) -> List[Command]:
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
                str(device_count + 1),
                "priomap",
                *("0" for _ in range(16)),
            ),
            purpose=f"create project queue root on {interface}",
        ),
        Command(
            executable="tc",
            arguments=("qdisc", "del", "dev", interface, "parent", DEFAULT_CLASS),
            purpose=f"keep unmarked traffic on {interface} unimpaired",
            ignore_failure=True,
        ),
    ]
    for queue in queues:
        band_class = _band_class(queue.mark)
        child_handle = f"{queue.mark + 10}:"
        if queue.impairment != Impairment():
            commands.append(
                Command(
                    executable="tc",
                    arguments=(
                        "qdisc",
                        "replace",
                        "dev",
                        interface,
                        "parent",
                        band_class,
                        "handle",
                        child_handle,
                        *_format_impairment_arguments(queue),
                    ),
                    purpose=(
                        f"apply {queue.direction.value} impairment for "
                        f"device {queue.device_id} on {interface}"
                    ),
                )
            )
        else:
            commands.append(
                Command(
                    executable="tc",
                    arguments=(
                        "qdisc",
                        "del",
                        "dev",
                        interface,
                        "parent",
                        band_class,
                        "handle",
                        child_handle,
                    ),
                    purpose=(
                        f"remove stale impairment for device {queue.device_id} "
                        f"on {interface}"
                    ),
                    ignore_failure=True,
                )
            )
        commands.append(
            Command(
                executable="tc",
                arguments=(
                    "filter",
                    "replace",
                    "dev",
                    interface,
                    "protocol",
                    "ip",
                    "parent",
                    ROOT_HANDLE,
                    "handle",
                    str(queue.mark),
                    "fw",
                    "flowid",
                    band_class,
                ),
                purpose=f"route mark {queue.mark} on {interface}",
            )
        )
    return commands


def render_command_plan(*, plan: NetworkPlan, topology: Topology) -> Sequence[Command]:
    """Render a validated plan into commands for simulation or later execution."""

    commands = [
        Command(
            executable="nft",
            arguments=("delete", "table", "inet", PROJECT_TABLE),
            purpose="remove the previous project-owned nftables table",
            ignore_failure=True,
        ),
        Command(
            executable="nft",
            arguments=("-f", "-"),
            purpose="install project-owned device mark rules",
            stdin=_render_nft_script(plan),
        ),
    ]
    upload_queues = tuple(queue for queue in plan.queues if queue.direction == Direction.UPLOAD)
    download_queues = tuple(queue for queue in plan.queues if queue.direction == Direction.DOWNLOAD)
    commands.extend(
        _render_tc_commands(
            queues=upload_queues,
            interface=topology.uplink_interface,
            device_count=max((queue.mark for queue in upload_queues), default=0),
        )
    )
    commands.extend(
        _render_tc_commands(
            queues=download_queues,
            interface=topology.test_interface,
            device_count=max((queue.mark for queue in download_queues), default=0),
        )
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

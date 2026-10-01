"""Render a network plan into inspectable Linux command operations."""

from typing import List, Sequence

from jittersim.config.models import Impairment
from jittersim.engine.plan import Direction, NetworkPlan, QueueSpec

from .commands import Command
from .legacy import LEGACY_ACCESS_CLEANUP, cleanup_command
from .topology import Topology

PROJECT_TABLE = "jittersim"
DOWNLOAD_TABLE = "jittersim_download"
ROOT_HANDLE = "10:"
DASHBOARD_REPLY_MARK = 0x80000000


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


def _render_nft_script(plan: NetworkPlan, topology: Topology) -> str:
    test_interface = topology.test_interface
    # Adding an existing table is harmless. Delete and recreate both in one batch.
    lines = [
        *LEGACY_ACCESS_CLEANUP.splitlines(),
        f"add table inet {PROJECT_TABLE}",
        f"delete table inet {PROJECT_TABLE}",
        f"add table netdev {DOWNLOAD_TABLE}",
        f"delete table netdev {DOWNLOAD_TABLE}",
        f"add table netdev {DOWNLOAD_TABLE}",
        f'add chain netdev {DOWNLOAD_TABLE} download {{ type filter hook egress device "{test_interface}" priority 0; policy accept; }}',
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
    # Only locally generated dashboard replies get this mark. A forwarded
    # server using port 8080 must still follow the client's download profile.
    lines.extend([
        f'add rule inet {PROJECT_TABLE} input iifname "{test_interface}" '
        'tcp dport 8080 return comment "keep dashboard sign-in reachable"',
        f'add rule inet {PROJECT_TABLE} output oifname "{test_interface}" '
        f'tcp sport 8080 meta mark set {DASHBOARD_REPLY_MARK}',
        f'add rule netdev {DOWNLOAD_TABLE} download meta mark {DASHBOARD_REPLY_MARK} '
        'meta mark set 0 return comment "keep local dashboard replies usable"',
    ])
    if plan.mac_policy.denylist:
        lines.append(
            f'add rule inet {PROJECT_TABLE} input iifname "{test_interface}" '
            f"ether saddr {_render_set(plan.mac_policy.denylist)} drop "
            'comment "deny listed test client"'
        )
        lines.append(
            f'add rule inet {PROJECT_TABLE} forward iifname "{test_interface}" '
            f"ether saddr {_render_set(plan.mac_policy.denylist)} drop "
            'comment "deny listed test client"'
        )
    if plan.mac_policy.mode == "allowlist":
        lines.append(
            f'add rule inet {PROJECT_TABLE} input iifname "{test_interface}" '
            f"ether saddr != {_render_set(plan.mac_policy.allowlist)} drop "
            'comment "allow only listed test clients"'
        )
        lines.append(
            f'add rule inet {PROJECT_TABLE} forward iifname "{test_interface}" '
            f"ether saddr != {_render_set(plan.mac_policy.allowlist)} drop "
            'comment "allow only listed test clients"'
        )
    if plan.global_blocked_addresses:
        lines.append(
            f'add rule inet {PROJECT_TABLE} forward iifname "{test_interface}" '
            f"ip daddr {_render_set(plan.global_blocked_addresses)} drop "
            'comment "block global website destinations"'
        )
    if plan.global_allowed_addresses is not None:
        match = (
            f"ip daddr != {_render_set(plan.global_allowed_addresses)} "
            if plan.global_allowed_addresses
            else "meta nfproto ipv4 "
        )
        lines.append(
            f'add rule inet {PROJECT_TABLE} forward iifname "{test_interface}" '
            f'{match}drop comment "enforce global website allowlist"'
        )
    if plan.protected_mac_addresses:
        macs = _render_set(plan.protected_mac_addresses)
        for chain in ("input", "forward"):
            lines.append(
                f'add rule inet {PROJECT_TABLE} {chain} iifname "{test_interface}" '
                f'ether saddr {macs} meta mark set 0 return comment "unaffected by profiles"'
            )
        lines.append(
            f"add rule netdev {DOWNLOAD_TABLE} download ether daddr {macs} "
            'meta mark set 0 return comment "unaffected by profiles"'
        )
    for website_filter in plan.website_filters:
        if website_filter.allowed_addresses is not None:
            if website_filter.allowed_addresses:
                lines.append(
                    f'add rule inet {PROJECT_TABLE} forward iifname "{test_interface}" '
                    f"ip saddr {website_filter.device_ip} ip daddr != {_render_set(website_filter.allowed_addresses)} drop "
                    'comment "enforce profile website allowlist"'
                )
            else:
                lines.append(
                    f'add rule inet {PROJECT_TABLE} forward iifname "{test_interface}" '
                    f"ip saddr {website_filter.device_ip} drop "
                    'comment "profile website allowlist has no resolved addresses"'
                )
        if website_filter.blocked_addresses:
            lines.append(
                f'add rule inet {PROJECT_TABLE} forward iifname "{test_interface}" '
                f"ip saddr {website_filter.device_ip} "
                f"ip daddr {_render_set(website_filter.blocked_addresses)} drop "
                'comment "block progression website destinations"'
            )
    if plan.blocked_device_addresses:
        lines.append(
            f'add rule inet {PROJECT_TABLE} input iifname "{test_interface}" '
            f"ip saddr {_render_set(plan.blocked_device_addresses)} drop "
            'comment "temporarily disconnect randomized clients"'
        )
        lines.append(
            f'add rule inet {PROJECT_TABLE} forward iifname "{test_interface}" '
            f"ip saddr {_render_set(plan.blocked_device_addresses)} drop "
            'comment "temporarily disconnect randomized clients"'
        )
    for rule in plan.mark_rules:
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward "
            f'iifname "{test_interface}" ip saddr {rule.ipv4_address} meta mark set {rule.mark} counter '
            f'comment "usage-device {rule.device_id} upload"'
        )
        lines.append(
            f"add rule netdev {DOWNLOAD_TABLE} download ip daddr {rule.ipv4_address} counter "
            f'comment "usage-device {rule.device_id} download"'
        )
        lines.append(
            f"add rule netdev {DOWNLOAD_TABLE} download ip daddr {rule.ipv4_address} "
            f"meta mark 0 meta mark set {rule.mark}"
        )
    for mark, device_id in [(rule.mark, rule.device_id) for rule in plan.mark_rules] + [
        (rule.mark, rule.device_id) for rule in plan.service_assignment_rules
    ]:
        lines.append(
            f"add rule inet {PROJECT_TABLE} output "
            f'oifname "{topology.uplink_interface}" meta mark {mark} counter '
            f'comment "usage-device {device_id} upload"'
        )
    for quota_rule in plan.service_quota_rules:
        destinations = _render_set(quota_rule.destination_addresses)
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward "
            f'iifname "{test_interface}" ip saddr {quota_rule.device_ip} ip daddr {destinations} counter '
            f'comment "usage-service {quota_rule.quota_id} upload"'
        )
        lines.append(
            f"add rule netdev {DOWNLOAD_TABLE} download "
            f"ip daddr {quota_rule.device_ip} ip saddr {destinations} counter "
            f'comment "usage-service {quota_rule.quota_id} download"'
        )
        if quota_rule.active:
            lines.append(
                f"add rule inet {PROJECT_TABLE} forward "
                f'iifname "{test_interface}" ip saddr {quota_rule.device_ip} ip daddr {destinations} '
                f"meta mark set {quota_rule.mark} "
                f'comment "apply-service-quota {quota_rule.quota_id} upload"'
            )
            lines.append(
                f"add rule netdev {DOWNLOAD_TABLE} download "
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
            arguments=(
                "qdisc",
                "replace",
                "dev",
                interface,
                "parent",
                f"{ROOT_HANDLE}1",
                "handle",
                "100:",
                "fq_codel",
            ),
            purpose=f"preserve fair queuing for ordinary traffic on {interface}",
        ),
    ]
    for queue in queues:
        band = str(queue.mark + 1)
        child_handle = f"{queue.mark + 10}:"
        commands.append(
            Command(
                executable="tc",
                arguments=(
                    "qdisc",
                    "del",
                    "dev",
                    interface,
                    "parent",
                    f"{ROOT_HANDLE}{band}",
                    "handle",
                    child_handle,
                ),
                purpose=f"clear the previous queue for device {queue.device_id} on {interface}",
                ignore_failure=True,
            )
        )
    # Old sibling queues can prevent the kernel from accepting duplication.
    # Clear them all before installing any replacement queues.
    for queue in queues:
        band = str(queue.mark + 1)
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
                        f"{ROOT_HANDLE}{band}",
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
                        "replace",
                        "dev",
                        interface,
                        "parent",
                        f"{ROOT_HANDLE}{band}",
                        "handle",
                        child_handle,
                        "fq_codel",
                    ),
                    purpose=(
                        f"preserve fair queuing for device {queue.device_id} "
                        f"on {interface}"
                    ),
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
                        f"{ROOT_HANDLE}{band}",
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
            purpose="replace project-owned traffic rules atomically",
            stdin=_render_nft_script(plan, topology),
        ),
    ]
    upload_queues = tuple(queue for queue in plan.queues if queue.direction == Direction.UPLOAD)
    download_queues = tuple(queue for queue in plan.queues if queue.direction == Direction.DOWNLOAD)
    if upload_queues:
        commands.extend(
            _render_tc_commands(
                queues=upload_queues,
                interface=topology.uplink_interface,
                device_count=max(queue.mark for queue in upload_queues),
            )
        )
    if download_queues:
        commands.extend(
            _render_tc_commands(
                queues=download_queues,
                interface=topology.test_interface,
                device_count=max(queue.mark for queue in download_queues),
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
    commands.append(
        Command(
            executable="nft",
            arguments=("delete", "table", "netdev", DOWNLOAD_TABLE),
            purpose="remove the project-owned download table",
            ignore_failure=True,
        )
    )
    commands.append(cleanup_command())
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

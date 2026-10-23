"""Render a network plan into inspectable Linux command operations."""

from dataclasses import replace
from ipaddress import IPv4Address
from typing import List, Mapping, Optional, Sequence, Tuple

from jittersim.config.models import Impairment
from jittersim.engine.plan import (
    GENERAL_QUEUE_ID,
    Direction,
    NetworkPlan,
    QueueSpec,
    service_list_set_name,
)

from .commands import Command
from .topology import Topology

PROJECT_TABLE = "jittersim"
DOWNLOAD_TABLE = "jittersim_download"
ROOT_HANDLE = "10:"
# prio class minors start at 1, so band 0 is class 10:1. Band 0 keeps unmarked
# traffic (the Pi itself, ARP, DHCP and unassigned clients) on a fair queue.
DEFAULT_CLASS = f"{ROOT_HANDLE}1"
DASHBOARD_REPLY_MARK = 0x80000000
# mac80211 maps DSCP 2 to best effort like unmarked traffic (its top three bits are 0).
# The dashboard also resets the socket priority to 0. Not yet checked on the Pi's own Wi-Fi.
SPEED_TEST_DSCP = 2
GENERAL_DOWNLOAD_MARK = 0x40000000
# Forwarded downloads for everyone else carry this mark until the download hook
# turns it into their queue mark. The hook sees the client MAC, so protected
# devices are left alone there.
EVERYONE_DOWNLOAD_MARK = 0x10000000
# Forwarded downloads from a listed service carry this base plus the list's mark
# until the download hook turns them back into the plain queue mark.
SERVICE_DOWNLOAD_MARK_BASE = 0x20000000
SERVICE_SET_SIZE = 2048
# Upload corruption runs on this intermediate device, fed from the test
# interface's ingress, so the Pi's own IP stack checks every damaged packet
# before it forwards it. Delay, jitter, loss and rate stay on the uplink: they
# never change or copy a frame.
UPLOAD_DEVICE = "jittersim-ifb"
INGRESS_HANDLE = "ffff:"
# Upload class marks are 1 to 15. Only these bits are kept in the connection
# mark, which the ingress hook copies back before the forward hook runs.
UPLOAD_CLASS_MASK = 0xFF


def queue_band(mark: int) -> str:
    """Return the traffic class number of a mark's queue as tc reads it.

    Band 1 carries ordinary traffic so a mark's queue sits one above it. tc reads
    a class minor number as hexadecimal while `prio bands` counts in decimal so
    the band must be written in hex or every class above 9 lands on the wrong one.
    """

    return format(mark + 1, "x")


def queue_parent(mark: int) -> str:
    """Return the class a mark's queue hangs from and its filter points at."""

    return ROOT_HANDLE + queue_band(mark)


# tc cannot change a netem into an fq_codel in place, so each kind of child
# queue has its own handle range and a band that switches kind gets a new child.
# The kernel refuses a duplicating netem while any other netem exists on the
# interface (check_netem_in_tree), so duplicating queues are a kind of their own.
CHILD_HANDLE_BASES = {"netem": 0x40, "duplicate": 0x50, "fq_codel": 0x60}
# Fair queues first (they remove the netems of bands that stop shaping), then
# plain netems, then duplicating netems once no other netem is left.
KIND_ORDER = ("fq_codel", "netem", "duplicate")


def queue_kind(impairment: Impairment) -> str:
    """Return the kind of child queue an impairment needs."""

    if impairment == Impairment():
        return "fq_codel"
    if impairment.duplicate_percent:
        return "duplicate"
    return "netem"


def queue_handle(mark: int, kind: str = "netem") -> str:
    """Return the handle of a mark's child queue of the given kind.

    tc reads the digits as a hexadecimal major number and prints them back
    unchanged. Marks of 1 and above never reach the root handle `10:`.
    """

    return f"{CHILD_HANDLE_BASES[kind] + mark:x}:"


def upstream_safe_impairment(impairment: Impairment) -> Impairment:
    """Return the part of an upload impairment that may run toward the router.

    netem on the uplink works on whole Ethernet frames after the Pi built them:
    corruption can flip a bit in the router-facing MAC addresses, EtherType or
    IP header, and the network card then adds a valid checksum. Duplicated or
    reordered packets would reach the router as they are. Uploads therefore
    never duplicate or reorder, and their corruption runs before the Pi
    forwards (see `UPLOAD_DEVICE`) or not at all.
    """

    return replace(impairment, duplicate_percent=0, reorder_percent=0, corrupt_percent=0)


def withheld_upload_settings(impairment: Impairment, *, corrupt: bool) -> List[str]:
    """Name the upload settings that do not run. `corrupt`: the upload device is in use."""

    names = []
    if impairment.reorder_percent:
        names.append("reordering")
    if impairment.duplicate_percent:
        names.append("duplication")
    if impairment.corrupt_percent and not corrupt:
        names.append("corruption")
    return names


def _render_set(values: Sequence[object]) -> str:
    return "{ " + ", ".join(str(value) for value in values) + " }"


# A rate far above any real link. netem keeps jittered packets in order only when
# a rate serializes them.
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
    """Return netem settings. Uploads only get what may run toward the router."""

    impairment = queue.impairment
    if queue.direction == Direction.UPLOAD:
        impairment = upstream_safe_impairment(impairment)
    return _netem_arguments(impairment)


def _netem_arguments(impairment: Impairment) -> List[str]:
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
    # corrupt or rate setting it is not given, so always state both. A rate
    # also keeps jittered packets in order.
    arguments.extend(["corrupt", f"{impairment.corrupt_percent}%"])
    if impairment.bandwidth_kbit is not None:
        rate = f"{impairment.bandwidth_kbit}kbit"
    elif impairment.jitter_ms:
        rate = ORDERING_RATE
    else:
        rate = "0bit"
    arguments.extend(["rate", rate])
    return arguments


def _service_guard(plan: NetworkPlan) -> str:
    """Match only traffic that is not headed to a service list's addresses."""

    return "".join(
        f"ip daddr != @{service_list_set_name(rule)} " for rule in plan.service_list_rules
    )


def _render_everyone_else_rules(plan: NetworkPlan, topology: Topology, guard: str) -> List[str]:
    """Shape clients that are neither saved devices nor protected.

    Uploads are marked in the forward hook. Downloads get a marker there and
    the download hook on the test interface, which sees the client MAC, turns
    it into the queue mark after the protected return rule has had its turn.
    Service lists win over this rule like they win over device rules.
    """

    rule = plan.everyone_else
    test_interface = topology.test_interface
    saved = _render_set([mark_rule.ipv4_address for mark_rule in plan.mark_rules])
    upload = f'iifname "{test_interface}" meta nfproto ipv4'
    download = f'oifname "{test_interface}" meta nfproto ipv4'
    if plan.mark_rules:
        # Saved devices keep their own rules, counters and limits.
        upload += f" ip saddr != {saved}"
        download += f" ip daddr != {saved}"
    lines = []
    if rule.blocked:
        # A client asking for an address sends from 0.0.0.0 and may be a saved
        # device, so DHCP still reaches the Pi.
        lines.append(
            f"add rule inet {PROJECT_TABLE} input {upload} ip saddr != 0.0.0.0 drop "
            'comment "temporarily disconnect everyone else"'
        )
        lines.append(
            f"add rule inet {PROJECT_TABLE} forward {upload} {guard}drop "
            'comment "temporarily disconnect everyone else"'
        )
        lines.append(
            f"add rule netdev {DOWNLOAD_TABLE} download meta mark {EVERYONE_DOWNLOAD_MARK} drop "
            'comment "temporarily disconnect everyone else"'
        )
    lines.append(
        f"add rule inet {PROJECT_TABLE} forward {upload} {guard}meta mark set {rule.mark} counter "
        'comment "everyone else upload"'
    )
    lines.append(
        f"add rule inet {PROJECT_TABLE} forward {download} "
        f'meta mark set {EVERYONE_DOWNLOAD_MARK} comment "identify everyone else downloads"'
    )
    lines.append(
        f"add rule netdev {DOWNLOAD_TABLE} download meta mark {EVERYONE_DOWNLOAD_MARK} "
        f'meta mark set {rule.mark} counter comment "everyone else download"'
    )
    return lines


def _render_nft_script(
    plan: NetworkPlan,
    topology: Topology,
    service_addresses: Optional[Mapping[str, Sequence[IPv4Address]]] = None,
) -> str:
    test_interface = topology.test_interface
    guard = _service_guard(plan)
    # Adding an existing table is harmless. Delete and recreate both in one batch.
    # nft applies the script as one transaction: declaring, deleting and
    # rebuilding the tables replaces them without a moment with no rules.
    lines = [
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
    lines.extend((
        # The ingress hook copies the connection's upload class into the mark.
        # Classify every forwarded upload afresh so a class from an older plan
        # never sticks.
        f'add rule inet {PROJECT_TABLE} forward iifname "{test_interface}" meta mark set 0',
        (
            f"add chain inet {PROJECT_TABLE} upload_class "
            "{ type filter hook forward priority 100; policy accept; }"
        ),
        # A packet conntrack cannot track (a checksum the Pi found damaged, or
        # out of its window) would skip NAT and reach the router with a test
        # Wi-Fi source address.
        f'add rule inet {PROJECT_TABLE} upload_class iifname "{test_interface}" '
        f'oifname "{topology.uplink_interface}" ct state invalid drop '
        'comment "never forward untracked test traffic to the router"',
        f'add rule inet {PROJECT_TABLE} upload_class iifname "{test_interface}" '
        f"ct mark set meta mark and {UPLOAD_CLASS_MASK:#x}",
    ))
    known_addresses = service_addresses or {}
    for list_rule in plan.service_list_rules:
        set_name = service_list_set_name(list_rule)
        lines.append(
            f"add set inet {PROJECT_TABLE} {set_name} "
            f"{{ type ipv4_addr; size {SERVICE_SET_SIZE}; }}"
        )
        addresses = sorted(set(known_addresses.get(list_rule.list_id, ())), key=int)
        if addresses:
            lines.append(
                f"add element inet {PROJECT_TABLE} {set_name} {_render_set(addresses)}"
            )
    # Only locally generated dashboard replies get this mark. A forwarded
    # server using port 8080 must still follow the client's download profile.
    lines.extend([
        f'add rule inet {PROJECT_TABLE} input iifname "{test_interface}" '
        'tcp dport 8080 return comment "keep dashboard sign-in reachable"',
        f'add rule inet {PROJECT_TABLE} output oifname "{test_interface}" '
        f'tcp sport 8080 meta mark set {DASHBOARD_REPLY_MARK}',
        # The dashboard's speed test sends its main part with this DSCP, so that part
        # alone goes through the device's download queue like any other download.
        f'add rule inet {PROJECT_TABLE} output oifname "{test_interface}" '
        f'tcp sport 8080 ip dscp {SPEED_TEST_DSCP:#04x} meta mark set 0 '
        'comment "shape the dashboard speed test"',
        f'add rule netdev {DOWNLOAD_TABLE} download meta mark {DASHBOARD_REPLY_MARK} '
        'meta mark set 0 return comment "keep local dashboard replies usable"',
        # The browser proxy marks its socket toward the client. Keep that class
        # on the connection so the client's uploads to the proxy are shaped on
        # the Wi-Fi side, not the proxy's own connection toward the router.
        f'add rule inet {PROJECT_TABLE} output oifname "{test_interface}" '
        f"meta mark and {UPLOAD_CLASS_MASK:#x} != 0 "
        f"ct mark set meta mark and {UPLOAD_CLASS_MASK:#x}",
    ])
    # Household MAC rules belong to the persistent access table. Standalone
    # experiment plans keep their own legacy MAC policy.
    if plan.access is None and plan.mac_policy.mode == "denylist" and plan.mac_policy.denylist:
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
    if plan.access is None and plan.mac_policy.mode == "allowlist":
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
    protected_macs = set(plan.protected_mac_addresses)
    if plan.everyone_else is not None:
        protected_macs.update(plan.everyone_else.unaffected_macs)
    if protected_macs:
        macs = _render_set(sorted(protected_macs))
        for chain in ("input", "forward"):
            lines.append(
                f'add rule inet {PROJECT_TABLE} {chain} iifname "{test_interface}" '
                f'ether saddr {macs} meta mark set 0 return comment "unaffected by profiles"'
            )
        lines.append(
            f"add rule netdev {DOWNLOAD_TABLE} download ether daddr {macs} "
            'meta mark set 0 return comment "unaffected by profiles"'
        )
    if plan.general_profile is not None:
        general = plan.general_profile
        upload_match = f'iifname "{test_interface}" oifname "{topology.uplink_interface}" '
        # Devices that run their own profile beside Everyone are matched by address
        # and left out of every shared rule below, so Everyone's marks, counters and
        # queues never see them. Their own rules come with the device rules.
        exceptions = [mark_rule.ipv4_address for mark_rule in plan.mark_rules]
        skip_upload = f"ip saddr != {_render_set(exceptions)} " if exceptions else ""
        skip_download = f"ip daddr != {_render_set(exceptions)} " if exceptions else ""
        if general.allowed_addresses is not None:
            match = (
                f"ip daddr != {_render_set(general.allowed_addresses)} "
                if general.allowed_addresses else "meta nfproto ipv4 "
            )
            lines.append(
                f'add rule inet {PROJECT_TABLE} forward {upload_match}{skip_upload}{guard}{match}'
                'drop comment "general profile website allowlist"'
            )
        lines.append(
            f'add rule inet {PROJECT_TABLE} forward oifname "{test_interface}" '
            f'{skip_download}meta mark set {GENERAL_DOWNLOAD_MARK} '
            'comment "identify forwarded test downloads"'
        )
        if exceptions:
            # An address match is IPv4 only. IPv6 downloads are identified as before.
            lines.append(
                f'add rule inet {PROJECT_TABLE} forward oifname "{test_interface}" '
                f'meta nfproto ipv6 meta mark set {GENERAL_DOWNLOAD_MARK} '
                'comment "identify forwarded IPv6 test downloads"'
            )
        download_match = f"meta mark {{ 1, {GENERAL_DOWNLOAD_MARK} }} "
        if general.outage_active:
            lines.extend((
                f'add rule inet {PROJECT_TABLE} forward {upload_match}{skip_upload}{guard}'
                'drop comment "general profile outage"',
                f'add rule netdev {DOWNLOAD_TABLE} download {download_match}'
                'drop comment "general profile outage"',
            ))
            if guard or exceptions:
                # The guard and the address skip only look at IPv4 addresses, so IPv6
                # needs its own drop. A device's IPv6 address is not known to the plan.
                lines.append(
                    f'add rule inet {PROJECT_TABLE} forward {upload_match}meta nfproto ipv6 '
                    'drop comment "general profile outage over IPv6"'
                )
        lines.extend((
            f'add rule inet {PROJECT_TABLE} forward {upload_match}meta nfproto ipv4 '
            f'{skip_upload}meta mark set 1 counter comment "usage-device {GENERAL_QUEUE_ID} upload"',
            f'add rule netdev {DOWNLOAD_TABLE} download {download_match}ether type ip '
            f'meta mark set 1 counter comment "usage-device {GENERAL_QUEUE_ID} download"',
            f'add rule inet {PROJECT_TABLE} output oifname "{topology.uplink_interface}" '
            f'meta mark 1 counter comment "usage-device {GENERAL_QUEUE_ID} upload"',
        ))
    for website_filter in plan.website_filters:
        if website_filter.allowed_addresses is not None:
            if website_filter.allowed_addresses:
                lines.append(
                    f'add rule inet {PROJECT_TABLE} forward iifname "{test_interface}" '
                    f"ip saddr {website_filter.device_ip} {guard}"
                    f"ip daddr != {_render_set(website_filter.allowed_addresses)} drop "
                    'comment "enforce profile website allowlist"'
                )
            else:
                lines.append(
                    f'add rule inet {PROJECT_TABLE} forward iifname "{test_interface}" '
                    f"ip saddr {website_filter.device_ip} {guard}drop "
                    'comment "profile website allowlist has no resolved addresses"'
                )
        if website_filter.blocked_addresses:
            lines.append(
                f'add rule inet {PROJECT_TABLE} forward iifname "{test_interface}" '
                f"ip saddr {website_filter.device_ip} {guard}"
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
            f"ip saddr {_render_set(plan.blocked_device_addresses)} {guard}drop "
            'comment "temporarily disconnect randomized clients"'
        )
    if plan.everyone_else is not None:
        lines.extend(_render_everyone_else_rules(plan, topology, guard))
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
    # A service list replaces a quota's fallback profile, so leave the marker of
    # a listed download alone until the download hook has turned it into a queue.
    quota_download_guard = (
        f"meta mark and {SERVICE_DOWNLOAD_MARK_BASE} == 0 " if plan.service_list_rules else ""
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
                f"{quota_download_guard}meta mark set {quota_rule.mark} "
                f"comment \"apply-service-quota {quota_rule.quota_id} download\""
            )
    # The general profile's shared-limit chart counts all affected traffic.
    general_download_count = (
        f' counter comment "usage-device {GENERAL_QUEUE_ID} download"'
        if plan.general_profile is not None
        else ""
    )
    for list_rule in plan.service_list_rules:
        set_name = service_list_set_name(list_rule)
        download_mark = SERVICE_DOWNLOAD_MARK_BASE + list_rule.mark
        if list_rule.outage_active:
            # A random outage of the list cuts its services for every affected
            # client. Protected clients already returned above in the forward
            # and download hooks. The input hook is left alone so the Pi's DNS,
            # DHCP and dashboard keep working. The output rule catches browser
            # proxy sockets that carry the list's mark, over IPv4 and IPv6.
            lines.extend((
                f'add rule inet {PROJECT_TABLE} forward iifname "{test_interface}" '
                f'ip daddr @{set_name} drop comment "service list outage"',
                f"add rule netdev {DOWNLOAD_TABLE} download meta mark {download_mark} "
                'drop comment "service list outage"',
                f'add rule inet {PROJECT_TABLE} output oifname "{topology.uplink_interface}" '
                f'meta mark {list_rule.mark} drop comment "service list outage"',
            ))
        lines.extend((
            f'add rule inet {PROJECT_TABLE} forward iifname "{test_interface}" '
            f"ip daddr @{set_name} meta mark set {list_rule.mark} counter "
            f'comment "service-list {list_rule.list_id} upload"',
            f'add rule inet {PROJECT_TABLE} forward oifname "{test_interface}" '
            f"ip saddr @{set_name} meta mark set {download_mark} counter "
            f'comment "service-list {list_rule.list_id} download"',
        ))
        if plan.general_profile is not None and plan.mark_rules:
            # A listed download of a device beside Everyone is not Everyone's traffic.
            # It is rewritten first and without Everyone's counter. The device's own
            # download counter counts it by address.
            exceptions = _render_set([mark_rule.ipv4_address for mark_rule in plan.mark_rules])
            lines.append(
                f"add rule netdev {DOWNLOAD_TABLE} download ip daddr {exceptions} "
                f"meta mark {download_mark} meta mark set {list_rule.mark}"
            )
        lines.append(
            f"add rule netdev {DOWNLOAD_TABLE} download meta mark {download_mark} "
            f"meta mark set {list_rule.mark}{general_download_count}"
        )
    return "\n".join(lines) + "\n"


def _render_tc_commands(
    *, queues: Sequence[QueueSpec], interface: str, corruption_only: bool = False
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
    # "tc qdisc replace" changes a queue of the same kind in place, so the
    # packets it holds survive a settings change. A netem that stops or starts
    # duplicating is removed first: the kernel checks a duplicating netem against
    # every other netem on the interface, whatever its own earlier settings were.
    by_kind = {kind: [] for kind in KIND_ORDER}
    for queue in queues:
        by_kind[queue_kind(queue.impairment)].append(queue)
    for queue in by_kind["netem"]:
        commands.append(_remove_child_queue(queue, interface, "duplicate"))
    for kind in KIND_ORDER:
        for queue in by_kind[kind]:
            if kind == "duplicate":
                commands.append(_remove_child_queue(queue, interface, "netem"))
            commands.append(_child_queue_command(queue, interface, kind, corruption_only))
            commands.extend(_filter_commands(queue, interface))
    return commands


def _child_queue_command(
    queue: QueueSpec, interface: str, kind: str, corruption_only: bool = False
) -> Command:
    # An upload that only asked for withheld settings keeps a plain netem, so
    # the queue keeps the handle the helper's queue counters look for.
    if kind == "fq_codel":
        settings = ("fq_codel",)
        purpose = f"preserve fair queuing for device {queue.device_id} on {interface}"
    elif corruption_only:
        settings = tuple(_corruption_arguments(queue.impairment))
        purpose = f"apply upload corruption for device {queue.device_id} on {interface}"
    else:
        settings = tuple(_format_impairment_arguments(queue))
        purpose = (
            f"apply {queue.direction.value} impairment for "
            f"device {queue.device_id} on {interface}"
        )
    return Command(
        executable="tc",
        arguments=(
            "qdisc", "replace", "dev", interface,
            "parent", queue_parent(queue.mark),
            "handle", queue_handle(queue.mark, kind),
            *settings,
        ),
        purpose=purpose,
    )


def _remove_child_queue(queue: QueueSpec, interface: str, kind: str) -> Command:
    """Delete the child queue of another kind that a band may still hold."""

    return Command(
        executable="tc",
        arguments=(
            "qdisc", "del", "dev", interface,
            "parent", queue_parent(queue.mark),
            "handle", queue_handle(queue.mark, kind),
        ),
        purpose=(
            f"remove the {kind} queue of device {queue.device_id} on {interface} "
            "before its queue changes kind"
        ),
        ignore_failure=True,
    )


def _filter_commands(queue: QueueSpec, interface: str) -> List[Command]:
    return [
        Command(
            executable="tc",
            arguments=(
                "filter", "replace", "dev", interface,
                "protocol", protocol,
                "parent", ROOT_HANDLE,
                "pref", str(queue.mark if protocol == "ip" else queue.mark + 100),
                "handle", str(queue.mark),
                "fw", "flowid", queue_parent(queue.mark),
            ),
            purpose=f"route {protocol} mark {queue.mark} on {interface}",
        )
        for protocol in ("ip", "ipv6")
    ]


def _corruption_arguments(impairment: Impairment) -> List[str]:
    """netem settings of the upload device: only the corruption, nothing else."""

    return _netem_arguments(Impairment(corrupt_percent=impairment.corrupt_percent))


def corrupting_upload_queues(plan: NetworkPlan) -> Tuple[QueueSpec, ...]:
    """Return the upload queues whose corruption runs on the upload device."""

    return tuple(
        queue for queue in plan.queues
        if queue.direction == Direction.UPLOAD and queue.impairment.corrupt_percent
    )


def _render_upload_redirect(
    *, queues: Sequence[QueueSpec], topology: Topology, exempt_macs: Sequence[str] = ()
) -> List[Command]:
    """Send corrupting upload classes from the test interface's ingress through the upload device.

    tc ingress runs before nftables, so the forward hook's mark is not known
    yet. The forward hook keeps each connection's upload class in its
    connection mark and `connmark` copies it back here. Only packets of a
    marked class are redirected. Everything else, including traffic to the Pi
    itself, never passes the upload device. The first packet of a new
    connection has no class yet and passes unshaped.
    """

    interface = topology.test_interface
    commands = [
        Command(
            executable="tc",
            arguments=("qdisc", "add", "dev", interface, "handle", INGRESS_HANDLE, "ingress"),
            purpose=f"create the upload intake on {interface}",
        ),
    ]
    # Protected and unaffected devices leave the intake first. Their established
    # connections still carry the class of an earlier plan in the connection
    # mark, which the forward hook only clears after the packet was redirected.
    for mac in sorted(set(exempt_macs)):
        commands.append(Command(
            executable="tc",
            arguments=(
                "filter", "add", "dev", interface, "parent", INGRESS_HANDLE,
                "protocol", "all", "pref", "1", "flower", "src_mac", mac, "action", "ok",
            ),
            purpose=f"keep an unaffected device out of the upload intake on {interface}",
        ))
    commands += [
        Command(
            executable="tc",
            arguments=(
                "filter", "add", "dev", interface, "parent", INGRESS_HANDLE,
                "protocol", "all", "pref", "2", "matchall",
                "action", "connmark", "continue",
            ),
            purpose=f"restore each connection's upload class on {interface}",
        ),
    ]
    for mark in sorted({queue.mark for queue in queues}):
        for protocol in ("ip", "ipv6"):
            commands.append(Command(
                executable="tc",
                arguments=(
                    "filter", "add", "dev", interface, "parent", INGRESS_HANDLE,
                    "protocol", protocol,
                    "pref", str(mark + 10 if protocol == "ip" else mark + 110),
                    "handle", str(mark), "fw",
                    "action", "mirred", "egress", "redirect", "dev", UPLOAD_DEVICE,
                ),
                purpose=f"send {protocol} upload class {mark} through {UPLOAD_DEVICE}",
            ))
    return commands


def render_command_plan(
    *,
    plan: NetworkPlan,
    topology: Topology,
    service_addresses: Optional[Mapping[str, Sequence[IPv4Address]]] = None,
    upload_device: Optional[str] = None,
) -> Sequence[Command]:
    """Render a validated plan into commands for simulation or later execution.

    `service_addresses` maps a service list id to the destination addresses
    already learned for it, so a new table starts with them in the same batch.

    Upload delay, jitter, loss and rate run on the uplink's egress. With
    `upload_device` (an existing, up ifb device) upload corruption runs there,
    fed from the test interface's ingress, before the Pi forwards. Without it
    uploads are not corrupted. Uploads never get duplication or reordering:
    see `upstream_safe_impairment`.
    """

    commands = [
        Command(
            executable="nft",
            arguments=("-f", "-"),
            purpose="replace project-owned traffic rules atomically",
            stdin=_render_nft_script(plan, topology, service_addresses),
        ),
    ]
    upload_queues = tuple(queue for queue in plan.queues if queue.direction == Direction.UPLOAD)
    download_queues = tuple(queue for queue in plan.queues if queue.direction == Direction.DOWNLOAD)
    # A plan without queues is applied after a reset, which removes the roots.
    if upload_queues:
        commands.extend(
            _render_tc_commands(queues=upload_queues, interface=topology.uplink_interface)
        )
    corrupting = corrupting_upload_queues(plan)
    if corrupting and upload_device is not None:
        commands.extend(
            _render_tc_commands(queues=corrupting, interface=upload_device, corruption_only=True)
        )
        exempt = set(plan.protected_mac_addresses)
        if plan.everyone_else is not None:
            exempt.update(plan.everyone_else.unaffected_macs)
        commands.extend(_render_upload_redirect(queues=corrupting, topology=topology,
                                                exempt_macs=sorted(exempt)))
    if download_queues:
        commands.extend(
            _render_tc_commands(queues=download_queues, interface=topology.test_interface)
        )
    return tuple(commands)


def render_live_queue_changes(
    *, previous: NetworkPlan, plan: NetworkPlan, topology: Topology,
    upload_device: Optional[str] = None,
):
    """Return safe in-place changes or None when complete replacement is needed.

    `upload_device` is the upload device the previous plan was applied with.
    """

    def classification(value):
        return replace(
            value,
            queues=tuple(replace(queue, impairment=Impairment()) for queue in value.queues),
            service_list_rules=tuple(replace(rule, upload_impairment=Impairment(), download_impairment=Impairment()) for rule in value.service_list_rules),
            service_assignment_rules=tuple(replace(rule, upload_impairment=Impairment(), download_impairment=Impairment()) for rule in value.service_assignment_rules),
            service_quota_rules=tuple(replace(rule, upload_impairment=Impairment(), download_impairment=Impairment()) for rule in value.service_quota_rules),
        )

    if classification(previous) != classification(plan):
        return None
    if upload_device is not None and (
        {queue.mark for queue in corrupting_upload_queues(previous)}
        != {queue.mark for queue in corrupting_upload_queues(plan)}
    ):
        # The intake redirects only corrupting classes, so it must be rebuilt.
        return None
    commands = []
    for before, after in zip(previous.queues, plan.queues):
        if before.impairment == after.impairment:
            continue
        if (
            before.impairment == Impairment() or after.impairment == Impairment()
            or before.impairment.duplicate_percent or after.impairment.duplicate_percent
        ):
            return None
        interface = topology.uplink_interface if after.direction == Direction.UPLOAD else topology.test_interface
        if (
            upload_device is not None and after.direction == Direction.UPLOAD
            and after.impairment.corrupt_percent != before.impairment.corrupt_percent
        ):
            commands.append(Command(
                executable="tc",
                arguments=("qdisc", "change", "dev", upload_device, "parent",
                           queue_parent(after.mark), "handle", queue_handle(after.mark),
                           *_corruption_arguments(after.impairment)),
                purpose=f"update upload corruption for {after.device_id}",
            ))
        commands.append(Command(
            executable="tc",
            arguments=("qdisc", "change", "dev", interface, "parent", queue_parent(after.mark),
                       "handle", queue_handle(after.mark), *_format_impairment_arguments(after)),
            purpose=f"update {after.direction.value} settings for {after.device_id}",
        ))
    return tuple(commands)


def render_service_address_commands(
    *,
    plan: NetworkPlan,
    additions: Mapping[str, Sequence[IPv4Address]],
    removals: Mapping[str, Sequence[IPv4Address]],
) -> Sequence[Command]:
    """Render live changes to the address sets of the lists in an applied plan."""

    set_names = {rule.list_id: service_list_set_name(rule) for rule in plan.service_list_rules}
    commands = []
    added = [
        f"add element inet {PROJECT_TABLE} {set_names[list_id]} "
        f"{_render_set(sorted(set(addresses), key=int))}"
        for list_id, addresses in sorted(additions.items())
        if addresses and list_id in set_names
    ]
    if added:
        commands.append(
            Command(
                executable="nft",
                arguments=("-f", "-"),
                purpose="add learned service addresses",
                stdin="\n".join(added) + "\n",
            )
        )
    for list_id, addresses in sorted(removals.items()):
        if addresses and list_id in set_names:
            commands.append(
                Command(
                    executable="nft",
                    arguments=("-f", "-"),
                    purpose="remove stale service addresses",
                    stdin=(
                        f"delete element inet {PROJECT_TABLE} {set_names[list_id]} "
                        f"{_render_set(sorted(set(addresses), key=int))}\n"
                    ),
                    ignore_failure=True,
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

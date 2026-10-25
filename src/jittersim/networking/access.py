"""Persistent DNS and device restrictions owned independently of experiments."""

import argparse
import fcntl
import json
import logging
import os
import threading
import time
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Optional

from jittersim.config.models import AccessOptions
from jittersim.engine.access import (
    MAX_ACCESS_BYTES,
    AccessPlan,
    TEST_NETWORK,
    access_from_payload,
    domain_matches,
)
from jittersim.diagnostic_privacy import configure_logging
from jittersim.panic_guard import PanicGuard

from jittersim.localname import GATEWAY_ADDRESS, read_local_name, resolver_names, validate_local_name

from .commands import Command
from .deadlines import bounded_wait
from .executor import CommandExecutionError, SubprocessExecutor
from .renderer import DASHBOARD_REPLY_MARK
from .topology import Topology

LOGGER = logging.getLogger(__name__)
ACCESS_DIRECTORY = Path("/var/lib/jittersim-access")
ACCESS_RUNTIME = Path("/run/jittersim/access-policy.json")
# The filtered resolver listens here. Port 5353 belongs to mDNS: the Avahi
# publisher answers `<pi-name>.local` there and the access table must never
# close it.
DNS_PORT = 5335
# At most 256 client views plus the protected default. Each uses UDP and TCP on
# the existing resolver process; 5353 (mDNS) and 5354 (optional ads) stay separate.
DNS_VIEW_PORT_FIRST = 5400
DNS_VIEW_PORT_LAST = DNS_VIEW_PORT_FIRST + 256
# Test clients keep reaching the dashboard sign-in whatever the MAC lists say. The
# application checks the token and the saved device permission itself and a
# blocked device must still be able to learn why. The experiment table has the
# same exception further down the input hook.
DASHBOARD_PORT = 8080
# Panic's household release waits at most this long for another access change
# (an address refresh or the boot restoration) and then reports busy. Panic's
# deadline can shorten it further (docs/architecture.md, "Panic time budget").
RELEASE_LOCK_WAIT_SECONDS = 10.0
# Panic already paused ad blocking, so its release waits only briefly for the
# ad-blocking settings lock. A busy lock never keeps household rules on.
RELEASE_TRANSITION_WAIT_SECONDS = 1.0
# A release clears old DNS connection state at most this many times, and all
# attempts together get at most this long. Each conntrack call is bounded by
# its own timeout as well, so the cleanup cannot hold Panic for longer.
DNS_CLEANUP_ATTEMPTS = 2
DNS_CLEANUP_BUDGET_SECONDS = 3.0
DNS_CLEANUP_RETRY_DELAY_SECONDS = 0.2
CONNTRACK_TIMEOUT_SECONDS = 2.0


def atomic_write(path: Path, content: str, mode: int = 0o640) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def read_access(path: Path) -> AccessPlan:
    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_ACCESS_BYTES + 1)
    except FileNotFoundError:
        return AccessPlan(options=AccessOptions(enabled=False))
    if len(content) > MAX_ACCESS_BYTES:
        raise ValueError("saved access policy is too large")
    return access_from_payload(json.loads(content))


def _local_name_records(name: str) -> list:
    """The dashboard's neutral names, answered here whatever the rules block."""
    records = []
    for item in resolver_names(name):
        records += [
            f'  local-zone: "{item}." static',
            f'  local-data: "{item}. 60 IN A {GATEWAY_ADDRESS}"',
        ]
    return records


def _dns_views(plan):
    views = {plan.blocked_domains() if plan.options.enabled else (): "everyone"}
    if plan.options.enabled:
        for client in plan.clients:
            views.setdefault(plan.blocked_domains(client.id), f"group{len(views)}")
        if plan.protected_mac_addresses:
            views.setdefault(plan.blocked_domains(client_mac=plan.protected_mac_addresses[0]),
                             f"group{len(views)}")
    ports = {blocked: DNS_PORT if index == 0 else DNS_VIEW_PORT_FIRST + index - 1
             for index, blocked in enumerate(views)}
    return views, ports


def render_dns(plan: AccessPlan, local_name: Optional[str] = None) -> str:
    """Use Unbound views to check each client's rules before answering from cache."""
    local = validate_local_name(local_name) if local_name else read_local_name()
    lines = [
        "server:",
        f"  interface: 127.0.0.1@{DNS_PORT}",
        f"  interface: 192.168.50.1@{DNS_PORT}",
        "  ip-freebind: yes",
        '  chroot: ""',
        '  directory: "/"',
        '  username: "unbound"',
        '  pidfile: ""',
        "  use-syslog: no",
        '  logfile: ""',
        "  verbosity: 0",
        "  log-queries: no",
        "  log-replies: yes",
        "  log-tag-queryreply: yes",
        "  log-local-actions: yes",
        f'  log-identity: "jittersim-dns-{plan.digest()}"',
        "  do-ip6: no",
        "  do-not-query-localhost: no",
        '  module-config: "iterator"',
        "  hide-identity: yes",
        "  hide-version: yes",
        "  num-threads: 1",
        "  msg-cache-size: 4m",
        "  rrset-cache-size: 8m",
        "  infra-cache-numhosts: 256",
        "  outgoing-range: 256",
        "  num-queries-per-thread: 256",
        "  cache-max-ttl: 300",
        "  access-control: 0.0.0.0/0 refuse",
        "  access-control: 127.0.0.1/32 allow",
        "  access-control: 192.168.50.0/24 allow",
        f"  local-data: 'jittersim-policy.invalid. 0 IN TXT \"{plan.digest()}\"'",
        '  access-control-view: 192.168.50.0/24 "everyone"',
    ]
    lines += _local_name_records(local)
    views, ports = _dns_views(plan)
    if plan.options.enabled and plan.protected_mac_addresses:
        # Source ACLs override interface-view. Select every view by listener in
        # this mode; nft chooses it using the client's MAC and (for site rules) IP.
        lines = [line for line in lines if not line.startswith("  access-control")]
        lines += ["  access-control: 127.0.0.1/32 allow"]
        for blocked, name in views.items():
            listener = f"192.168.50.1@{ports[blocked]}"
            if ports[blocked] != DNS_PORT:
                lines.append(f"  interface: {listener}")
            lines += [f"  interface-action: {listener} allow",
                      f'  interface-view: {listener} "{name}"']
    elif plan.options.enabled:
        for client in plan.clients:
            name = views[plan.blocked_domains(client.id)]
            if name != "everyone":
                lines.append(f'  access-control-view: {client.address}/32 "{name}"')
    # The existing shared-network DNS service retains DHCP names and upstream
    # resolver choices. Local output does not pass through our client redirect.
    lines += ["forward-zone:", '  name: "."', "  forward-addr: 192.168.50.1@53"]
    for blocked, name in views.items():
        lines += ["view:", f'  name: "{name}"', "  view-first: no"]
        lines += _local_name_records(local)
        if plan.options.enabled and plan.websites.mode == "allow_only":
            lines.append('  local-zone: "." always_nxdomain')
            for domain in plan.websites.allowed_domains:
                if not any(domain_matches(domain, denied) for denied in blocked):
                    lines.append(f'  local-zone: "{domain}." always_transparent')
        for domain in blocked:
            lines.append(f'  local-zone: "{domain}." always_nxdomain')
        # Keep an empty view explicit without falling back to another client's tree.
        if not blocked and not (plan.options.enabled and plan.websites.mode == "allow_only"):
            lines.append('  local-zone: "." always_transparent')
    return "\n".join(lines) + "\n"


def _table(name: str, family: str = "inet") -> list:
    return [
        f"add table {family} {name}",
        f"delete table {family} {name}",
        f"add table {family} {name}",
    ]


def render_access_firewall(plan: AccessPlan, topology: Topology) -> str:
    table = "jittersim_access"
    interface = topology.test_interface
    lines = _table(table) + _table(table, "netdev")
    for name, hook, kind, priority in (
        ("input", "input", "filter", -250),
        ("forward", "forward", "filter", -250),
        ("dns", "prerouting", "nat", -110),
    ):
        lines.append(
            f"add chain inet {table} {name} "
            f"{{ type {kind} hook {hook} priority {priority}; policy accept; }}"
        )
    lines.append(_resolver_privacy_rule(topology))
    if not plan.options.enabled:
        return "\n".join(lines) + "\n"
    if plan.protected_mac_addresses:
        ports = f"{{ {DNS_PORT}, {DNS_VIEW_PORT_FIRST}-{DNS_VIEW_PORT_LAST} }}"
        lines += [
            f'add rule inet {table} input iifname "{interface}" ip saddr != {TEST_NETWORK} '
            f'meta l4proto {{ tcp, udp }} th dport {ports} drop',
            f'add rule inet {table} input iifname "{interface}" '
            f'meta l4proto {{ tcp, udp }} th dport {ports} '
            'ct original proto-dst != 53 drop',
        ]
    # Before every drop below. Replies leave through the output hook and the
    # egress chain so they carry the mark that the experiment table uses.
    lines.append(
        f'add rule inet {table} input iifname "{interface}" tcp dport {DASHBOARD_PORT} '
        'accept comment "keep dashboard sign-in reachable"'
    )
    lines.append(
        f'add rule inet {table} input iifname "{interface}" ip daddr 192.168.50.1 '
        'tcp dport 8443 accept comment "allow verified homepage policy reads"'
    )
    dns_ports = (f"{{ {DNS_PORT}, {DNS_VIEW_PORT_FIRST}-{DNS_VIEW_PORT_LAST} }}"
                 if plan.protected_mac_addresses else str(DNS_PORT))
    if plan.options.dns_failure_percent:
        # nftables' random generator returns values from 0 to 99. At 100%,
        # reject directly instead of comparing against an invalid value.
        chance = (
            f"numgen random mod 100 < {plan.options.dns_failure_percent} "
            if plan.options.dns_failure_percent < 100
            else ""
        )
        # These are prompt errors rather than dropped packets. They run only on
        # DNS redirected from test Wi-Fi, before it reaches the filtered resolver.
        lines.append(
            f'add rule inet {table} input iifname "{interface}" '
            f"ip saddr {TEST_NETWORK} udp dport {dns_ports} {chance}"
            "counter reject with icmpx type port-unreachable"
        )
        lines.append(
            f'add rule inet {table} input iifname "{interface}" '
            # Decide once per TCP connection. A later segment is part of the
            # same request and must not reset it part-way.
            f"ip saddr {TEST_NETWORK} tcp dport {dns_ports} ct state new {chance}"
            "counter reject with tcp reset"
        )
        # Optional ad filtering must preserve the household DNS failure choice.
        lines.append(
            f'add rule inet {table} input iifname "{interface}" '
            f"ip saddr {TEST_NETWORK} udp dport 5354 {chance}"
            "counter reject with icmpx type port-unreachable"
        )
        lines.append(
            f'add rule inet {table} input iifname "{interface}" '
            f"ip saddr {TEST_NETWORK} tcp dport 5354 tcp flags & (syn | ack) == syn {chance}"
            "counter reject with tcp reset"
        )
    mac = plan.mac
    addresses = mac.allowlist if mac.mode == "allowlist" else mac.denylist
    if mac.mode != "disabled" and addresses:
        condition = "!= " if mac.mode == "allowlist" else ""
        match = "{ " + ", ".join(addresses) + " }"
        for chain in ("input", "forward"):
            lines.append(
                f'add rule inet {table} {chain} iifname "{interface}" '
                f"ether saddr {condition}{match} counter drop"
            )
        # Check the destination after routing has selected its Ethernet address.
        # Otherwise an existing one-way download can continue to a denied device.
        # Priority -1 runs before the experiment's download chain that clears
        # the dashboard reply mark.
        lines.append(
            f"add chain netdev {table} outgoing "
            f'{{ type filter hook egress device "{interface}" priority -1; policy accept; }}'
        )
        # Only the Pi's own dashboard replies carry the mark. A forwarded server
        # on port 8080 stays blocked like any other traffic to the device.
        lines.append(
            f"add chain inet {table} output "
            "{ type filter hook output priority -250; policy accept; }"
        )
        lines.append(
            f'add rule inet {table} output oifname "{interface}" tcp sport {DASHBOARD_PORT} '
            f"meta mark set {DASHBOARD_REPLY_MARK}"
        )
        lines.append(
            f"add rule netdev {table} outgoing meta mark {DASHBOARD_REPLY_MARK} "
            'accept comment "keep dashboard sign-in reachable"'
        )
        # The device must still find the Pi's hardware address. Without ARP
        # replies the sign-in fails as soon as the device's cache entry expires.
        lines.append(
            f"add rule netdev {table} outgoing ether type arp "
            'accept comment "keep dashboard sign-in reachable"'
        )
        unicast = (
            "ether daddr & 01:00:00:00:00:00 == 00:00:00:00:00:00 "
            if mac.mode == "allowlist"
            else ""
        )
        lines.append(
            f"add rule netdev {table} outgoing {unicast}"
            f"ether daddr {condition}{match} counter drop"
        )
    if plan.has_dns_rules:
        for client in plan.clients:
            if not client.block_ids or not client.mac:
                continue
            for chain in ("input", "forward"):
                # DHCP must remain available while a saved client gets a new lease.
                prefix = f'add rule inet {table} {chain} iifname "{interface}" '
                lines.append(prefix + f"ether saddr {client.mac} udp sport 68 udp dport 67 accept")
                lines.append(
                    prefix + f"ether saddr {client.mac} ip saddr != {client.address} counter drop"
                )
                lines.append(
                    prefix + f"ip saddr {client.address} ether saddr != {client.mac} counter drop"
                )
        if plan.protected_mac_addresses:
            _, ports = _dns_views(plan)
            selectors = []
            for client in plan.clients:
                if client.mac in plan.protected_mac_addresses:
                    selectors.append((f"ether saddr {client.mac} ip saddr {client.address} ",
                                      ports[plan.blocked_domains(client.id)]))
            protected = "{ " + ", ".join(plan.protected_mac_addresses) + " }"
            selectors.append((f"ether saddr {protected} ", ports[plan.blocked_domains(
                client_mac=plan.protected_mac_addresses[0])]))
            for client in plan.clients:
                if client.mac not in plan.protected_mac_addresses:
                    mac_match = f"ether saddr {client.mac} " if client.mac else ""
                    selectors.append((mac_match + f"ip saddr {client.address} ",
                                      ports[plan.blocked_domains(client.id)]))
            for selector, port in selectors:
                for protocol in ("udp", "tcp"):
                    lines.append(f'add rule inet {table} dns iifname "{interface}" '
                                 f'{selector}{protocol} dport 53 redirect to :{port}')
        for protocol in ("udp", "tcp"):
            lines.append(
                f'add rule inet {table} dns iifname "{interface}" '
                f"{protocol} dport 53 redirect to :{DNS_PORT}"
            )
            lines.append(
                f'add rule inet {table} forward iifname "{interface}" '
                f"{protocol} dport {{ 53, 853 }} counter drop"
            )
        # No IPv6 route around IPv4 DNS enforcement on the test network.
        lines.append(
            f'add rule inet {table} forward iifname "{interface}" meta nfproto ipv6 counter drop'
        )
    return "\n".join(lines) + "\n"


def _resolver_privacy_rule(topology: Topology) -> str:
    # Keep this port private even while filtering is disabled. Unbound may still
    # answer on it while a release waits for old DNS mappings to clear.
    return (
        f'add rule inet jittersim_access input iifname != "{topology.test_interface}" iifname != "lo" '
        f"meta l4proto {{ tcp, udp }} th dport {{ {DNS_PORT}, {DNS_VIEW_PORT_FIRST}-{DNS_VIEW_PORT_LAST} }} drop"
    )


def _removal_firewall(topology: Topology) -> str:
    """Remove every access rule and the transition guard, but keep the resolver port private.

    Each line it adds is also in the open firewall the release tries first, so the
    fallback cannot fail on a rule of its own.
    """

    return "\n".join(
        _table("jittersim_access")
        + ["add chain inet jittersim_access input "
           "{ type filter hook input priority -250; policy accept; }",
           _resolver_privacy_rule(topology)]
        + _table("jittersim_access", "netdev") + ["delete table netdev jittersim_access"]
    ) + "\n" + transition_firewall(topology, clear=True)


def transition_firewall(topology: Topology, *, clear: bool = False) -> str:
    table = "jittersim_access_transition"
    lines = _table(table)
    if clear:
        lines.append(f"delete table inet {table}")
    else:
        interface = topology.test_interface
        for chain in ("input", "forward", "output"):
            lines += [
                f"add chain inet {table} {chain} "
                f"{{ type filter hook {chain} priority -350; policy accept; }}"
            ]
            # A guard left by a failed restore or recovery must not lock people out:
            # the device still gets an address (DHCP) and reaches the dashboard sign-in
            # where Panic opens the network again. Nothing is forwarded.
            if chain == "input":
                lines += [
                    f'add rule inet {table} input iifname "{interface}" tcp dport {DASHBOARD_PORT} accept',
                    f'add rule inet {table} input iifname "{interface}" udp dport 67 accept',
                ]
            if chain == "output":
                lines += [
                    f'add rule inet {table} output oifname "{interface}" tcp sport {DASHBOARD_PORT} accept',
                    f'add rule inet {table} output oifname "{interface}" udp sport 67 accept',
                ]
            if chain != "output":
                lines.append(
                    f'add rule inet {table} {chain} iifname "{topology.test_interface}" drop'
                )
            if chain != "input":
                lines.append(
                    f'add rule inet {table} {chain} oifname "{topology.test_interface}" drop'
                )
    return "\n".join(lines) + "\n"


class AccessManager:
    """Replace the access policy under a temporary test-network guard."""

    def __init__(
        self,
        *,
        executor,
        topology,
        directory=ACCESS_DIRECTORY,
        runtime_path=ACCESS_RUNTIME,
        hostapd_path=Path("/run/jittersim-hostapd"),
        ad_blocking_store=None,
    ):
        self.executor = executor
        self.topology = topology
        self.directory = Path(directory)
        self.runtime_path = Path(runtime_path)
        self.hostapd_path = Path(hostapd_path)
        self.ad_blocking_store = ad_blocking_store
        self.lock = threading.RLock()
        self.error = None
        self.health_error = None
        self.applied = None
        self.generation = None
        # True after a change failed in the DNS step. The helper words its answer
        # from this.
        self.dns_failed = False
        # True after a release that opened the network but could not undo every
        # restriction (for example the Wi-Fi join limits). Panic reports this as
        # incomplete recovery so the owner retries.
        self.release_incomplete = False
        # True while old DNS mappings to the resolver port may remain after a
        # release. A retried release then clears them before it stops the resolver.
        self._dns_flows_pending = False

    @contextmanager
    def _locked(self, timeout=None):
        """Hold the thread lock and the boot file lock, waiting at most `timeout` seconds."""

        deadline = None if timeout is None else time.monotonic() + timeout
        if not self.lock.acquire(timeout=-1 if timeout is None else timeout):
            raise ValueError("Household rules are busy with another change. Try again.")
        try:
            self.directory.mkdir(mode=0o750, parents=True, exist_ok=True)
            # Boot restoration and the live helper must not change policy together.
            with (self.directory / "lock").open("a") as stream:
                if deadline is None:
                    fcntl.flock(stream, fcntl.LOCK_EX)
                else:
                    while True:
                        try:
                            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic() >= deadline:
                                raise ValueError(
                                    "Household rules are busy with another change. Try again."
                                ) from None
                            time.sleep(0.05)
                try:
                    yield
                finally:
                    fcntl.flock(stream, fcntl.LOCK_UN)
        finally:
            self.lock.release()

    @contextmanager
    def transaction(self):
        with self._locked():
            yield

    def _generation(self):
        try:
            stat = (self.directory / "policy.json").stat()
            return stat.st_ino, stat.st_mtime_ns
        except FileNotFoundError:
            return None

    def _run(self, executable, *arguments, stdin=None):
        return self.executor.run(
            Command(
                executable=executable,
                arguments=arguments,
                stdin=stdin,
                timeout_seconds=(
                    8 if executable == "systemctl" else 1 if executable == "hostapd_cli" else 2
                ),
                purpose="update persistent access rules",
            )
        )

    def _nft(self, value, *, check=False):
        return self._run("nft", *(("--check", "-f", "-") if check else ("-f", "-")), stdin=value)

    def admission_supported(self):
        return (self.hostapd_path / self.topology.test_interface).is_socket()

    def _hostapd(self, *arguments):
        response = self._run(
            "hostapd_cli",
            "-p",
            str(self.hostapd_path),
            "-i",
            self.topology.test_interface,
            *arguments,
        )
        if response.stdout.strip() != "OK":
            raise ValueError("Wi-Fi admission change was rejected")

    def _admission(self, plan):
        requested = plan.options.enabled and plan.options.reject_wifi_joins
        if not self.admission_supported():
            if requested:
                raise ValueError("this access point does not support Wi-Fi admission rules")
            return
        allow = plan.mac.allowlist if requested and plan.mac.mode == "allowlist" else ()
        deny = plan.mac.denylist if requested and plan.mac.mode == "denylist" else ()
        # Replacing the lists also disconnects stations excluded by the new list.
        # Set the final mode before loading them so hostapd checks the right mode.
        self._hostapd("set", "macaddr_acl", "1" if allow else "0")
        self._hostapd("accept_acl", "CLEAR")
        self._hostapd("deny_acl", "CLEAR")
        for name, addresses in (("accept", allow), ("deny", deny)):
            path = self.directory / (name + ".macs")
            atomic_write(path, "\n".join(addresses) + "\n", 0o600)
            self._hostapd("set", name + "_mac_file", str(path))

    def _stop_dns(self, *, wait=True):
        # Nothing is redirected to the resolver any more. A failed stop must not
        # block an emergency release or an Apply that needs no DNS rules. A
        # release only queues the stop (`--no-block`): it is cleanup and must not
        # hold Panic's answer. A later restart replaces a queued stop.
        try:
            self.executor.run(
                Command(
                    executable="systemctl",
                    arguments=("stop", "jittersim-dns.service") if wait
                    else ("stop", "--no-block", "jittersim-dns.service"),
                    ignore_failure=True,
                    timeout_seconds=8,
                    purpose="stop the unused filtered resolver",
                )
            )
        except CommandExecutionError:
            LOGGER.warning("could not stop the unused filtered DNS service")

    def _install(self, plan):
        atomic_write(self.directory / "unbound.conf", render_dns(plan))
        # Only DNS rules need the resolver. MAC lists and the release path must
        # work even when it is broken or missing.
        if plan.has_dns_rules:
            try:
                self._run("systemctl", "restart", "jittersim-dns.service")
                self._run("systemctl", "is-active", "--quiet", "jittersim-dns.service")
                deadline = time.monotonic() + 5
                while not self._probe(plan):
                    if time.monotonic() >= deadline:
                        raise ValueError("the DNS service did not load the new access rules")
                    time.sleep(0.1)
            except (CommandExecutionError, ValueError):
                self.dns_failed = True
                raise
        else:
            self._stop_dns()
        self._admission(plan)
        self._nft(render_access_firewall(plan, self.topology))
        self._clear_dns_connections()

    def _clear_dns_connections(self):
        # Existing NAT decisions outlive nft rule replacement. Drop only test
        # clients' DNS mappings so reused sockets pick up the new policy too.
        for protocol in ("udp", "tcp"):
            self._clear_dns_protocol(protocol, CONNTRACK_TIMEOUT_SECONDS)

    def _clear_dns_protocol(self, protocol, timeout):
        result = self.executor.run(
            Command(
                executable="conntrack",
                arguments=("-D", "-s", "192.168.50.0/24", "-p", protocol, "--dport", "53"),
                ignore_failure=True,
                timeout_seconds=timeout,
                purpose="refresh test-network DNS routes",
            )
        )
        if result.return_code and "0 flow entries have been deleted" not in result.stderr:
            raise CommandExecutionError("could not refresh DNS connection state")

    def _clear_old_dns_flows(self):
        """Clear old DNS mappings for a release. Return whether all were cleared.

        A failed protocol is tried again, at most DNS_CLEANUP_ATTEMPTS times in
        all and within DNS_CLEANUP_BUDGET_SECONDS, so Panic is never held long.
        """

        deadline = time.monotonic() + DNS_CLEANUP_BUDGET_SECONDS
        pending = ["udp", "tcp"]
        for attempt in range(DNS_CLEANUP_ATTEMPTS):
            if attempt:
                if time.monotonic() + DNS_CLEANUP_RETRY_DELAY_SECONDS >= deadline:
                    break
                time.sleep(DNS_CLEANUP_RETRY_DELAY_SECONDS)
            failed = []
            for protocol in pending:
                remaining = deadline - time.monotonic()
                try:
                    if remaining <= 0:
                        raise CommandExecutionError("the DNS cleanup ran out of time")
                    self._clear_dns_protocol(protocol, min(CONNTRACK_TIMEOUT_SECONDS, remaining))
                except Exception as error:  # noqa: BLE001 - the release must go on
                    LOGGER.warning("household release: clearing old %s DNS state failed: %s",
                                   protocol, error)
                    failed.append(protocol)
            pending = failed
            if not pending:
                return True
        return False

    def _restart_dns(self):
        self._run("systemctl", "restart", "jittersim-dns.service")

    def _probe(self, plan):
        try:
            probe = self._run(
                "dig",
                "@127.0.0.1",
                "-p",
                str(DNS_PORT),
                "jittersim-policy.invalid",
                "TXT",
                "+short",
                "+time=1",
                "+tries=1",
            )
            return probe.stdout.strip() == '"' + plan.digest() + '"'
        except CommandExecutionError:
            return False

    def check_health(self):
        with self.lock:
            if self.applied is not None and self.applied.has_dns_rules:
                self.health_error = (
                    None
                    if self._probe(self.applied)
                    else "Filtered DNS is unavailable. Device blocks remain active."
                )

    def disable(self, *, bounded=True, deadline=None):
        # Panic calls this while it holds the helper's network lock. A slow address
        # refresh or boot restoration must not hold Panic for longer than
        # RELEASE_LOCK_WAIT_SECONDS, nor past Panic's own `deadline` (a monotonic
        # time; the release lock still gets the deadline scope's floor after it).
        # Boot restoration waits as before.
        wait = bounded_wait(RELEASE_LOCK_WAIT_SECONDS, deadline) if bounded else None
        panic_deadline = deadline
        deadline = None if wait is None else time.monotonic() + wait
        if not self.lock.acquire(timeout=-1 if wait is None else wait):
            raise ValueError("Household rules are busy with another change. Try again.")
        try:
            try:
                plan = self.applied or read_access(self.directory / "policy.json")
            except (ValueError, OSError):
                plan = AccessPlan()
            return self.apply(
                replace(plan, options=replace(plan.options, enabled=False)),
                force=True,
                wait=None if deadline is None else max(0.0, deadline - time.monotonic()),
                deadline=panic_deadline,
            )
        finally:
            self.lock.release()

    def apply(self, plan: AccessPlan, *, force=False, wait=None, guard=True, deadline=None):
        """Replace the policy. `wait` bounds the wait for another change, in seconds.

        ``guard`` closes test Wi-Fi forwarding while the rules change. An address
        refresh does not need it: nft swaps its table atomically and DNS for the
        listed clients fails closed while the resolver restarts. ``deadline`` is
        Panic's: a release then waits only briefly for the ad-blocking lock.
        """

        with self._locked(wait):
            return self._apply_locked(plan, force=force, guard=guard, deadline=deadline)

    def _relax(self, problems, description, step, *arguments):
        """Run one release step. A failure is recorded and the release goes on.

        Any exception counts: on the Panic path an unexpected error in one step
        must not skip clearing the transition guard.
        """

        try:
            step(*arguments)
            return True
        except Exception as error:  # noqa: BLE001 - see above
            problems.append((description, error))
            return False

    def _release(self, plan: AccessPlan, *, deadline=None):
        """Turn household rules off without ever closing the test network.

        Every step only relaxes, so none is rolled back and a failed step does not
        stop the later ones. The permissive firewall goes in first and the
        transition guard is always cleared. Wi-Fi admission, the resolver and DNS
        connection state never bring back the restrictive rules. A Wi-Fi join limit
        or old DNS connections that could not be cleared are reported and set
        ``release_incomplete`` so Panic asks for a retry.
        """

        failures, warnings = [], []
        # Unknown earlier rules may have sent DNS to the resolver.
        previous = self.applied
        redirected = previous is None or previous.has_dns_rules or self._dns_flows_pending
        try:
            if self.ad_blocking_store is None:
                transition = nullcontext()
            elif deadline is None:
                transition = self.ad_blocking_store.access_transition()
            else:
                transition = self.ad_blocking_store.access_transition(
                    wait=bounded_wait(RELEASE_TRANSITION_WAIT_SECONDS, deadline, floor=0.0))
            transition.__enter__()
            entered = True
        except Exception:  # noqa: BLE001 - the release must go on
            # Panic already paused ad blocking. A busy settings lock must not keep
            # household rules on.
            LOGGER.warning("ad blocking was not paused for the household release", exc_info=True)
            entered = False
        try:
            opened = self._relax(failures, "the open access firewall",
                                 lambda: self._nft(render_access_firewall(plan, self.topology)))
            guard_cleared = False
            if not opened:
                # Never fall back to the restrictions a release removes. A plain
                # removal of the access tables and the guard needs no new rules and
                # is the least that can fail.
                opened = guard_cleared = self._relax(
                    failures, "removing the access tables", lambda: self._nft(_removal_firewall(self.topology)))
                if opened:
                    LOGGER.warning("the open access firewall did not load. The access tables were removed")
                    failures.clear()
            if opened:
                # Nothing is redirected to the resolver or to a MAC rule any more.
                self.applied = plan
            if not guard_cleared:
                self._relax(failures, "clearing the transition guard",
                            lambda: self._nft(transition_firewall(self.topology, clear=True)))
            self._relax(warnings, "the Wi-Fi join limits could not be cleared",
                        self._admission, plan)
            if opened:
                # Before this the old table may still send DNS to the resolver.
                # Old DNS mappings outlive the new table and can still point at
                # the resolver port, so the resolver stops only after they are
                # cleared. Until then it answers everyone without household rules.
                # Nothing here keeps anything closed.
                logged = []
                written = self._relax(
                    logged, "rewriting the resolver configuration",
                    lambda: atomic_write(self.directory / "unbound.conf", render_dns(plan)))
                if self._clear_old_dns_flows() or not redirected:
                    self._dns_flows_pending = False
                    self._relax(logged, "stopping the unused resolver",
                                lambda: self._stop_dns(wait=False))
                else:
                    self._dns_flows_pending = True
                    warnings.append(("old DNS connections could not be cleared", None))
                    if written:
                        self._relax(warnings, "the resolver could not drop the household rules",
                                    self._restart_dns)
                for description, error in logged:
                    LOGGER.warning("household release: %s failed: %s", description, error)
            if not failures:
                content = json.dumps(plan.payload(), sort_keys=True) + "\n"

                def persist():
                    self.runtime_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
                    atomic_write(self.directory / "policy.json", content)
                    atomic_write(self.runtime_path, content)

                self._relax(failures, "saving that household rules are off", persist)
        finally:
            if entered:
                try:
                    transition.__exit__(None, None, None)
                except Exception:  # noqa: BLE001 - the network is already open
                    LOGGER.warning("ad blocking did not record the household release",
                                   exc_info=True)
        for description, error in warnings:
            LOGGER.warning("household release: %s: %s", description, error)
        if failures:
            for description, error in failures:
                LOGGER.error("household release failed at %s: %s", description, error)
            self.error = "Household rules could not be fully turned off. Try Panic again."
            self.release_incomplete = True
            raise failures[0][1]
        self.generation = self._generation()
        self.health_error = None
        # Nothing restrictive comes back, but a warning means a restriction may
        # still be in force, so the release is not confirmed.
        self.release_incomplete = bool(warnings)
        self.error = (
            "Household rules are off, but " + "; ".join(text for text, _ in warnings) + "."
            if warnings else None
        )
        return self.status()

    def _apply_locked(self, plan: Optional[AccessPlan], *, force=False, guard=True, deadline=None):
        self.dns_failed = False
        self.release_incomplete = False
        if (
            not force
            and self.applied == plan
            and self.error is None
            and self.health_error is None
            and self.generation == self._generation()
        ):
            return self.status()
        if plan is not None and not plan.options.enabled:
            return self._release(plan, deadline=deadline)
        self.runtime_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        if plan is None:
            self._nft(transition_firewall(self.topology))
        try:
            old = read_access(self.directory / "policy.json")
        except (ValueError, OSError):
            # A checked replacement can repair damaged state. If it fails there
            # is no safe previous policy to reopen the network with.
            old = None
            if plan is None:
                self.error = "Saved access rules could not be read. The test network is closed."
                raise
        if plan is None:
            plan = old
            if not plan.options.enabled:
                return self._release(plan, deadline=deadline)
        if (
            plan.options.enabled
            and plan.options.reject_wifi_joins
            and not self.admission_supported()
        ):
            raise ValueError("Wi-Fi admission is unavailable with the current access point")
        candidate = self.directory / "candidate.conf"
        atomic_write(candidate, render_dns(plan))
        try:
            if plan.has_dns_rules:
                self._run("unbound-checkconf", str(candidate))
            self._nft(render_access_firewall(plan, self.topology), check=True)
        finally:
            candidate.unlink(missing_ok=True)
        transition = (self.ad_blocking_store.access_transition()
                      if self.ad_blocking_store is not None else nullcontext())
        with transition:
            if guard:
                self._nft(transition_firewall(self.topology))
            try:
                self._install(plan)
                # The install cleared the old DNS mappings too.
                self._dns_flows_pending = False
                content = json.dumps(plan.payload(), sort_keys=True) + "\n"
                atomic_write(self.directory / "policy.json", content)
                atomic_write(self.runtime_path, content)
                self._nft(transition_firewall(self.topology, clear=True))
            except Exception:
                self.error = "Access update failed. Previous rules were restored."
                if old is not None and not old.options.enabled:
                    # Going back to no household rules only relaxes. Use the
                    # release so a second failure cannot leave the guard closed.
                    try:
                        self._release(old)
                        self.error = "Access update failed. Household rules stay off."
                    except Exception:
                        LOGGER.exception("household release after a failed update failed")
                    raise
                try:
                    if old is None:
                        raise ValueError("no readable previous access policy")
                    self._install(old)
                    content = json.dumps(old.payload(), sort_keys=True) + "\n"
                    atomic_write(self.directory / "policy.json", content)
                    atomic_write(self.runtime_path, content)
                    self._nft(transition_firewall(self.topology, clear=True))
                    self.applied = old
                except Exception:
                    self.error = (
                        "Access recovery failed. "
                        "The test network is closed until recovery succeeds."
                        if guard else
                        "Access recovery failed. "
                        "Filtered DNS may be unavailable until recovery succeeds."
                    )
                    LOGGER.exception("persistent access recovery failed")
                raise
            self.applied = plan
            self.generation = self._generation()
            self.error = None
            self.health_error = None
            return self.status()

    def restore(self):
        return self.apply(None, force=True)

    def status(self):
        plan = self.applied
        return {
            "available": True,
            "wifi_admission_available": self.admission_supported(),
            "applied_digest": plan.digest() if plan is not None else None,
            "enabled": bool(plan and plan.options.enabled),
            "error": self.error or self.health_error,
            "policy": plan.payload() if plan is not None else None,
        }

    def refresh_addresses(self, observations):
        with self.lock:
            if self.applied is None or not self.applied.has_dns_rules:
                return
            by_mac = {}
            for device in observations[:256]:
                mac, address = device.get("mac_address"), device.get("ipv4_address")
                if mac and address:
                    by_mac.setdefault(mac, set()).add(address)
            clients = tuple(
                replace(c, address=next(iter(by_mac[c.mac])))
                if c.mac in by_mac and len(by_mac[c.mac]) == 1
                else c
                for c in self.applied.clients
            )
            candidate = replace(self.applied, clients=clients)
            try:
                candidate = access_from_payload(json.loads(json.dumps(candidate.payload())))
            except ValueError:
                return
            if candidate != self.applied:
                self.apply(candidate, guard=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Restore persistent JitterSim access rules.")
    parser.add_argument("--uplink", default="eth0")
    parser.add_argument("--test-interface", default="wlan0")
    args = parser.parse_args(argv)
    configure_logging()
    gateway_path = ACCESS_RUNTIME.parent / "gateway-state.json"
    try:
        guard = PanicGuard().provision(gateway_path)
        stopped = guard["household_stop_latched"]
    except OSError:
        LOGGER.exception("Panic state was not readable. Household rules remain disabled.")
        stopped = True
    # The helper keeps a stop recorded in either file (see HelperService.__init__).
    # Decide the same way so a guard save that failed after gateway.json never restores.
    try:
        from jittersim.networking.helper import gateway_household_stopped

        stopped = gateway_household_stopped(gateway_path) or stopped
    except Exception:  # noqa: BLE001 - an unknown stop state keeps household rules off
        LOGGER.exception("Gateway stop state was not readable. Household rules remain disabled.")
        stopped = True
    manager = AccessManager(
        executor=SubprocessExecutor(timeout_seconds=20),
        topology=Topology(
            uplink_interface=args.uplink,
            test_interface=args.test_interface,
        ),
    )
    if stopped:
        manager.disable(bounded=False)
    else:
        manager.restore()
    return 0

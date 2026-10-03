"""Persistent DNS and device restrictions owned independently of experiments."""

import argparse
import fcntl
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from jittersim.config.models import AccessOptions
from jittersim.engine.access import (
    MAX_ACCESS_BYTES,
    AccessPlan,
    TEST_NETWORK,
    access_from_payload,
    domain_matches,
)
from jittersim.panic_guard import PanicGuard

from .commands import Command
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
# Test clients keep reaching the dashboard sign-in whatever the MAC lists say. The
# application checks the token and the saved device permission itself and a
# blocked device must still be able to learn why. The experiment table has the
# same exception further down the input hook.
DASHBOARD_PORT = 8080


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


def render_dns(plan: AccessPlan) -> str:
    """Use Unbound views to check each client's rules before answering from cache."""
    lines = [
        "server:",
        f"  interface: 127.0.0.1@{DNS_PORT}",
        f"  interface: 192.168.50.1@{DNS_PORT}",
        "  ip-freebind: yes",
        '  chroot: ""',
        '  directory: "/"',
        '  username: "unbound"',
        '  pidfile: ""',
        "  use-syslog: yes",
        "  verbosity: 0",
        "  log-queries: no",
        "  log-replies: no",
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
    views = {plan.blocked_domains() if plan.options.enabled else (): "everyone"}
    if plan.options.enabled:
        for client in plan.clients:
            blocked = plan.blocked_domains(client.id)
            name = views.setdefault(blocked, f"group{len(views)}")
            if name != "everyone":
                lines.append(f'  access-control-view: {client.address}/32 "{name}"')
    # The existing shared-network DNS service retains DHCP names and upstream
    # resolver choices. Local output does not pass through our client redirect.
    lines += ["forward-zone:", '  name: "."', "  forward-addr: 192.168.50.1@53"]
    for blocked, name in views.items():
        lines += ["view:", f'  name: "{name}"', "  view-first: no"]
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
    # Keep this port private even while filtering is disabled.
    lines.append(
        f'add rule inet {table} input iifname != "{interface}" iifname != "lo" '
        f"meta l4proto {{ tcp, udp }} th dport {DNS_PORT} drop"
    )
    if not plan.options.enabled:
        return "\n".join(lines) + "\n"
    # Before every drop below. Replies leave through the output hook and the
    # egress chain so they carry the mark that the experiment table uses.
    lines.append(
        f'add rule inet {table} input iifname "{interface}" tcp dport {DASHBOARD_PORT} '
        'accept comment "keep dashboard sign-in reachable"'
    )
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
            f"ip saddr {TEST_NETWORK} udp dport {DNS_PORT} {chance}"
            "counter reject with icmpx type port-unreachable"
        )
        lines.append(
            f'add rule inet {table} input iifname "{interface}" '
            # Decide once per TCP connection. A later segment is part of the
            # same request and must not reset it part-way.
            f"ip saddr {TEST_NETWORK} tcp dport {DNS_PORT} ct state new {chance}"
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
    ):
        self.executor = executor
        self.topology = topology
        self.directory = Path(directory)
        self.runtime_path = Path(runtime_path)
        self.hostapd_path = Path(hostapd_path)
        self.lock = threading.RLock()
        self.error = None
        self.health_error = None
        self.applied = None
        self.generation = None
        # True after a change failed in the DNS step. The helper words its answer
        # from this.
        self.dns_failed = False

    @contextmanager
    def transaction(self):
        with self.lock:
            self.directory.mkdir(mode=0o750, parents=True, exist_ok=True)
            # Boot restoration and the live helper must not change policy together.
            with (self.directory / "lock").open("a") as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream, fcntl.LOCK_UN)

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

    def _stop_dns(self):
        # Nothing is redirected to the resolver any more. A failed stop must not
        # block an emergency release or an Apply that needs no DNS rules.
        try:
            self.executor.run(
                Command(
                    executable="systemctl",
                    arguments=("stop", "jittersim-dns.service"),
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
        # Existing NAT decisions outlive nft rule replacement. Drop only test
        # clients' DNS mappings so reused sockets pick up the new policy too.
        for protocol in ("udp", "tcp"):
            result = self.executor.run(
                Command(
                    executable="conntrack",
                    arguments=("-D", "-s", "192.168.50.0/24", "-p", protocol, "--dport", "53"),
                    ignore_failure=True,
                    timeout_seconds=2,
                    purpose="refresh test-network DNS routes",
                )
            )
            if result.return_code and "0 flow entries have been deleted" not in result.stderr:
                raise CommandExecutionError("could not refresh DNS connection state")

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

    def disable(self):
        with self.lock:
            try:
                plan = self.applied or read_access(self.directory / "policy.json")
            except (ValueError, OSError):
                plan = AccessPlan()
            return self.apply(
                replace(plan, options=replace(plan.options, enabled=False)), force=True
            )

    def apply(self, plan: AccessPlan, *, force=False):
        with self.transaction():
            self.dns_failed = False
            if (
                not force
                and self.applied == plan
                and self.error is None
                and self.health_error is None
                and self.generation == self._generation()
            ):
                return self.status()
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
            self._nft(transition_firewall(self.topology))
            try:
                self._install(plan)
                content = json.dumps(plan.payload(), sort_keys=True) + "\n"
                atomic_write(self.directory / "policy.json", content)
                atomic_write(self.runtime_path, content)
                self._nft(transition_firewall(self.topology, clear=True))
            except Exception:
                self.error = "Access update failed. Previous rules were restored."
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
                self.apply(candidate)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Restore persistent JitterSim access rules.")
    parser.add_argument("--uplink", default="eth0")
    parser.add_argument("--test-interface", default="wlan0")
    args = parser.parse_args(argv)
    try:
        guard = PanicGuard().provision(ACCESS_RUNTIME.parent / "gateway-state.json")
        stopped = guard["household_stop_latched"]
    except OSError:
        LOGGER.exception("Panic state was not readable. Household rules remain disabled.")
        stopped = True
    manager = AccessManager(
        executor=SubprocessExecutor(timeout_seconds=20),
        topology=Topology(
            uplink_interface=args.uplink,
            test_interface=args.test_interface,
        ),
    )
    if stopped:
        manager.disable()
    else:
        manager.restore()
    return 0

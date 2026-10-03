"""Optional DNS filtering with expiring routing and no lookup logs."""

import argparse
import fcntl
import grp
import json
import os
import re
import secrets
import socket
import struct
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import requests

from jittersim.config.adblocking import (
    ACTIVE_FILE,
    DOMAIN_FILE,
    HEALTH_DOMAIN,
    MAX_LIST_BYTES,
    MAX_STATE_BYTES,
    DomainPolicy,
    allowed_domains,
    blocking_macs,
    domain_bytes,
    parse_hosts,
    read_domains,
)

from .access import ACCESS_RUNTIME, read_access, render_dns
from .commands import Command
from .executor import CommandExecutionError, SubprocessExecutor
from .topology import Topology

POLICY_FILE = Path("/etc/jittersim/adblocking.json")
LOCK_FILE = Path("/run/jittersim/adblocking.lock")
STATUS_FILE = Path("/run/jittersim/adblocking-status.json")
LIST_INFO_FILE = Path("/var/lib/jittersim-adblocking/list.json")
DNS_CONFIG_FILE = Path("/run/jittersim/adblocking-unbound.conf")
BLOCKLIST_URL = "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"
ADS_TABLE = "jittersim_adblocking"
DNS_PORT = 5354
LEASE_SECONDS = 5
REFRESH_SECONDS = 1
MAX_DOWNLOAD_SECONDS = 20


def read_json(path, default=None):
    try:
        with path.open("rb") as source:
            data = source.read(MAX_STATE_BYTES + 1)
    except FileNotFoundError:
        return {} if default is None else default
    if len(data) > MAX_STATE_BYTES:
        raise ValueError("Ad-blocking settings exceed their size limit")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Invalid ad-blocking settings")
    return value


def atomic_write(path, data, group_id=None):
    """Replace one owned file without exposing a partly written generation."""

    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o640)
            if group_id is not None:
                os.fchown(output.fileno(), -1, group_id)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value, group_id=None):
    atomic_write(path, json.dumps(value, sort_keys=True).encode("ascii"), group_id)


class AdRouting:
    def __init__(self, interface, executor=None):
        Topology(
            uplink_interface="ads-unused" if interface != "ads-unused" else "ads-other",
            test_interface=interface,
        )
        self.interface = interface
        self.executor = executor or SubprocessExecutor(timeout_seconds=2)

    def _run(self, script):
        self.executor.run(
            Command(
                executable="nft",
                arguments=("-f", "-"),
                stdin=script,
                purpose="update optional DNS filtering",
            )
        )

    def initialize(self):
        """Only rewrite local DNS packets so stale connection mappings cannot survive bypass."""

        selected = (
            f'iifname "{self.interface}" ip saddr 192.168.50.0/24 '
            "ip daddr 192.168.50.1 ether saddr @clients"
        )
        self._run(f'''add table inet {ADS_TABLE}
delete table inet {ADS_TABLE}
table inet {ADS_TABLE} {{
 set clients {{ type ether_addr; flags timeout; timeout {LEASE_SECONDS}s; }}
 chain incoming {{ type filter hook prerouting priority raw; policy accept;
  {selected} udp dport 53 udp dport set {DNS_PORT} notrack
  {selected} tcp dport 53 tcp dport set {DNS_PORT} notrack
 }}
 chain outgoing {{ type filter hook output priority raw; policy accept;
  oifname "{self.interface}" ip saddr 192.168.50.1 udp sport {DNS_PORT} udp sport set 53 notrack
  oifname "{self.interface}" ip saddr 192.168.50.1 tcp sport {DNS_PORT} tcp sport set 53 notrack
 }}
 chain guard {{ type filter hook input priority -10; policy accept;
  iifname "lo" accept
  iifname "{self.interface}" ip saddr 192.168.50.0/24 ether saddr @clients accept
  udp dport {DNS_PORT} drop
  tcp dport {DNS_PORT} drop
 }}
}}
''')

    def refresh(self, macs):
        macs = blocking_macs(macs)
        script = f"flush set inet {ADS_TABLE} clients\n"
        if macs:
            script += f"add element inet {ADS_TABLE} clients {{ " + ", ".join(macs) + " }\n"
        self._run(script)

    def clear(self):
        self.initialize()


class AdStore:
    def __init__(
        self,
        routing,
        path=POLICY_FILE,
        lock_path=LOCK_FILE,
        active_path=ACTIVE_FILE,
        status_path=STATUS_FILE,
        group_id=None,
    ):
        self.routing = routing
        self.path = path
        self.lock_path = lock_path
        self.active_path = active_path
        self.status_path = status_path
        self.group_id = group_id

    @contextmanager
    def lock(self):
        with self.lock_path.open("a") as lock:
            deadline = time.monotonic() + 3
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError("Ad-blocking settings are busy. Try again.")
                    time.sleep(0.02)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def read(self):
        value = read_json(
            self.path,
            {
                "mac_addresses": [],
                "allowed_domains": [],
                "running": True,
                "generation": "",
                "update_request": "",
            },
        )
        value["mac_addresses"] = blocking_macs(value.get("mac_addresses"))
        value["allowed_domains"] = allowed_domains(value.get("allowed_domains"))
        value["suspended"] = value.get("suspended", False)
        if type(value["suspended"]) is not bool:
            raise ValueError("Invalid access transition state")
        if type(value.get("running")) is not bool:
            raise ValueError("Invalid ad-blocking state")
        if not isinstance(value.get("generation"), str) or len(value["generation"]) > 32:
            raise ValueError("Invalid ad-blocking generation")
        if not isinstance(value.get("update_request"), str) or len(value["update_request"]) > 32:
            raise ValueError("Invalid blocklist update request")
        return value

    def _clear(self):
        self.routing.clear()
        write_json(
            self.active_path,
            {"mac_addresses": [], "allowed_domains": [], "expires_at": 0, "list_digest": ""},
            self.group_id,
        )

    def configure(self, macs, allowances, update=False, resume=False):
        macs, allowances = blocking_macs(macs), allowed_domains(allowances)
        if type(update) is not bool or type(resume) is not bool:
            raise ValueError("Invalid ad-blocking operation")
        with self.lock():
            state = self.read()
            changed = macs != state["mac_addresses"] or allowances != state["allowed_domains"]
            if changed or update or resume:
                state.update(
                    mac_addresses=macs, allowed_domains=allowances, generation=secrets.token_hex(8)
                )
                if resume:
                    state["running"] = True
                if update:
                    state["update_request"] = secrets.token_hex(8)
                write_json(self.path, state, self.group_id)
                self._clear()
        return self.status()

    def pause(self):
        with self.lock():
            state = self.read()
            state.update(running=False, generation=secrets.token_hex(8))
            write_json(self.path, state, self.group_id)
            self._clear()

    @contextmanager
    def access_transition(self):
        """Bypass ads while household DNS rules change without losing saved switches."""

        with self.lock():
            state = self.read()
            state.update(suspended=True, generation=secrets.token_hex(8))
            write_json(self.path, state, self.group_id)
            self._clear()
        try:
            yield
        finally:
            with self.lock():
                state = self.read()
                state.update(suspended=False, generation=secrets.token_hex(8))
                write_json(self.path, state, self.group_id)
                self._clear()

    def resume(self):
        with self.lock():
            state = self.read()
            if not state["running"]:
                state.update(running=True, generation=secrets.token_hex(8))
                write_json(self.path, state, self.group_id)
                self._clear()

    def status(self):
        desired = self.read()
        try:
            status = read_json(self.status_path)
            active = read_json(self.active_path)
            ready = (
                active.get("generation") == desired["generation"]
                and isinstance(active.get("expires_at"), (int, float))
                and active["expires_at"] > time.monotonic()
                and desired["running"]
            )
        except (OSError, ValueError, TypeError):
            status, ready = {}, False
        message = status.get("message", "Waiting for the ad-blocking service.")
        ready = ready and not desired["suspended"]
        if not desired["running"]:
            message = "Ad blocking is paused."
        elif desired["suspended"]:
            message = "Waiting for household DNS rules to finish applying."
        elif not desired["mac_addresses"]:
            message = "Ad blocking is off."
        elif not ready and message.startswith("Ad blocking is active."):
            message = "Waiting for the ad-blocking service."
        return {
            "available": True,
            "ready": bool(ready),
            "paused": not desired["running"],
            "domain_count": status.get("domain_count", 0),
            "updated_at": status.get("updated_at", 0),
            "message": message,
            "query_history_saved": False,
        }


def filtering_config(domains, allowances, access, dns_user="jittersim"):
    """Reuse household views while keeping one shared advertising tree."""

    policy = DomainPolicy(frozenset(domains), frozenset(allowances))
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", dns_user):
        raise ValueError("Invalid DNS service account")
    server, views = render_dns(access).split("forward-zone:\n", 1)
    server = (
        server.replace("@5335", f"@{DNS_PORT}")
        .replace('username: "unbound"', f'username: "{dns_user}"')
        .replace("use-syslog: yes", 'use-syslog: no\n  logfile: "/dev/null"')
    )
    existing = set(re.findall(r'local-zone: "([^"]+)\." ', server))
    lines = [f'  local-zone: "{HEALTH_DOMAIN}." always_nxdomain']
    lines.extend(
        f'  local-zone: "{domain}." always_nxdomain'
        for domain in sorted(policy.domains)
        if not policy.allowed(domain) and domain not in existing and domain != HEALTH_DOMAIN
    )
    lines.extend(
        f'  local-zone: "{domain}." always_transparent'
        for domain in sorted(policy.allowances)
        if domain not in existing and domain != HEALTH_DOMAIN
    )
    # Household denies take priority. Permitted names use the shared ad tree.
    # The large list is never copied into individual device views.
    views = views.replace("view-first: no", "view-first: yes").replace(
        "always_transparent", "noview"
    )
    return (server + "\n".join(lines) + "\nforward-zone:\n" + views).encode("ascii")


def healthy():
    identifier = secrets.randbelow(65536)
    labels = (
        b"".join(bytes([len(part)]) + part.encode("ascii") for part in HEALTH_DOMAIN.split("."))
        + b"\0"
    )
    query = struct.pack("!HHHHHH", identifier, 256, 1, 0, 0, 0) + labels + struct.pack("!HH", 1, 1)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.25)
            probe.connect(("127.0.0.1", DNS_PORT))
            probe.send(query)
            reply = probe.recv(512)
        return (
            len(reply) >= 12
            and struct.unpack("!H", reply[:2])[0] == identifier
            and struct.unpack("!H", reply[2:4])[0] & 0x800F == 0x8003
        )
    except OSError:
        return False


def download_domains():
    deadline = time.monotonic() + MAX_DOWNLOAD_SECONDS
    data = bytearray()
    with requests.get(
        BLOCKLIST_URL, stream=True, timeout=(5, 5), allow_redirects=False
    ) as response:
        if response.status_code != 200:
            raise ValueError("The blocklist publisher did not return a list")
        for chunk in response.iter_content(65536):
            if time.monotonic() >= deadline or len(data) + len(chunk) > MAX_LIST_BYTES:
                raise ValueError("The blocklist download exceeded its limit")
            data.extend(chunk)
    domains = parse_hosts(bytes(data))
    if len(domains) < 100:
        raise ValueError("The downloaded blocklist is unexpectedly small")
    return domains


class AdController:
    def __init__(
        self,
        store,
        domains_path=DOMAIN_FILE,
        info_path=LIST_INFO_FILE,
        config_path=DNS_CONFIG_FILE,
        dns_user="jittersim",
        downloader=download_domains,
        access_path=ACCESS_RUNTIME,
    ):
        self.store = store
        self.domains_path = domains_path
        self.info_path = info_path
        self.config_path = config_path
        self.dns_user = dns_user
        self.downloader = downloader
        self.access_path = access_path
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ad-list")
        self.future = None
        self.child = None
        self.key = None
        self.domains = ()
        self.digest = ""
        self.list_generation = None
        self.next_fetch = 0
        self.next_start = 0
        self.update_request = None
        self.failures = 0
        self.message = "Ad blocking is off."

    def _stop(self):
        self.store._clear()
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=1)
        self.child = None
        self.key = None

    def _list(self):
        try:
            info = self.domains_path.stat()
            generation = (info.st_ino, info.st_mtime_ns, info.st_size)
            if generation != self.list_generation:
                self.domains, self.digest = read_domains(self.domains_path)
                self.list_generation = generation
        except FileNotFoundError:
            self.domains, self.digest = (), ""
        return read_json(self.info_path)

    def _download(self, desired, info):
        now = time.monotonic()
        if self.future is not None and self.future.done():
            try:
                domains = self.future.result()
                self._stop()
                atomic_write(self.domains_path, domain_bytes(domains), self.store.group_id)
                info = {"updated_at": int(time.time()), "domain_count": len(domains)}
                write_json(self.info_path, info, self.store.group_id)
                self._list()
                self.failures = 0
                self.message = "Blocklist updated."
                self.next_fetch = now + 86400
            except Exception:
                self.failures += 1
                self.message = (
                    "Blocklist update failed. The last working list is retained."
                    if self.domains
                    else "Blocklist update failed. Normal DNS rules are in use."
                )
                self.next_fetch = now + (60 * self.failures if self.failures < 3 else 86400)
            self.future = None
        if desired["update_request"] != self.update_request:
            self.update_request = desired["update_request"]
            self.failures = 0
            self.next_fetch = 0
        old = time.time() - info.get("updated_at", 0) >= 86400
        if self.future is None and now >= self.next_fetch and (old or self.next_fetch == 0):
            self.message = "Updating the blocklist."
            self.future = self.pool.submit(self.downloader)
        return info

    def tick(self):
        desired = self.store.read()
        info = self._list()
        if not desired["mac_addresses"] or not desired["running"] or desired["suspended"]:
            if self.child is not None or self.key is not None:
                self._stop()
            self.message = (
                "Ad blocking is paused."
                if not desired["running"]
                else "Waiting for household DNS rules to finish applying."
                if desired["suspended"]
                else "Ad blocking is off."
            )
        else:
            info = self._download(desired, info)
            access = read_access(self.access_path)
            key = (self.digest, desired["allowed_domains"], access.digest())
            if self.domains and (
                self.child is None or self.child.poll() is not None or self.key != key
            ):
                if time.monotonic() >= self.next_start:
                    self._stop()
                    atomic_write(
                        self.config_path,
                        filtering_config(self.domains, key[1], access, self.dns_user),
                        self.store.group_id,
                    )
                    subprocess.run(
                        ["unbound-checkconf", str(self.config_path)],
                        check=True,
                        timeout=5,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self.child = subprocess.Popen(
                        [
                            "unbound",
                            "-d",
                            "-c",
                            str(self.config_path),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self.key = key
                    self.next_start = time.monotonic() + 5
            if (
                self.key == key
                and self.child is not None
                and self.child.poll() is None
                and healthy()
            ):
                committed = False
                with self.store.lock():
                    if (
                        self.store.read()["generation"] == desired["generation"]
                        and read_access(self.access_path).digest() == key[2]
                    ):
                        self.store.routing.refresh(desired["mac_addresses"])
                        write_json(
                            self.store.active_path,
                            {
                                "mac_addresses": desired["mac_addresses"],
                                "allowed_domains": desired["allowed_domains"],
                                "generation": desired["generation"],
                                "list_digest": self.digest,
                                "expires_at": time.monotonic() + LEASE_SECONDS,
                            },
                            self.store.group_id,
                        )
                        committed = True
                if committed and self.future is None and self.failures == 0:
                    self.message = "Ad blocking is active. Browsing history is not saved."
                if not committed:
                    self.store._clear()
                    self.message = "Waiting for the latest settings."
            elif self.domains:
                self.store._clear()
                self.message = "The blocker is unavailable. Normal DNS rules are in use."
        write_json(self.store.status_path, {**info, "message": self.message}, self.store.group_id)

    def close(self):
        self._stop()
        self.pool.shutdown(wait=False, cancel_futures=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run optional ad blocking for protected devices.")
    parser.add_argument("--test-interface", required=True)
    parser.add_argument("--clear-only", action="store_true")
    args = parser.parse_args(argv)
    store = AdStore(AdRouting(args.test_interface), group_id=grp.getgrnam("jittersim").gr_gid)
    if args.clear_only:
        with store.lock():
            store._clear()
        return 0
    controller = AdController(store)
    store.routing.initialize()
    try:
        while True:
            try:
                controller.tick()
            except (
                OSError,
                ValueError,
                CommandExecutionError,
                subprocess.SubprocessError,
                requests.RequestException,
            ):
                store._clear()
                write_json(
                    store.status_path,
                    {"message": "The blocker is unavailable. Normal DNS rules are in use."},
                    store.group_id,
                )
            time.sleep(REFRESH_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        controller.close()
    return 0

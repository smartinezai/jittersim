"""Optional DNS filtering with expiring routing and no lookup logs."""

import argparse
import contextvars
import fcntl
import grp
import hashlib
import json
import logging
import os
import pwd
import re
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from jittersim.diagnostic_privacy import configure_logging
from jittersim.panic_guard import PanicGuard

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
INTENT_FILE = Path("/var/lib/jittersim-control/adblocking-intent.json")
LOCK_FILE = Path("/run/jittersim/adblocking.lock")
STATUS_FILE = Path("/run/jittersim/adblocking-status.json")
LIST_INFO_FILE = Path("/var/lib/jittersim-adblocking/list.json")
DNS_CONFIG_FILE = Path("/run/jittersim/adblocking-unbound.conf")
BLOCKLIST_URL = "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"
ADS_TABLE = "jittersim_adblocking"
DNS_PORT = 5354
LEASE_SECONDS = 5
# The end of a household access transition waits at least this long for the settings lock.
TRANSITION_END_WAIT_SECONDS = 3.0
# A pause waits at least this long for its saves once it holds the settings lock.
PAUSE_SAVE_FLOOR_SECONDS = 0.5
REFRESH_SECONDS = 1
MAX_DOWNLOAD_SECONDS = 20
LOGGER = logging.getLogger(__name__)


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
        intent_path=INTENT_FILE,
        panic_guard=None,
    ):
        self.routing = routing
        self.path = path
        self.lock_path = lock_path
        self.active_path = active_path
        self.status_path = status_path
        self.group_id = group_id
        self.intent_path = intent_path
        self.panic_guard = panic_guard or PanicGuard()
        # The thread of a pause whose saves outlived its wait, if any.
        self._pause_saver = None

    @contextmanager
    def lock(self, wait=3.0):
        with self.lock_path.open("a") as lock:
            deadline = time.monotonic() + wait
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
                "running": False,
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
        # Release recovery restores settings but must never reverse a later stop.
        intent = read_json(self.intent_path, {"version": 2, "running": False})
        if (
            type(intent.get("version")) is not int or intent["version"] not in (1, 2)
            or type(intent.get("running")) is not bool
        ):
            raise ValueError("Invalid ad-blocking stop intent")
        witness = intent.get("panic_witness")
        valid_witness = isinstance(witness, str) and re.fullmatch(r"[0-9a-f]{64}", witness)
        value["running"] = bool(
            intent["running"] and intent["version"] == 2
            and valid_witness and witness == self._panic_witness()
        )
        return value

    def _panic_witness(self):
        def identity(metadata):
            return (metadata.st_dev, metadata.st_ino, metadata.st_ctime_ns,
                    metadata.st_mtime_ns, metadata.st_size)

        before = identity(os.stat(self.panic_guard.path, follow_symlinks=False))
        state = self.panic_guard.read()
        after = identity(os.stat(self.panic_guard.path, follow_symlinks=False))
        if before != after or state is None or state["panic_recovery_failed"]:
            raise ValueError("Panic state must be confirmed before resuming ad blocking")
        # Older approved helpers replace this file even for an identical stop.
        # A saved permission must belong to the current validated file identity.
        return hashlib.sha256(json.dumps([after, state], sort_keys=True).encode("ascii")).hexdigest()

    def _intent(self, running):
        witness = self._panic_witness() if running else None
        write_json(self.intent_path, {"version": 2, "running": running, "panic_witness": witness})

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
            damaged = False
            try:
                state = self.read()
            except (OSError, ValueError):
                # The authenticated helper supplies the complete saved selection.
                # Rebuilding this cache never publishes permission to resume.
                state = {"mac_addresses": (), "allowed_domains": (), "running": False,
                         "generation": "", "update_request": "", "suspended": False}
                damaged = True
            changed = damaged or macs != state["mac_addresses"] or allowances != state["allowed_domains"]
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
                if resume:
                    self._intent(True)
        return self.status()

    def pause(self, *, wait=3.0):
        """Stop ad blocking within about `wait` seconds. Panic passes a longer `wait`.

        `wait` bounds the wait for the settings lock and the saves together, and the
        saves get at least PAUSE_SAVE_FLOOR_SECONDS. Panic pauses before it removes
        any rule, so a storage stall here must not hold that (review of PR 138). The
        saves then go on in their own thread, which keeps the settings lock until
        they end, and the pause raises so the stop counts as unconfirmed.
        """
        previous = self._pause_saver
        if previous is not None and previous.is_alive():
            raise ValueError("An earlier ad-blocking stop is still being saved. Try again.")
        deadline = time.monotonic() + wait
        held = self.lock(wait)
        held.__enter__()
        raised = []
        late = threading.Event()

        def save():
            try:
                self._pause_locked()
            except BaseException as error:  # noqa: BLE001 - raised again below, or logged
                raised.append(error)
                if late.is_set():
                    LOGGER.error("a late ad-blocking stop save failed (%s)", type(error).__name__)
            finally:
                held.__exit__(None, None, None)

        saver = threading.Thread(target=contextvars.copy_context().run, args=(save,),
                                 name="ad-blocking-pause", daemon=True)
        try:
            saver.start()
        except BaseException:
            held.__exit__(None, None, None)
            raise
        self._pause_saver = saver
        saver.join(max(PAUSE_SAVE_FLOOR_SECONDS, deadline - time.monotonic()))
        if saver.is_alive():
            late.set()
            LOGGER.error("ad-blocking stop is still being saved; the stop is unconfirmed")
            raise ValueError("The ad-blocking stop could not be saved in time")
        if raised:
            raise raised[0]

    def _pause_locked(self):
        """The saves of `pause`, with the settings lock held."""
        self._intent(False)
        try:
            state = self.read()
        except (OSError, ValueError):
            self._clear()
            return
        state.update(running=False, generation=secrets.token_hex(8))
        write_json(self.path, state, self.group_id)
        self._clear()

    @contextmanager
    def access_transition(self, *, wait=3.0):
        """Bypass ads while household DNS rules change without losing saved switches.

        `wait` bounds the wait for the settings lock on entry. Panic's release passes a
        short one. The end waits at least TRANSITION_END_WAIT_SECONDS: it only clears
        the suspension, and giving up there would leave ads bypassed for good (review O-c).
        """

        with self.lock(wait):
            try:
                state = self.read()
            except (OSError, ValueError):
                self._intent(False)
                suspended = False
            else:
                suspended = True
                state.update(suspended=True, generation=secrets.token_hex(8))
                write_json(self.path, state, self.group_id)
            self._clear()
        try:
            yield
        finally:
            if suspended:
                with self.lock(max(wait, TRANSITION_END_WAIT_SECONDS)):
                    try:
                        state = self.read()
                    except (OSError, ValueError):
                        self._intent(False)
                    else:
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
                self._intent(True)

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
        if not desired["mac_addresses"]:
            message = "Ad blocking is off."
        elif not desired["running"]:
            message = "Ad blocking is paused."
        elif desired["suspended"]:
            message = "Waiting for household DNS rules to finish applying."
        elif not ready and message.startswith("Ad blocking is active."):
            message = "Waiting for the ad-blocking service."
        return {
            "available": True,
            "ready": bool(ready),
            "paused": bool(desired["mac_addresses"]) and not desired["running"],
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
    access = replace(access, websites=replace(access.websites, blocked_domains=()),
                     protected_mac_addresses=())
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


def download_bytes():
    # Imported here: only the short-lived --fetch-domains child downloads. Every process that
    # imports jittersim.networking loads this module, and requests with its TLS stack costs
    # about 5 MB of Python heap in each of them (soak audit 2026-10-18).
    import requests

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
    return bytes(data)


def _download_errors():
    """The requests error type when requests was loaded, else nothing more to catch."""

    loaded = sys.modules.get("requests")
    return (loaded.RequestException,) if loaded is not None else ()


def download_domains():
    # DNS resolution and a slow partial HTTP chunk must share the same deadline.
    # A killed fetch cannot occupy the single worker or delay a later refresh.
    result = subprocess.run(
        [sys.executable, "-m", "jittersim.networking.adblocking", "--fetch-domains"],
        check=True,
        timeout=MAX_DOWNLOAD_SECONDS,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    domains = parse_hosts(result.stdout)
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
                    self.next_start = time.monotonic() + 5
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
            if (
                self.key == key
                and self.child is not None
                and self.child.poll() is None
                and healthy()
            ):
                committed = False
                with self.store.lock():
                    latest = self.store.read()
                    if (
                        latest["running"] and not latest["suspended"]
                        and latest["generation"] == desired["generation"]
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
    parser.add_argument("--test-interface")
    parser.add_argument("--clear-only", action="store_true")
    parser.add_argument("--fetch-domains", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    configure_logging()
    if args.fetch_domains:
        if args.test_interface or args.clear_only:
            parser.error("A list fetch cannot change routing")
        if os.geteuid() == 0:
            account = pwd.getpwnam("jittersim")
            os.setgroups([account.pw_gid])
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)
        sys.stdout.buffer.write(download_bytes())
        return 0
    if not args.test_interface:
        parser.error("--test-interface is required")
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
            ) + _download_errors():
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


if __name__ == "__main__":
    raise SystemExit(main())

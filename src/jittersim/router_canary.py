"""A small, read-only router health canary for the persistent outage log.

Every minute the dashboard checks, through the Pi's uplink, that the household
router still answers: its ARP entry, one ping and one DNS question to the
router's resolver. Nothing is changed on the Pi or the router and no login is
made. When two checks in a row fail, one journal record names what failed, the
running profiles, the connection tracking count and whether upload corruption
runs, so the next outage leaves evidence from before the router is restarted.
A record also follows every ten minutes while it stays down and when it
recovers.

It uses `ping` (iputils) and plain files under /proc and /sys, and writes
through `diagnostic_records.send_fields`, the bounded journal writer of the
outage log. `jittersim diagnostics export` lists these records as service
warnings with their times; `journalctl -t jittersim` shows their text.
"""

import os
import random
import socket
import struct
import subprocess
import threading
import time
from ipaddress import IPv4Address
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple

from jittersim.diagnostic_records import send_fields
from jittersim.networking.renderer import UPLOAD_DEVICE

INTERVAL_SECONDS = 60.0
# Two failed checks in a row before a record: one lost ping is not an outage.
FAILURES_TO_REPORT = 2
REPEAT_SECONDS = 600.0
PING_TIMEOUT_SECONDS = 2
DNS_TIMEOUT_SECONDS = 2.0
DNS_NAME = "example.com"
PROC_ROUTE = Path("/proc/net/route")
PROC_ARP = Path("/proc/net/arp")
CONNTRACK_COUNT = Path("/proc/sys/net/netfilter/nf_conntrack_count")
UPLOAD_DEVICE_PATH = Path("/sys/class/net") / UPLOAD_DEVICE
MAX_PROFILE_TEXT = 200


def default_gateway(route_table: Path = PROC_ROUTE) -> Optional[Tuple[str, str]]:
    """Return the (interface, address) of the IPv4 default route with the lowest metric."""

    try:
        lines = route_table.read_text(encoding="ascii", errors="replace").splitlines()[1:]
    except OSError:
        return None
    best = None
    for line in lines:
        fields = line.split()
        if len(fields) < 7 or fields[1] != "00000000":
            continue
        try:
            flags = int(fields[3], 16)
            metric = int(fields[6])
            gateway = IPv4Address(struct.pack("<I", int(fields[2], 16)))
        except ValueError:
            continue
        # RTF_UP and RTF_GATEWAY.
        if flags & 0x3 != 0x3:
            continue
        if best is None or metric < best[0]:
            best = (metric, fields[0], str(gateway))
    return None if best is None else (best[1], best[2])


def arp_state(gateway: str, interface: str, arp_table: Path = PROC_ARP) -> str:
    """Return "reachable", "incomplete" or "missing" for the gateway's ARP entry."""

    try:
        lines = arp_table.read_text(encoding="ascii", errors="replace").splitlines()[1:]
    except OSError:
        return "unreadable"
    for line in lines:
        fields = line.split()
        if len(fields) >= 6 and fields[0] == gateway and fields[5] == interface:
            try:
                complete = int(fields[2], 16) & 0x2
            except ValueError:
                return "unreadable"
            return "reachable" if complete else "incomplete"
    return "missing"


def ping_state(gateway: str, interface: str, run=subprocess.run) -> str:
    """Send one ping to the gateway through the uplink with iputils' ping."""

    try:
        result = run(
            ["ping", "-n", "-q", "-c", "1", "-W", str(PING_TIMEOUT_SECONDS), "-I", interface,
             gateway],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=PING_TIMEOUT_SECONDS + 2, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    return {0: "answered", 1: "no-reply"}.get(result.returncode, "unavailable")


def _dns_query(identifier: int, name: str = DNS_NAME) -> bytes:
    labels = b"".join(bytes([len(part)]) + part.encode("ascii") for part in name.split("."))
    return struct.pack("!HHHHHH", identifier, 0x0100, 1, 0, 0, 0) + labels + b"\0" + struct.pack("!HH", 1, 1)


def dns_state(gateway: str, interface: str, *, connect=socket.socket) -> str:
    """Ask the router's resolver one A question. Any answer with our ID counts."""

    identifier = random.getrandbits(16)
    try:
        with connect(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(DNS_TIMEOUT_SECONDS)
            try:
                client.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_BINDTODEVICE", 25),
                                  interface.encode("ascii") + b"\0")
            except OSError:
                # Binding needs a privilege. The default route already uses the uplink.
                pass
            client.sendto(_dns_query(identifier), (gateway, 53))
            deadline = time.monotonic() + DNS_TIMEOUT_SECONDS
            while True:
                client.settimeout(max(0.01, deadline - time.monotonic()))
                answer, source = client.recvfrom(4096)
                if source[0] != gateway or len(answer) < 12:
                    continue
                answered_id, flags = struct.unpack("!HH", answer[:4])
                if answered_id != identifier:
                    continue
                return f"answered-rcode-{flags & 0xF}"
    except socket.timeout:
        return "timeout"
    except OSError:
        return "error"


def _read_number(path: Path) -> str:
    try:
        text = path.read_text(encoding="ascii").strip()
    except OSError:
        return "unknown"
    return text if text.isdigit() and len(text) <= 12 else "unknown"


def _upload_device_present(path: Path = UPLOAD_DEVICE_PATH) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _safe_text(value: str) -> str:
    return "".join(character if character.isprintable() else "?" for character in value)


class RouterCanary:
    """Run the checks on a daemon thread and record failures in the journal."""

    def __init__(
        self,
        *,
        profiles: Callable[[], Iterable[str]] = lambda: (),
        clock: Callable[[], float] = time.monotonic,
        gateway: Callable[[], Optional[Tuple[str, str]]] = default_gateway,
        arp: Callable[[str, str], str] = arp_state,
        ping: Callable[[str, str], str] = ping_state,
        dns: Callable[[str, str], str] = dns_state,
        record: Callable[..., bool] = send_fields,
        interval_seconds: float = INTERVAL_SECONDS,
    ) -> None:
        self._profiles = profiles
        self._clock = clock
        self._gateway = gateway
        self._arp = arp
        self._ping = ping
        self._dns = dns
        self._record = record
        self._interval = interval_seconds
        self._failures = 0
        # A router that never answers pings or DNS (a firewall setting) is not
        # down. A check counts once it has answered at least once.
        self._ping_seen = False
        self._dns_seen = False
        self._down_since: Optional[float] = None
        self._last_report = 0.0
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._closed = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._closed or (self._thread is not None and self._thread.is_alive()):
                return
            self._thread = threading.Thread(target=self._run, name="jittersim-router-canary",
                                            daemon=True)
            try:
                self._thread.start()
            except BaseException:
                self._closed = True
                self._stop_event.set()
                raise

    def begin_shutdown(self) -> None:
        with self._lifecycle_lock:
            self._closed = True
            self._stop_event.set()

    def stop(self, timeout: float = 1.0) -> None:
        """Ask the thread to stop and wait briefly.

        The canary holds no shared state (it only reads /proc and writes one
        journal record), so a check still waiting for a ping or a DNS answer
        never holds up the dashboard's shutdown: its daemon thread ends with the
        process.
        """

        self.begin_shutdown()
        thread = self._thread
        if thread is None:
            return
        try:
            thread.join(timeout=timeout)
        except RuntimeError:
            return
        if not thread.is_alive():
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                self.check_once()
            except Exception:
                # A canary must never take the dashboard down. The next round retries.
                pass

    def check_once(self) -> Dict[str, str]:
        """Run one round and record it when it changes the router's state."""

        route = self._gateway()
        if route is None:
            results = {"route": "missing"}
        else:
            interface, gateway = route
            results = {
                "route": "present",
                "arp": self._arp(gateway, interface),
                "ping": self._ping(gateway, interface),
                "dns": self._dns(gateway, interface),
            }
        self._ping_seen = self._ping_seen or results.get("ping") == "answered"
        self._dns_seen = self._dns_seen or results.get("dns", "").startswith("answered")
        # An incomplete ARP entry alone is normal between uses, so it is
        # recorded but never decides.
        failed = (
            results.get("route") == "missing"
            or (self._ping_seen and results.get("ping") == "no-reply")
            or (self._dns_seen and results.get("dns") in ("timeout", "error"))
        )
        now = self._clock()
        if failed:
            self._failures += 1
            if self._failures == FAILURES_TO_REPORT:
                self._down_since = now
                self._report("router health check failed", results, priority="4")
            elif (self._failures > FAILURES_TO_REPORT
                  and now - self._last_report >= REPEAT_SECONDS):
                self._report("router health check still failing", results, priority="4")
        else:
            if self._down_since is not None:
                down = int(now - self._down_since)
                self._report(f"router health check passes again after about {down} s",
                             results, priority="5")
            self._failures = 0
            self._down_since = None
        return results

    def _report(self, headline: str, results: Dict[str, str], *, priority: str) -> None:
        self._last_report = self._clock()
        try:
            profiles = ", ".join(sorted({str(name) for name in self._profiles() if name}))
        except Exception:
            profiles = "unknown"
        details = " ".join(f"{key}={value}" for key, value in results.items())
        message = _safe_text(
            f"{headline}: {details} profiles=[{profiles[:MAX_PROFILE_TEXT] or 'none'}] "
            f"upload_corruption={'on' if _upload_device_present() else 'off'} "
            f"conntrack={_read_number(CONNTRACK_COUNT)} pid={os.getpid()}"
        )[:2000]
        self._record({"MESSAGE": message, "SYSLOG_IDENTIFIER": "jittersim", "PRIORITY": priority},
                     important=True)

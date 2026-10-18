"""Learn service list destinations from the DNS answers clients receive.

The Pi's DNS server answers every lookup from the test Wi-Fi. This watcher
reads those answers from the test interface and tells the privileged helper
which addresses belong to the hostnames of the applied service lists. Matching
by the answers a client actually got covers content networks that hand
different addresses to different resolvers.

Only frames the Pi sent out of the test interface count, so a client cannot
send a forged answer itself. Leaving is not enough on its own: the Pi also
forwards answers that a client asked another DNS server for, and that server
can say anything (security audit 2026-10-18, finding A1). So an answer counts
only when it comes from the Pi's own resolver address, or while household DNS
rules send every test Wi-Fi lookup through the Pi's resolver and drop direct
DNS. Only then can every answer on its way out come from that resolver.
"""

import argparse
import ctypes
import json
import logging
import signal
import socket
import struct
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from jittersim.diagnostic_privacy import configure_logging
from jittersim.localname import GATEWAY_ADDRESS

from .access import read_access
from .helper import HelperClient, HelperClientError

LOGGER = logging.getLogger(__name__)

ETH_P_ALL = 0x0003  # outgoing frames reach only sockets that listen to every protocol
PACKET_HOST = 0  # Linux packet types; the socket module lacks them on other systems
PACKET_OUTGOING = 4
SO_ATTACH_FILTER = 26  # Linux; the socket module does not export it everywhere
MAX_ANSWERS = 128
MAX_QUESTIONS = 4
MAX_NAME_LENGTH = 255
MAX_POINTER_JUMPS = 16
MAX_ADDRESSES_PER_REQUEST = 1024
MAX_PENDING_PER_LIST = 4096
MAX_REMEMBERED_ADDRESSES = 16384
RESEND_SECONDS = 10 * 60
FLUSH_SECONDS = 0.25
PLAN_CHECK_SECONDS = 1.0
LOG_ERROR_EVERY_SECONDS = 60.0
# The Pi's own address on the test Wi-Fi. Its resolver answers from here.
RESOLVER_ADDRESS = IPv4Address(GATEWAY_ADDRESS)
_PRIVATE_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)

# tcpdump -dd 'ip and udp src port 53' for Ethernet frames: IPv4 UDP datagrams
# that are not fragments and come from port 53.
BPF_DNS_RESPONSES = (
    (0x28, 0, 0, 0x0000000C),
    (0x15, 0, 8, 0x00000800),
    (0x30, 0, 0, 0x00000017),
    (0x15, 0, 6, 0x00000011),
    (0x28, 0, 0, 0x00000014),
    (0x45, 4, 0, 0x00001FFF),
    (0xB1, 0, 0, 0x0000000E),
    (0x48, 0, 0, 0x0000000E),
    (0x15, 0, 1, 0x00000035),
    (0x06, 0, 0, 0x00040000),
    (0x06, 0, 0, 0x00000000),
)


@dataclass(frozen=True)
class DnsAnswers:
    """The questions and IPv4 answers of one DNS response."""

    questions: Tuple[str, ...]
    addresses: Tuple[Tuple[str, IPv4Address], ...]


class _Malformed(Exception):
    pass


def _read_name(message: bytes, offset: int) -> Tuple[str, int]:
    """Read a possibly compressed name and return it with the offset after it."""

    labels: List[str] = []
    jumps = 0
    next_offset: Optional[int] = None
    total = 0
    while True:
        if offset >= len(message):
            raise _Malformed
        length = message[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(message):
                raise _Malformed
            pointer = ((length & 0x3F) << 8) | message[offset + 1]
            if next_offset is None:
                next_offset = offset + 2
            jumps += 1
            if jumps > MAX_POINTER_JUMPS or pointer >= len(message):
                raise _Malformed
            offset = pointer
            continue
        if length & 0xC0:
            raise _Malformed
        offset += 1
        if length == 0:
            break
        if offset + length > len(message):
            raise _Malformed
        total += length + 1
        if total > MAX_NAME_LENGTH:
            raise _Malformed
        try:
            labels.append(message[offset : offset + length].decode("ascii").lower())
        except UnicodeDecodeError as error:
            raise _Malformed from error
        offset += length
    return ".".join(labels), (offset if next_offset is None else next_offset)


def parse_dns_message(message: bytes) -> Optional[DnsAnswers]:
    """Read the IPv4 answers of a successful DNS response. Return None for anything else."""

    try:
        if len(message) < 12:
            return None
        _, flags, question_count, answer_count, _, _ = struct.unpack_from("!HHHHHH", message)
        if not flags & 0x8000 or (flags >> 11) & 0xF or flags & 0xF:
            return None
        if question_count > MAX_QUESTIONS:
            return None
        offset = 12
        questions = []
        for _ in range(question_count):
            name, offset = _read_name(message, offset)
            offset += 4
            if offset > len(message):
                return None
            questions.append(name)
        addresses = []
        for _ in range(min(answer_count, MAX_ANSWERS)):
            owner, offset = _read_name(message, offset)
            if offset + 10 > len(message):
                break
            record_type, _, _, length = struct.unpack_from("!HHIH", message, offset)
            offset += 10
            if offset + length > len(message):
                break
            if record_type == 1 and length == 4:
                addresses.append((owner, IPv4Address(message[offset : offset + 4])))
            offset += length
        return DnsAnswers(tuple(questions), tuple(addresses))
    except (_Malformed, struct.error):
        return None


def frame_source(frame: bytes) -> Optional[IPv4Address]:
    """The IPv4 source address of an Ethernet frame, or None for anything else."""

    if len(frame) < 14 + 20 or frame[12:14] != b"\x08\x00" or frame[14] >> 4 != 4:
        return None
    return IPv4Address(frame[26:30])


def parse_dns_frame(frame: bytes) -> Optional[DnsAnswers]:
    """Unwrap an Ethernet frame carrying an unfragmented IPv4 UDP DNS answer."""

    if len(frame) < 14 + 20 + 8 + 12 or frame[12:14] != b"\x08\x00":
        return None
    ip = frame[14:]
    header_length = (ip[0] & 0x0F) * 4
    if ip[0] >> 4 != 4 or header_length < 20 or len(ip) < header_length + 8 or ip[9] != 17:
        return None
    if struct.unpack_from("!H", ip, 6)[0] & 0x3FFF:
        return None
    total = struct.unpack_from("!H", ip, 2)[0]
    datagram = ip[header_length : max(header_length, min(total, len(ip)))]
    if len(datagram) < 8:
        return None
    source_port, _, length = struct.unpack_from("!HHH", datagram)
    if source_port != 53 or length < 8:
        return None
    return parse_dns_message(datagram[8:length])


class ServiceMatcher:
    """Find the service list that owns a hostname or one of its parent names."""

    def __init__(self, lists: Iterable[Tuple[str, Iterable[str]]]) -> None:
        self._owners: Dict[str, str] = {}
        for list_id, domains in lists:
            for domain in domains:
                self._owners.setdefault(domain.lower().rstrip("."), list_id)

    def match(self, name: str) -> Optional[str]:
        labels = name.lower().rstrip(".").split(".")
        for start in range(len(labels)):
            owner = self._owners.get(".".join(labels[start:]))
            if owner is not None:
                return owner
        return None

    def __bool__(self) -> bool:
        return bool(self._owners)


def _shapeable(address: IPv4Address) -> bool:
    """Keep local and special-purpose addresses out of the sets, as the helper does."""

    return not (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
        or any(address in network for network in _PRIVATE_NETWORKS)
    )


class ServiceWatcher:
    """Collect listed destinations from DNS answers and pass them to the helper."""

    def __init__(
        self,
        *,
        helper_client,
        applied_plan: Callable[[], Optional[dict]],
        clock: Callable[[], float] = time.monotonic,
        dns_redirected: Callable[[], bool] = lambda: False,
        resolver_address: IPv4Address = RESOLVER_ADDRESS,
    ) -> None:
        self._helper_client = helper_client
        self._applied_plan = applied_plan
        # Whether every test Wi-Fi lookup goes through the Pi's resolver right now.
        self._dns_redirected = dns_redirected
        self._resolver_address = resolver_address
        self._clock = clock
        self._matcher = ServiceMatcher(())
        self._lists: Tuple = ()
        self._protected: frozenset = frozenset()
        self._pending: Dict[str, "OrderedDict[IPv4Address, None]"] = {}
        self._sent: "OrderedDict[Tuple[str, IPv4Address], float]" = OrderedDict()
        self._last_error_logged = float("-inf")

    def refresh_lists(self) -> None:
        """Follow the lists of the plan the helper applied last."""

        plan = self._applied_plan()
        raw_protected = plan.get("protected_mac_addresses") if isinstance(plan, dict) else None
        protected = set()
        if isinstance(raw_protected, list):
            for mac in raw_protected:
                try:
                    address = bytes.fromhex(mac.replace(":", ""))
                except (AttributeError, ValueError):
                    continue
                if len(address) == 6:
                    protected.add(address)
        self._protected = frozenset(protected)
        rules = plan.get("service_list_rules") if isinstance(plan, dict) else None
        lists = []
        if isinstance(rules, list):
            for rule in rules:
                if (
                    isinstance(rule, dict)
                    and isinstance(rule.get("list_id"), str)
                    and isinstance(rule.get("domains"), list)
                    and all(isinstance(domain, str) for domain in rule["domains"])
                ):
                    lists.append((rule["list_id"], tuple(rule["domains"])))
        lists_tuple = tuple(sorted(lists))
        if lists_tuple == self._lists:
            return
        self._lists = lists_tuple
        self._matcher = ServiceMatcher(lists_tuple)
        known = {list_id for list_id, _ in lists_tuple}
        self._pending = {key: value for key, value in self._pending.items() if key in known}
        for key in [key for key in self._sent if key[0] not in known]:
            del self._sent[key]

    def pending_count(self, list_id: str) -> int:
        return len(self._pending.get(list_id, ()))

    def observe(self, frame: bytes, packet_type: int) -> None:
        """Note the listed addresses in one frame the Pi sent to the test network."""

        if packet_type != PACKET_OUTGOING or not self._matcher:
            return
        if frame[:6] in self._protected:
            return
        if not self._trusted_source(frame):
            return
        parsed = parse_dns_frame(frame)
        if parsed is None or not parsed.addresses:
            return
        asked = next((owner for owner in map(self._matcher.match, parsed.questions) if owner), None)
        now = self._clock()
        for owner, address in parsed.addresses:
            list_id = self._matcher.match(owner) or asked
            if list_id is None or not _shapeable(address):
                continue
            if now - self._sent.get((list_id, address), float("-inf")) < RESEND_SECONDS:
                continue
            pending = self._pending.setdefault(list_id, OrderedDict())
            if address in pending:
                continue
            pending[address] = None
            while len(pending) > MAX_PENDING_PER_LIST:
                pending.popitem(last=False)

    def _trusted_source(self, frame: bytes) -> bool:
        """Whether the Pi's own resolver sent this answer.

        Without household DNS rules a client may ask any DNS server, and the Pi
        forwards that server's answer out of the test interface like its own. With
        them, direct DNS is dropped and a redirected answer leaves with the address
        the client asked, so then every source counts.
        """

        source = frame_source(frame)
        if source is None:
            return False
        if source == self._resolver_address:
            return True
        try:
            return self._dns_redirected() is True
        except Exception:  # noqa: BLE001 - an unreadable policy trusts only the resolver
            return False

    def flush(self) -> bool:
        """Send waiting addresses. Return whether the helper took them."""

        request: Dict[str, List[IPv4Address]] = {}
        for list_id, pending in self._pending.items():
            if pending:
                request[list_id] = list(pending)[:MAX_ADDRESSES_PER_REQUEST]
        if not request:
            return False
        try:
            self._helper_client.service_addresses(request)
        except HelperClientError as error:
            now = self._clock()
            if (
                getattr(error, "reason", None) != "helper-busy"
                and now - self._last_error_logged >= LOG_ERROR_EVERY_SECONDS
            ):
                self._last_error_logged = now
                LOGGER.warning("service addresses could not be sent: %s", error)
            return False
        now = self._clock()
        for list_id, addresses in request.items():
            for address in addresses:
                self._pending[list_id].pop(address, None)
                self._sent[(list_id, address)] = now
                self._sent.move_to_end((list_id, address))
        while len(self._sent) > MAX_REMEMBERED_ADDRESSES:
            self._sent.popitem(last=False)
        return True


def open_dns_socket(interface: str) -> socket.socket:
    """Open a packet socket that the kernel filters down to DNS answers."""

    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    try:
        program = b"".join(struct.pack("HBBI", *instruction) for instruction in BPF_DNS_RESPONSES)
        buffer = ctypes.create_string_buffer(program)
        sock.setsockopt(
            socket.SOL_SOCKET,
            SO_ATTACH_FILTER,
            struct.pack("HL", len(BPF_DNS_RESPONSES), ctypes.addressof(buffer)),
        )
        sock.bind((interface, ETH_P_ALL))
        sock.settimeout(FLUSH_SECONDS)
    except BaseException:
        sock.close()
        raise
    return sock


def watch(
    watcher: ServiceWatcher,
    open_socket: Callable[[], socket.socket],
    stop: threading.Event,
    *,
    clock: Callable[[], float] = time.monotonic,
    retry_seconds: float = 2.0,
) -> None:
    """Read DNS answers until told to stop and reopen the socket if the interface restarts."""

    while not stop.is_set():
        try:
            sock = open_socket()
        except OSError as error:
            LOGGER.warning("could not watch DNS answers: %s", error)
            stop.wait(retry_seconds)
            continue
        next_flush = next_plan_check = clock()
        try:
            while not stop.is_set():
                try:
                    frame, address = sock.recvfrom(4096)
                except socket.timeout:
                    pass
                else:
                    watcher.observe(frame, address[2])
                now = clock()
                if now >= next_plan_check:
                    watcher.refresh_lists()
                    next_plan_check = now + PLAN_CHECK_SECONDS
                if now >= next_flush:
                    watcher.flush()
                    next_flush = now + FLUSH_SECONDS
        except OSError as error:
            LOGGER.warning("DNS watching stopped: %s", error)
            stop.wait(retry_seconds)
        finally:
            sock.close()


class _RedirectFile:
    """Read whether household DNS rules are on, again only when the file changed."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._generation: Optional[tuple] = None
        self._redirected = False

    def __call__(self) -> bool:
        try:
            metadata = self._path.stat()
            generation = (metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns)
            if generation != self._generation:
                self._redirected = False
                self._generation = generation
                self._redirected = read_access(self._path).has_dns_rules is True
        except FileNotFoundError:
            self._redirected, self._generation = False, None
        except (OSError, ValueError, TypeError, KeyError):
            # Not readable: trust only the Pi's own resolver until the file changes.
            self._redirected = False
        return self._redirected


class _PlanFile:
    """Read the published plan again only when the helper replaced it."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._generation: Optional[tuple] = None
        self._payload: Optional[dict] = None

    def __call__(self) -> Optional[dict]:
        try:
            metadata = self._path.stat()
            generation = (metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns)
            if generation != self._generation:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
                self._payload = payload if isinstance(payload, dict) else None
                self._generation = generation
        except FileNotFoundError:
            self._payload = self._generation = None
        except (OSError, ValueError):
            self._payload = self._generation = None
        return self._payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Learn service list addresses from DNS answers.")
    parser.add_argument("--interface", default="wlan0")
    parser.add_argument("--helper-socket", type=Path, default=Path("/run/jittersim/helper.sock"))
    parser.add_argument("--plan", type=Path, default=Path("/run/jittersim/active-plan.json"))
    parser.add_argument("--access-policy", type=Path,
                        default=Path("/run/jittersim/access-policy.json"))
    arguments = parser.parse_args(argv)
    configure_logging(level=logging.INFO)
    stop = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stop.set())
    watcher = ServiceWatcher(
        helper_client=HelperClient(socket_path=arguments.helper_socket, timeout_seconds=3),
        applied_plan=_PlanFile(arguments.plan),
        dns_redirected=_RedirectFile(arguments.access_policy),
    )
    LOGGER.info("watching DNS answers on %s", arguments.interface)
    watch(watcher, lambda: open_dns_socket(arguments.interface), stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

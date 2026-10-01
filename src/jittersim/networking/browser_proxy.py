"""Bounded HTTPS CONNECT proxy for managed test browsers."""

import argparse
import json
import logging
import os
import socket
import socketserver
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from ipaddress import IPv4Address, ip_address, ip_network
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

from jittersim.config.exemptions import normalize_exempt_mac
from jittersim.config.models import ValidationError, normalize_domain
from jittersim.engine.codec import PlanCodecError, plan_from_payload
from jittersim.engine.plan import NetworkPlan

LOGGER = logging.getLogger(__name__)
MAX_PLAN_BYTES = 256 * 1024
MAX_HEADER_BYTES = 8 * 1024
CONNECT_TIMEOUT_SECONDS = 10
RESOLVE_TIMEOUT_SECONDS = 5
IDLE_TIMEOUT_SECONDS = 300
COPY_BYTES = 64 * 1024


class ProxyError(RuntimeError):
    """Raised when a browser tunnel cannot be accepted safely."""


def _read_active_plan(path: Path) -> Tuple[Optional[NetworkPlan], Optional[tuple]]:
    """Read one atomically published helper plan and its file generation."""

    try:
        with path.open("rb") as active_file:
            metadata = os.fstat(active_file.fileno())
            generation = (metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns)
            payload = active_file.read(MAX_PLAN_BYTES + 1)
    except FileNotFoundError:
        return None, None
    if len(payload) > MAX_PLAN_BYTES:
        raise ProxyError("active plan is too large")
    try:
        return plan_from_payload(json.loads(payload)), generation
    except (UnicodeDecodeError, json.JSONDecodeError, PlanCodecError) as error:
        raise ProxyError("active plan is invalid") from error


def load_active_plan(path: Path) -> Optional[NetworkPlan]:
    """Read the active helper plan for diagnostics and tests."""

    return _read_active_plan(path)[0]


def _plan_generation(path: Path) -> Optional[tuple]:
    try:
        metadata = path.stat()
    except FileNotFoundError:
        return None
    return metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns


def select_mark(
    plan: Optional[NetworkPlan],
    *,
    client_ip: IPv4Address,
    host: str,
    client_mac: Optional[str] = None,
) -> Tuple[int, str]:
    """Choose the service list or hostname assignment for this host, else the device's mark."""

    if plan is None:
        return 0, "reset"
    if plan.protected_mac_addresses or plan.general_profile is not None:
        if client_mac is None:
            raise ProxyError("current Wi-Fi identity could not be verified")
        if client_mac in plan.mac_policy.denylist or (
            plan.mac_policy.mode == "allowlist" and client_mac not in plan.mac_policy.allowlist
        ):
            raise ProxyError("client is blocked by global access rules")
        if client_mac in plan.protected_mac_addresses:
            return 0, "unaffected"
    # A listed service replaces whatever profile behavior the client would get,
    # including a random outage, exactly like the network rules for direct traffic.
    for service_list in plan.service_list_rules:
        if any(host == domain or host.endswith("." + domain) for domain in service_list.domains):
            return service_list.mark, f"service-list:{service_list.list_id}"
    if plan.general_profile is not None:
        if plan.general_profile.outage_active:
            raise ProxyError("the general profile is temporarily disconnected")
        return 1, "general-profile"
    base_rule = next((rule for rule in plan.mark_rules if rule.ipv4_address == client_ip), None)
    if base_rule is None:
        raise ProxyError("client does not have an assigned device")
    if client_ip in plan.blocked_device_addresses:
        raise ProxyError("client is temporarily disconnected")
    for assignment in plan.service_assignment_rules:
        if assignment.device_ip != client_ip:
            continue
        if any(host == domain or host.endswith("." + domain) for domain in assignment.domains):
            return assignment.mark, assignment.assignment_id
    return base_rule.mark, "device"


def destination_allowed(
    plan: Optional[NetworkPlan],
    client_ip: IPv4Address,
    address: str,
    client_mac: Optional[str] = None,
) -> bool:
    """Preserve existing address blocks for traffic terminating on the proxy."""

    if plan is None:
        return True
    destination = ip_address(address)
    if destination in plan.global_blocked_addresses:
        return False
    if (
        plan.global_allowed_addresses is not None
        and destination not in plan.global_allowed_addresses
    ):
        return False
    if client_mac in plan.protected_mac_addresses:
        return True
    if plan.general_profile is not None:
        general = plan.general_profile
        return not general.outage_active and (
            general.allowed_addresses is None or destination in general.allowed_addresses
        )
    for website_filter in plan.website_filters:
        if website_filter.device_ip != client_ip:
            continue
        if destination.version != 4 and website_filter.allowed_addresses is not None:
            return False
        if destination in website_filter.blocked_addresses:
            return False
        if (
            website_filter.allowed_addresses is not None
            and destination not in website_filter.allowed_addresses
        ):
            return False
    return True


def _read_connect_request(client: socket.socket) -> Tuple[str, bytes]:
    client.settimeout(CONNECT_TIMEOUT_SECONDS)
    received = bytearray()
    while b"\r\n\r\n" not in received:
        chunk = client.recv(4096)
        if not chunk:
            raise ProxyError("browser closed before sending a request")
        received.extend(chunk)
        if len(received) > MAX_HEADER_BYTES + COPY_BYTES:
            raise ProxyError("browser request is too large")
    header, first_bytes = bytes(received).split(b"\r\n\r\n", 1)
    if len(header) > MAX_HEADER_BYTES:
        raise ProxyError("browser request headers are too large")
    try:
        method, authority, version = header.split(b"\r\n", 1)[0].decode("ascii").split()
        host_text, port_text = authority.rsplit(":", 1)
        host = normalize_domain(host_text)
        port = int(port_text)
    except (UnicodeDecodeError, ValueError, ValidationError) as error:
        raise ProxyError("browser request is not a valid CONNECT request") from error
    if method != "CONNECT" or version not in {"HTTP/1.0", "HTTP/1.1"} or port != 443:
        raise ProxyError("only HTTPS CONNECT to port 443 is supported")
    return host, first_bytes


def _resolve_public_addresses(host: str):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    public_addresses = []
    for family, socktype, protocol, _, address in addresses:
        if ip_address(address[0]).is_global:
            public_addresses.append((family, socktype, protocol, address))
        if len(public_addresses) == 8:
            break
    return public_addresses


def _set_socket_mark(connection: socket.socket, mark: int) -> None:
    if mark:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, mark)


def _copy_stream(
    source: socket.socket,
    destination: socket.socket,
    plan_path: Path,
    generation: Optional[tuple],
    allow_plan_change: Optional[Callable[[], bool]] = None,
) -> None:
    try:
        while True:
            chunk = source.recv(COPY_BYTES)
            if not chunk:
                break
            current_generation = _plan_generation(plan_path)
            if current_generation != generation:
                if allow_plan_change is None or not allow_plan_change():
                    break
                generation = current_generation
            destination.sendall(chunk)
    except (OSError, socket.timeout):
        pass
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class _ProxyHandler(socketserver.BaseRequestHandler):
    server: "BrowserProxyServer"

    def handle(self) -> None:
        client = self.request
        tunnel_started = False
        try:
            peer_ip = IPv4Address(self.client_address[0])
            if peer_ip not in self.server.client_network:
                raise ProxyError("client is outside the test network")
            client_ip = self.server.identify_client(peer_ip)
            host, first_bytes = _read_connect_request(client)
            plan, generation = _read_active_plan(self.server.active_plan_path)
            client_mac = (
                self.server.identify_mac(peer_ip)
                if plan and (plan.protected_mac_addresses or plan.general_profile is not None) else None
            )
            mark, assignment_id = select_mark(
                plan, client_ip=client_ip, host=host, client_mac=client_mac
            )
            try:
                addresses = self.server.resolver.submit(self.server.resolve_addresses, host).result(
                    timeout=RESOLVE_TIMEOUT_SECONDS
                )
            except FutureTimeout as error:
                raise ProxyError("destination lookup timed out") from error
            if not addresses:
                raise ProxyError("destination has no public address")
            upstream = None
            for family, socktype, protocol, address in addresses:
                if not destination_allowed(plan, client_ip, address[0], client_mac):
                    continue
                candidate = socket.socket(family, socktype, protocol)
                try:
                    candidate.settimeout(CONNECT_TIMEOUT_SECONDS)
                    self.server.mark_socket(candidate, mark)
                except OSError as error:
                    candidate.close()
                    raise ProxyError("network queue marking is unavailable") from error
                try:
                    candidate.connect(address)
                except OSError:
                    candidate.close()
                    continue
                upstream = candidate
                break
            if upstream is None:
                raise ProxyError("destination connection failed")
            with upstream:

                def keep_protected_tunnel() -> bool:
                    try:
                        current_plan, _ = _read_active_plan(self.server.active_plan_path)
                        current_mac = self.server.identify_mac(peer_ip)
                        return (
                            assignment_id == "unaffected"
                            and current_plan is not None
                            and current_mac == client_mac
                            and select_mark(
                                current_plan, client_ip=client_ip, host=host, client_mac=current_mac
                            )[1]
                            == "unaffected"
                            and destination_allowed(
                                current_plan, client_ip, upstream.getpeername()[0], current_mac
                            )
                        )
                    except (OSError, ProxyError, ValueError):
                        return False

                allow_change = keep_protected_tunnel if assignment_id == "unaffected" else None
                if _plan_generation(self.server.active_plan_path) != generation and not (
                    allow_change and allow_change()
                ):
                    raise ProxyError("network plan changed while opening the tunnel")
                self.server.mark_socket(client, mark)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                tunnel_started = True
                client.settimeout(IDLE_TIMEOUT_SECONDS)
                upstream.settimeout(IDLE_TIMEOUT_SECONDS)
                if first_bytes:
                    upstream.sendall(first_bytes)
                upload = threading.Thread(
                    target=_copy_stream,
                    args=(client, upstream, self.server.active_plan_path, generation, allow_change),
                    daemon=True,
                )
                upload.start()
                _copy_stream(
                    upstream, client, self.server.active_plan_path, generation, allow_change
                )
                upload.join(timeout=1)
            LOGGER.debug("browser tunnel closed for %s", assignment_id)
        except (OSError, ProxyError, ValueError) as error:
            LOGGER.warning("browser tunnel rejected: %s", error)
            if not tunnel_started:
                try:
                    client.sendall(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n")
                except OSError:
                    pass


class BrowserProxyServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Serve a fixed number of browser tunnels from the isolated test network."""

    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 32

    def __init__(
        self,
        address: Tuple[str, int],
        *,
        active_plan_path: Path,
        client_network: str = "192.168.50.0/24",
        mark_socket: Callable[[socket.socket, int], None] = _set_socket_mark,
        resolve_addresses: Callable[[str], list] = _resolve_public_addresses,
        identify_client: Callable[[IPv4Address], IPv4Address] = lambda address: address,
        identify_mac: Optional[Callable[[IPv4Address], Optional[str]]] = None,
    ) -> None:
        self.active_plan_path = active_plan_path
        self.client_network = ip_network(client_network, strict=True)
        if self.client_network.version != 4:
            raise ValueError("browser proxy client network must use IPv4")
        if ip_address(address[0]) not in self.client_network:
            raise ValueError("browser proxy must listen on the test network")
        self.mark_socket = mark_socket
        self.resolve_addresses = resolve_addresses
        self.identify_client = identify_client
        self.identify_mac = identify_mac or _live_client_mac
        self.resolver = ThreadPoolExecutor(max_workers=8, thread_name_prefix="proxy-dns")
        self.connections = threading.BoundedSemaphore(32)
        super().__init__(address, _ProxyHandler)

    def process_request(self, request, client_address) -> None:
        if not self.connections.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connections.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()

    def server_close(self) -> None:
        super().server_close()
        self.resolver.shutdown(wait=False, cancel_futures=True)


def _live_client_mac(address: IPv4Address) -> Optional[str]:
    from .helper import HelperClient, HelperClientError

    try:
        snapshot = HelperClient(
            socket_path=Path("/run/jittersim/helper.sock"), timeout_seconds=3
        ).connected_device_snapshot()
        matches = [
            client["mac_address"]
            for client in snapshot["devices"]
            if snapshot["association_status_available"]
            and client["association_verified"]
            and client["ipv4_address"] == str(address)
        ]
        return normalize_exempt_mac(matches[0]) if len(matches) == 1 else None
    except (HelperClientError, ValueError, OSError):
        return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the managed browser proxy.")
    parser.add_argument("--host", default="192.168.50.1")
    parser.add_argument("--port", type=int, default=3128)
    parser.add_argument("--plan", type=Path, default=Path("/run/jittersim/active-plan.json"))
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            _set_socket_mark(probe, 1)
    except (AttributeError, OSError) as error:
        LOGGER.error("socket marking is unavailable: %s", error)
        return 1
    with BrowserProxyServer((arguments.host, arguments.port), active_plan_path=arguments.plan) as proxy:
        try:
            proxy.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0

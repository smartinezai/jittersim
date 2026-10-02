import json
import socket
import socketserver
import tempfile
import threading
import unittest
from dataclasses import replace
from ipaddress import IPv4Address
from pathlib import Path

from jittersim.config.models import Device, Impairment, MacPolicy, Profile, SiteBlock
from jittersim.engine.access import AccessPlan
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import GeneralProfileRule, ServiceAssignmentRule, WebsiteFilter
from jittersim.networking.access import atomic_write
from jittersim.networking.browser_proxy import (
    BrowserProxyServer,
    ProxyError,
    destination_allowed,
    select_mark,
)


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while data := self.request.recv(4096):
            self.request.sendall(data)


class BrowserProxyTests(unittest.TestCase):
    def test_general_profile_marks_both_sockets_for_unassigned_clients(self):
        protected_mac = "02:00:00:00:00:01"
        address = IPv4Address("192.168.50.99")
        profile = Profile("slow", "Slow", download=Impairment(bandwidth_kbit=1000))
        plan = compile_plan(
            devices=(), profiles={profile.id: profile},
            protected_mac_addresses=(protected_mac,),
            general_profile=GeneralProfileRule(profile.id),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "active.json"
            path.write_text(json.dumps(plan_to_payload(plan)))
            echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
            echo.daemon_threads = True
            threading.Thread(target=echo.serve_forever, daemon=True).start()
            marks = []
            current_mac = ["02:00:00:00:00:02"]
            proxy = BrowserProxyServer(
                ("127.0.0.1", 0), active_plan_path=path, client_network="127.0.0.0/8",
                mark_socket=lambda _socket, mark: marks.append(mark),
                identify_client=lambda _ip: address,
                identify_mac=lambda _ip: current_mac[0],
                resolve_addresses=lambda _host: [
                    (socket.AF_INET, socket.SOCK_STREAM, 0, echo.server_address)
                ],
            )
            threading.Thread(target=proxy.serve_forever, daemon=True).start()
            try:
                for mac, expected_mark in ((current_mac[0], 1), (protected_mac, 0)):
                    current_mac[0] = mac
                    marks.clear()
                    with socket.create_connection(proxy.server_address, timeout=3) as client:
                        client.sendall(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
                        self.assertIn(b"200 Connection Established", client.recv(4096))
                        client.sendall(b"general-profile-body")
                        self.assertEqual(client.recv(4096), b"general-profile-body")
                        self.assertEqual(marks, [expected_mark, expected_mark])
            finally:
                proxy.shutdown()
                proxy.server_close()
                echo.shutdown()
                echo.server_close()

    def test_unaffected_tunnel_survives_other_profile_changes_and_rechecks_global_rules(self):
        mac = "02:00:00:00:00:01"
        address = IPv4Address("192.168.50.10")
        # A stale IP assignment must not apply its profile to this verified MAC.
        device = Device("bob", "Bob", address, "slow", "02:00:00:00:00:02")
        plan = compile_plan(
            devices=(device,),
            profiles={"slow": Profile("slow", "Slow", download=Impairment(loss_percent=100))},
            protected_mac_addresses=(mac,),
            website_filters=(WebsiteFilter(address, (IPv4Address("8.8.8.8"),)),),
        )
        self.assertEqual(
            select_mark(plan, client_ip=address, host="example.com", client_mac=mac),
            (0, "unaffected"),
        )
        self.assertTrue(destination_allowed(plan, address, "127.0.0.1", mac))
        with self.assertRaises(ProxyError):
            select_mark(plan, client_ip=address, host="example.com")
        with self.assertRaises(ProxyError):
            select_mark(
                replace(plan, mac_policy=MacPolicy(mode="denylist", denylist=(mac,))),
                client_ip=address,
                host="example.com",
                client_mac=mac,
            )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "active.json"

            def publish(current):
                temporary = path.with_suffix(".next")
                temporary.write_text(json.dumps(plan_to_payload(current)))
                temporary.replace(path)

            publish(plan)
            echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
            echo.daemon_threads = True
            threading.Thread(target=echo.serve_forever, daemon=True).start()
            marks = []
            proxy = BrowserProxyServer(
                ("127.0.0.1", 0),
                active_plan_path=path,
                client_network="127.0.0.0/8",
                mark_socket=lambda _socket, mark: marks.append(mark),
                identify_client=lambda _ip: address,
                identify_mac=lambda _ip: mac,
                resolve_addresses=lambda _host: [
                    (socket.AF_INET, socket.SOCK_STREAM, 0, echo.server_address)
                ],
            )
            threading.Thread(target=proxy.serve_forever, daemon=True).start()
            try:
                for change in (
                    replace(
                        plan,
                        queues=tuple(
                            replace(queue, impairment=Impairment(delay_ms=800))
                            for queue in plan.queues
                        ),
                    ),
                    replace(plan, protected_mac_addresses=()),
                    replace(plan, global_blocked_addresses=(IPv4Address("127.0.0.1"),)),
                ):
                    publish(plan)
                    with socket.create_connection(proxy.server_address, timeout=3) as client:
                        client.sendall(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
                        self.assertIn(b"200 Connection Established", client.recv(4096))
                        self.assertEqual(marks[-2:], [0, 0])
                        publish(change)
                        client.sendall(b"after-plan-change")
                        if change.protected_mac_addresses and not change.global_blocked_addresses:
                            self.assertEqual(client.recv(4096), b"after-plan-change")
                        else:
                            self.assertEqual(client.recv(4096), b"")
            finally:
                proxy.shutdown()
                proxy.server_close()
                echo.shutdown()
                echo.server_close()

    def test_existing_blocks_still_apply_to_proxied_traffic(self) -> None:
        device = Device("alice", "Alice", "192.168.50.10", "normal")
        plan = compile_plan(
            devices=(device,),
            profiles={"normal": Profile(id="normal", name="Normal")},
            global_blocked_addresses=(IPv4Address("8.8.8.8"),),
            website_filters=(
                WebsiteFilter(
                    device_ip=device.ipv4_address,
                    allowed_addresses=(IPv4Address("9.9.9.9"),),
                ),
            ),
            blocked_device_addresses=(device.ipv4_address,),
        )
        with self.assertRaises(ProxyError):
            select_mark(plan, client_ip=device.ipv4_address, host="www.youtube.com")
        self.assertFalse(destination_allowed(plan, device.ipv4_address, "8.8.8.8"))
        self.assertFalse(destination_allowed(plan, device.ipv4_address, "1.1.1.1"))
        self.assertTrue(destination_allowed(plan, device.ipv4_address, "9.9.9.9"))

    def test_blocked_sites_without_an_allowlist_keep_other_sites_reachable(self) -> None:
        device = Device("alice", "Alice", "192.168.50.10", "normal")

        def plan_with(allowed_addresses):
            return compile_plan(
                devices=(device,),
                profiles={"normal": Profile(id="normal", name="Normal")},
                website_filters=(
                    WebsiteFilter(
                        device_ip=device.ipv4_address,
                        allowed_addresses=allowed_addresses,
                        blocked_addresses=(IPv4Address("203.0.113.9"),),
                    ),
                ),
            )

        no_allowlist = plan_with(None)
        empty_allowlist = plan_with(())

        self.assertFalse(destination_allowed(no_allowlist, device.ipv4_address, "203.0.113.9"))
        self.assertTrue(destination_allowed(no_allowlist, device.ipv4_address, "198.51.100.7"))
        # An allowlist whose domains resolved to nothing still allows nothing.
        self.assertFalse(
            destination_allowed(empty_allowlist, device.ipv4_address, "198.51.100.7")
        )

    def test_hostnames_on_the_same_address_get_different_marks(self) -> None:
        device = Device("alice", "Alice", "192.168.50.10", "normal")
        plan = compile_plan(
            devices=(device,),
            profiles={"normal": Profile(id="normal", name="Normal")},
            service_assignment_rules=(
                ServiceAssignmentRule(
                    assignment_id="video",
                    device_id="alice",
                    device_ip=device.ipv4_address,
                    domains=("youtube.com",),
                    mark=0,
                    upload_impairment=Impairment(delay_ms=200),
                    download_impairment=Impairment(delay_ms=200),
                ),
            ),
        )
        self.assertEqual(
            select_mark(plan, client_ip=device.ipv4_address, host="www.youtube.com"),
            (2, "video"),
        )
        self.assertEqual(
            select_mark(plan, client_ip=device.ipv4_address, host="notyoutube.com"),
            (1, "device"),
        )

        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "active-plan.json"
            plan_path.write_text(json.dumps(plan_to_payload(plan)))
            access_path = Path(directory) / "access-policy.json"
            atomic_write(access_path, json.dumps(AccessPlan().payload()))
            echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
            echo.daemon_threads = True
            echo_thread = threading.Thread(target=echo.serve_forever, daemon=True)
            echo_thread.start()
            marks = []
            proxy = BrowserProxyServer(
                ("127.0.0.1", 0),
                active_plan_path=plan_path,
                require_access_policy=True,
                client_network="127.0.0.0/8",
                mark_socket=lambda _connection, mark: marks.append(mark),
                identify_client=lambda _address: device.ipv4_address,
                resolve_addresses=lambda _host: [
                    (socket.AF_INET, socket.SOCK_STREAM, 0, ("127.0.0.1", echo.server_address[1]))
                ],
            )
            proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
            proxy_thread.start()
            try:
                for host, expected_mark in (("www.youtube.com", 2), ("other.example", 1)):
                    with self.subTest(host=host):
                        with socket.create_connection(proxy.server_address, timeout=3) as client:
                            client.sendall(
                                f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()
                            )
                            self.assertIn(b"200 Connection Established", client.recv(4096))
                            client.sendall(b"test-data")
                            self.assertEqual(client.recv(4096), b"test-data")
                        self.assertEqual(marks[-2:], [expected_mark, expected_mark])
                with socket.create_connection(proxy.server_address, timeout=3) as client:
                    client.settimeout(3)
                    client.sendall(b"CONNECT other.example:443 HTTP/1.1\r\n\r\n")
                    self.assertIn(b"200 Connection Established", client.recv(4096))
                    replacement = plan_path.with_suffix(".next")
                    replacement.write_text(json.dumps(plan_to_payload(plan)))
                    replacement.replace(plan_path)
                    client.sendall(b"stale-tunnel")
                    self.assertEqual(client.recv(4096), b"")
                with socket.create_connection(proxy.server_address, timeout=3) as client:
                    client.sendall(b"CONNECT other.example:443 HTTP/1.1\r\n\r\n")
                    self.assertIn(b"200 Connection Established", client.recv(4096))
                    blocked = AccessPlan(blocks=(SiteBlock("blocked", "Blocked", ("other.example",)),))
                    atomic_write(access_path, json.dumps(blocked.payload()))
                    client.sendall(b"access-changed")
                    self.assertEqual(client.recv(4096), b"")
                for host in ("other.example", "cdn.other.example"):
                    with socket.create_connection(proxy.server_address, timeout=3) as client:
                        client.sendall(f"CONNECT {host}:443 HTTP/1.1\r\n\r\n".encode())
                        self.assertIn(b"503", client.recv(4096))
                access_path.unlink()
                with socket.create_connection(proxy.server_address, timeout=3) as client:
                    client.sendall(b"CONNECT missing-policy.example:443 HTTP/1.1\r\n\r\n")
                    self.assertIn(b"503", client.recv(4096))
            finally:
                proxy.shutdown()
                proxy.server_close()
                echo.shutdown()
                echo.server_close()


if __name__ == "__main__":
    unittest.main()

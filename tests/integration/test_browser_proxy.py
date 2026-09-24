import json
import socket
import socketserver
import tempfile
import threading
import unittest
from ipaddress import IPv4Address
from pathlib import Path

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import ServiceAssignmentRule, WebsiteFilter
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
            echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
            echo.daemon_threads = True
            echo_thread = threading.Thread(target=echo.serve_forever, daemon=True)
            echo_thread.start()
            marks = []
            proxy = BrowserProxyServer(
                ("127.0.0.1", 0),
                active_plan_path=plan_path,
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
            finally:
                proxy.shutdown()
                proxy.server_close()
                echo.shutdown()
                echo.server_close()


if __name__ == "__main__":
    unittest.main()

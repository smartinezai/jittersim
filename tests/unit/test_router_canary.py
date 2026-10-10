import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path

from jittersim import router_canary
from jittersim.router_canary import RouterCanary, arp_state, default_gateway, dns_state

ROUTES = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "wlan0\t0032A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\t0\t0\t0\n"
    "wlan0\t00000000\t0132A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
    "eth0\t00000000\t0100A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
)
ARP = (
    "IP address       HW type     Flags       HW address            Mask     Device\n"
    "192.168.0.1      0x1         0x2         aa:bb:cc:dd:ee:ff     *        eth0\n"
    "192.168.50.10    0x1         0x0         00:00:00:00:00:00     *        wlan0\n"
)


class FileTests(unittest.TestCase):
    def write(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "table"
        path.write_text(text)
        return path

    def test_the_default_route_with_the_lowest_metric_wins(self) -> None:
        self.assertEqual(default_gateway(self.write(ROUTES)), ("eth0", "192.168.0.1"))

    def test_no_default_route(self) -> None:
        self.assertIsNone(default_gateway(self.write(ROUTES.splitlines()[0] + "\n")))
        self.assertIsNone(default_gateway(Path("/nonexistent/route")))

    def test_arp_entries(self) -> None:
        table = self.write(ARP)
        self.assertEqual(arp_state("192.168.0.1", "eth0", table), "reachable")
        self.assertEqual(arp_state("192.168.50.10", "wlan0", table), "incomplete")
        self.assertEqual(arp_state("192.168.0.9", "eth0", table), "missing")


class DnsTests(unittest.TestCase):
    def test_an_answer_with_the_same_id_counts(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        self.addCleanup(server.close)
        port = server.getsockname()[1]

        def answer():
            query, client = server.recvfrom(512)
            server.sendto(b"\x00\x00" + query[:12][2:], client)  # wrong id first
            server.sendto(query[:2] + struct.pack("!H", 0x8183) + query[4:], client)

        threading.Thread(target=answer, daemon=True).start()

        class Socket(socket.socket):
            def sendto(self, data, address):
                return super().sendto(data, ("127.0.0.1", port))

        self.assertEqual(dns_state("127.0.0.1", "lo", connect=Socket), "answered-rcode-3")

    def test_no_answer_times_out(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        self.addCleanup(server.close)
        port = server.getsockname()[1]

        class Socket(socket.socket):
            def sendto(self, data, address):
                return super().sendto(data, ("127.0.0.1", port))

        original = router_canary.DNS_TIMEOUT_SECONDS
        router_canary.DNS_TIMEOUT_SECONDS = 0.2
        self.addCleanup(setattr, router_canary, "DNS_TIMEOUT_SECONDS", original)
        self.assertEqual(dns_state("127.0.0.1", "lo", connect=Socket), "timeout")


class CanaryTests(unittest.TestCase):
    def canary(self, outcomes):
        self.now = 0.0
        self.records = []
        self.outcomes = list(outcomes)

        def ping(gateway, interface):
            return self.outcomes[0][0]

        def dns(gateway, interface):
            return self.outcomes.pop(0)[1]

        return RouterCanary(
            profiles=lambda: ["daily climb manual", "daily climb manual", ""],
            clock=lambda: self.now,
            gateway=lambda: ("eth0", "192.168.0.1"),
            arp=lambda gateway, interface: "reachable",
            ping=ping,
            dns=dns,
            record=lambda fields, important: self.records.append((fields, important)) or True,
        )

    def run_rounds(self, canary, count, step=60.0):
        for _ in range(count):
            canary.check_once()
            self.now += step

    def test_one_lost_ping_is_not_an_outage(self) -> None:
        canary = self.canary([("answered", "answered-rcode-0"), ("no-reply", "answered-rcode-0"),
                              ("answered", "answered-rcode-0")])
        self.run_rounds(canary, 3)
        self.assertEqual(self.records, [])

    def test_two_failures_record_the_profile_and_state_then_the_recovery(self) -> None:
        good, bad = ("answered", "answered-rcode-0"), ("no-reply", "timeout")
        canary = self.canary([good, bad, bad, bad, good])
        self.run_rounds(canary, 5)

        self.assertEqual(len(self.records), 2)
        failed, important = self.records[0]
        self.assertTrue(important)
        self.assertEqual(failed["SYSLOG_IDENTIFIER"], "jittersim")
        self.assertEqual(failed["PRIORITY"], "4")
        self.assertIn("router health check failed: route=present arp=reachable ping=no-reply "
                      "dns=timeout profiles=[daily climb manual]", failed["MESSAGE"])
        self.assertIn("upload_corruption=", failed["MESSAGE"])
        self.assertIn("conntrack=", failed["MESSAGE"])
        self.assertNotIn("192.168", failed["MESSAGE"])
        recovered = self.records[1][0]
        self.assertIn("passes again after about 120 s", recovered["MESSAGE"])
        self.assertEqual(recovered["PRIORITY"], "5")

    def test_a_long_outage_repeats_every_ten_minutes(self) -> None:
        good, bad = ("answered", "answered-rcode-0"), ("no-reply", "timeout")
        canary = self.canary([good] + [bad] * 23)
        self.run_rounds(canary, 24)
        self.assertEqual(len(self.records), 3)
        self.assertIn("still failing", self.records[1][0]["MESSAGE"])

    def test_a_router_that_never_answers_pings_or_dns_is_not_down(self) -> None:
        canary = self.canary([("no-reply", "timeout")] * 5)
        self.run_rounds(canary, 5)
        self.assertEqual(self.records, [])

    def test_a_missing_default_route_counts(self) -> None:
        canary = self.canary([])
        canary._gateway = lambda: None
        self.run_rounds(canary, 2)
        self.assertEqual(len(self.records), 1)
        self.assertIn("route=missing", self.records[0][0]["MESSAGE"])

    def test_it_starts_and_stops_without_holding_up_a_shutdown(self) -> None:
        canary = RouterCanary(interval_seconds=3600)
        canary.start()
        canary.stop(timeout=5)
        canary.start()
        self.assertIsNone(canary._thread)


if __name__ == "__main__":
    unittest.main()


class SchedulerWiringTests(unittest.TestCase):
    def test_the_scheduler_owns_a_canary_that_names_the_applied_profiles(self) -> None:
        from jittersim.engine.activity import Source, TargetState
        from jittersim.engine.runtime import RuntimeScheduler

        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=object())
        self.assertIsInstance(scheduler._router_canary, RouterCanary)
        scheduler._applied_states = {
            "a": TargetState("A", Source("profile", "p1", profile="daily climb manual")),
            "b": TargetState("B", Source("none", "none")),
        }
        self.assertEqual(scheduler._active_profile_names(), ["daily climb manual"])
        self.assertIsNone(
            RuntimeScheduler(database_path=":memory:", helper_client=None)._router_canary)

import random
import socket
import struct
import threading
import unittest
from ipaddress import IPv4Address
from unittest.mock import patch

from jittersim.config.models import Impairment, Profile
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import ServiceListRule
from jittersim.networking import dns_watch
from jittersim.networking.dns_watch import (
    BPF_DNS_RESPONSES,
    MAX_PENDING_PER_LIST,
    PACKET_HOST,
    PACKET_OUTGOING,
    RESEND_SECONDS,
    ServiceMatcher,
    ServiceWatcher,
    parse_dns_frame,
    parse_dns_message,
    watch,
)
from jittersim.networking.helper import HelperClientError

OUTGOING = PACKET_OUTGOING
INCOMING = PACKET_HOST


def encode_name(value: str) -> bytes:
    return b"".join(bytes([len(label)]) + label.encode() for label in value.split(".")) + b"\x00"


def a_record(owner, address, ttl=60):
    owner_bytes = owner if isinstance(owner, bytes) else encode_name(owner)
    return owner_bytes + struct.pack("!HHIH", 1, 1, ttl, 4) + socket.inet_aton(address)


def cname_record(owner, target):
    owner_bytes = owner if isinstance(owner, bytes) else encode_name(owner)
    target_bytes = target if isinstance(target, bytes) else encode_name(target)
    return owner_bytes + struct.pack("!HHIH", 5, 1, 60, len(target_bytes)) + target_bytes


def aaaa_record(owner):
    owner_bytes = owner if isinstance(owner, bytes) else encode_name(owner)
    return owner_bytes + struct.pack("!HHIH", 28, 1, 60, 16) + bytes(15) + b"\x01"


def dns_message(qname, records, flags=0x8180, questions=1):
    header = struct.pack("!HHHHHH", 0x1234, flags, questions, len(records), 0, 0)
    return header + encode_name(qname) + struct.pack("!HH", 1, 1) + b"".join(records)


def frame_for(
    message,
    *,
    sport=53,
    protocol=17,
    fragment=0,
    options=b"",
    ethertype=0x0800,
    dst_mac=b"\x02" * 6,
    src_mac=b"\x04" * 6,
    source="192.168.50.1",
):
    udp = struct.pack("!HHHH", sport, 40000, 8 + len(message), 0) + message
    ihl = 5 + len(options) // 4
    ip = struct.pack(
        "!BBHHHBBH4s4s",
        0x40 | ihl,
        0,
        ihl * 4 + len(udp),
        1,
        fragment,
        64,
        protocol,
        0,
        socket.inet_aton(source),
        socket.inet_aton("192.168.50.10"),
    )
    return dst_mac + src_mac + struct.pack("!H", ethertype) + ip + options + udp


STREAMING = ("streaming", ("netflix.com", "nflxvideo.net"))
WORK = ("work", ("linkedin.com",))


class ParserTests(unittest.TestCase):
    def test_reads_the_addresses_of_a_plain_answer(self) -> None:
        message = dns_message(
            "www.netflix.com",
            [a_record(b"\xc0\x0c", "203.0.113.5"), a_record(b"\xc0\x0c", "203.0.113.6")],
        )

        parsed = parse_dns_message(message)

        self.assertEqual(parsed.questions, ("www.netflix.com",))
        self.assertEqual(
            parsed.addresses,
            (
                ("www.netflix.com", IPv4Address("203.0.113.5")),
                ("www.netflix.com", IPv4Address("203.0.113.6")),
            ),
        )

    def test_follows_a_cname_chain_and_keeps_each_owner_name(self) -> None:
        message = dns_message(
            "www.netflix.com",
            [
                cname_record(b"\xc0\x0c", "www.dradis.netflix.com"),
                cname_record("www.dradis.netflix.com", "edge.example.net"),
                a_record("edge.example.net", "198.51.100.9"),
            ],
        )

        parsed = parse_dns_message(message)

        self.assertEqual(parsed.addresses, (("edge.example.net", IPv4Address("198.51.100.9")),))
        self.assertEqual(parsed.questions, ("www.netflix.com",))

    def test_ignores_other_record_types_but_keeps_reading(self) -> None:
        message = dns_message(
            "a.example", [aaaa_record(b"\xc0\x0c"), a_record(b"\xc0\x0c", "203.0.113.5")]
        )

        self.assertEqual(
            parse_dns_message(message).addresses, (("a.example", IPv4Address("203.0.113.5")),)
        )

    def test_names_are_lowercased(self) -> None:
        message = dns_message("WWW.Example.COM", [a_record(b"\xc0\x0c", "203.0.113.5")])

        self.assertEqual(parse_dns_message(message).questions, ("www.example.com",))

    def test_rejects_queries_errors_and_other_opcodes(self) -> None:
        records = [a_record(b"\xc0\x0c", "203.0.113.5")]
        for flags in (0x0100, 0x8183, 0x8980):
            with self.subTest(flags=hex(flags)):
                self.assertIsNone(parse_dns_message(dns_message("a.example", records, flags)))

    def test_survives_every_truncation_and_random_damage(self) -> None:
        message = dns_message(
            "www.netflix.com",
            [
                cname_record(b"\xc0\x0c", "edge.example.net"),
                a_record("edge.example.net", "198.51.100.9"),
                a_record(b"\xc0\x1e", "198.51.100.10"),
            ],
        )
        for length in range(len(message)):
            parse_dns_message(message[:length])
        generator = random.Random(7)
        for _ in range(3000):
            damaged = bytearray(message)
            for _ in range(generator.randint(1, 6)):
                damaged[generator.randrange(len(damaged))] = generator.randrange(256)
            parse_dns_message(bytes(damaged))
            parse_dns_message(
                bytes(generator.randrange(256) for _ in range(generator.randint(0, 80)))
            )

    def test_a_compression_loop_is_rejected(self) -> None:
        header = struct.pack("!HHHHHH", 1, 0x8180, 1, 0, 0, 0)
        looping = header + b"\xc0\x0c" + struct.pack("!HH", 1, 1)

        self.assertIsNone(parse_dns_message(looping))

    def test_oversized_labels_and_names_are_rejected(self) -> None:
        header = struct.pack("!HHHHHH", 1, 0x8180, 1, 0, 0, 0)
        long_label = header + bytes([64]) + b"a" * 64 + b"\x00" + struct.pack("!HH", 1, 1)
        long_name = header + (bytes([50]) + b"a" * 50) * 6 + b"\x00" + struct.pack("!HH", 1, 1)

        self.assertIsNone(parse_dns_message(long_label))
        self.assertIsNone(parse_dns_message(long_name))

    def test_only_the_first_answers_are_read(self) -> None:
        records = [a_record(b"\xc0\x0c", f"203.0.113.{number}") for number in range(1, 201)]
        parsed = parse_dns_message(dns_message("a.example", records))

        self.assertLessEqual(len(parsed.addresses), 128)

    def test_unwraps_ethernet_ip_and_udp(self) -> None:
        message = dns_message("a.example", [a_record(b"\xc0\x0c", "203.0.113.5")])

        for options in (b"", b"\x01\x01\x01\x01"):
            parsed = parse_dns_frame(frame_for(message, options=options))
            self.assertEqual(parsed.addresses, (("a.example", IPv4Address("203.0.113.5")),))

    def test_other_traffic_is_not_parsed(self) -> None:
        message = dns_message("a.example", [a_record(b"\xc0\x0c", "203.0.113.5")])
        for frame in (
            frame_for(message, sport=5353),
            frame_for(message, protocol=6),
            frame_for(message, fragment=0x2000),
            frame_for(message, fragment=0x00B9),
            frame_for(message, ethertype=0x86DD),
            frame_for(message)[:20],
            b"",
        ):
            self.assertIsNone(parse_dns_frame(frame))


class MatcherTests(unittest.TestCase):
    def test_matches_a_name_and_its_subdomains_only(self) -> None:
        matcher = ServiceMatcher([STREAMING, WORK])

        self.assertEqual(matcher.match("netflix.com"), "streaming")
        self.assertEqual(matcher.match("A.B.Netflix.com."), "streaming")
        self.assertEqual(matcher.match("x.nflxvideo.net"), "streaming")
        self.assertEqual(matcher.match("www.linkedin.com"), "work")
        for name in ("notnetflix.com", "netflix.com.evil.example", "com", "", "nflxvideo.network"):
            self.assertIsNone(matcher.match(name), name)


def plan_payload(*lists, protected=()):
    profile = Profile(id="slow", name="Slow", upload=Impairment(delay_ms=100))
    plan = compile_plan(
        devices=(),
        profiles={"slow": profile},
        protected_mac_addresses=tuple(protected),
        service_list_rules=tuple(
            ServiceListRule(list_id, domains, 0, profile.upload, profile.download)
            for list_id, domains in lists
        ),
    )
    return plan_to_payload(plan)


class Helper:
    def __init__(self) -> None:
        self.calls = []
        self.error = None

    def service_addresses(self, addresses):
        if self.error is not None:
            raise self.error
        self.calls.append({key: list(value) for key, value in addresses.items()})
        return sum(len(value) for value in addresses.values())


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self):
        return self.now


class WatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = Helper()
        self.clock = Clock()
        self.payload = plan_payload(STREAMING, WORK)
        self.watcher = ServiceWatcher(
            helper_client=self.helper, applied_plan=lambda: self.payload, clock=self.clock
        )
        self.watcher.refresh_lists()

    def answer(self, qname, *records, direction=OUTGOING, flags=0x8180):
        self.watcher.observe(frame_for(dns_message(qname, list(records), flags)), direction)

    def test_a_listed_answer_reaches_the_helper_once(self) -> None:
        self.answer("www.netflix.com", a_record(b"\xc0\x0c", "203.0.113.5"))

        self.assertTrue(self.watcher.flush())

        self.assertEqual(self.helper.calls, [{"streaming": [IPv4Address("203.0.113.5")]}])
        self.assertFalse(self.watcher.flush())

    def test_answers_are_grouped_by_list(self) -> None:
        self.answer("www.netflix.com", a_record(b"\xc0\x0c", "203.0.113.5"))
        self.answer("www.linkedin.com", a_record(b"\xc0\x0c", "203.0.113.9"))

        self.watcher.flush()

        self.assertEqual(
            self.helper.calls,
            [{"streaming": [IPv4Address("203.0.113.5")], "work": [IPv4Address("203.0.113.9")]}],
        )

    def test_a_cname_into_a_listed_name_and_out_of_one_both_count(self) -> None:
        self.answer(
            "www.netflix.com",
            cname_record(b"\xc0\x0c", "edge.cdn.example"),
            a_record("edge.cdn.example", "198.51.100.1"),
        )
        self.answer(
            "video.partner.example",
            cname_record(b"\xc0\x0c", "ipv4.nflxvideo.net"),
            a_record("ipv4.nflxvideo.net", "198.51.100.2"),
        )

        self.watcher.flush()

        self.assertEqual(
            sorted(self.helper.calls[0]["streaming"]),
            [IPv4Address("198.51.100.1"), IPv4Address("198.51.100.2")],
        )

    def test_unlisted_names_and_client_sent_frames_are_ignored(self) -> None:
        self.answer("example.org", a_record(b"\xc0\x0c", "203.0.113.5"))
        self.answer("www.netflix.com", a_record(b"\xc0\x0c", "203.0.113.6"), direction=INCOMING)

        self.assertFalse(self.watcher.flush())
        self.assertEqual(self.helper.calls, [])

    def test_local_and_special_addresses_are_never_sent(self) -> None:
        records = [
            a_record(b"\xc0\x0c", address)
            for address in ("127.0.0.1", "192.168.50.1", "10.0.0.5", "0.0.0.0", "224.0.0.1")
        ]
        self.answer("www.netflix.com", *records, a_record(b"\xc0\x0c", "203.0.113.5"))

        self.watcher.flush()

        self.assertEqual(self.helper.calls, [{"streaming": [IPv4Address("203.0.113.5")]}])

    def test_a_known_address_is_sent_again_only_after_the_interval(self) -> None:
        self.answer("www.netflix.com", a_record(b"\xc0\x0c", "203.0.113.5"))
        self.watcher.flush()
        self.answer("www.netflix.com", a_record(b"\xc0\x0c", "203.0.113.5"))
        self.assertFalse(self.watcher.flush())

        self.clock.now += RESEND_SECONDS + 1
        self.answer("www.netflix.com", a_record(b"\xc0\x0c", "203.0.113.5"))

        self.assertTrue(self.watcher.flush())
        self.assertEqual(len(self.helper.calls), 2)

    def test_a_busy_helper_keeps_the_addresses_for_the_next_flush(self) -> None:
        self.helper.error = HelperClientError("busy", reason="helper-busy")
        self.answer("www.netflix.com", a_record(b"\xc0\x0c", "203.0.113.5"))
        self.assertFalse(self.watcher.flush())

        self.helper.error = None

        self.assertTrue(self.watcher.flush())
        self.assertEqual(self.helper.calls, [{"streaming": [IPv4Address("203.0.113.5")]}])

    def test_other_helper_errors_are_survived(self) -> None:
        self.helper.error = HelperClientError("socket gone")
        self.answer("www.netflix.com", a_record(b"\xc0\x0c", "203.0.113.5"))

        with self.assertLogs("jittersim.networking.dns_watch", level="WARNING"):
            self.assertFalse(self.watcher.flush())

        self.helper.error = None
        self.assertTrue(self.watcher.flush())

    def test_each_request_is_bounded_and_the_rest_waits(self) -> None:
        for block in range(0, 1100, 100):
            records = [
                a_record(b"\xc0\x0c", f"198.51.{(block + n) // 250}.{(block + n) % 250 + 1}")
                for n in range(100)
            ]
            self.answer("www.netflix.com", *records)

        self.watcher.flush()
        self.assertLessEqual(len(self.helper.calls[0]["streaming"]), 1024)
        self.watcher.flush()
        sent = {a for call in self.helper.calls for a in call["streaming"]}
        self.assertEqual(len(sent), 1100)

    def test_waiting_addresses_are_bounded(self) -> None:
        self.helper.error = HelperClientError("busy", reason="helper-busy")
        for block in range(0, 6000, 100):
            records = [
                a_record(
                    b"\xc0\x0c",
                    f"198.18.{(block + n) // 250}.{(block + n) % 250 + 1}",
                )
                for n in range(100)
            ]
            self.answer("www.netflix.com", *records)

        self.assertLessEqual(self.watcher.pending_count("streaming"), MAX_PENDING_PER_LIST)

    def test_a_new_plan_changes_what_is_watched(self) -> None:
        self.payload = plan_payload(("music", ("spotify.com",)))
        self.watcher.refresh_lists()

        self.answer("www.netflix.com", a_record(b"\xc0\x0c", "203.0.113.5"))
        self.answer("open.spotify.com", a_record(b"\xc0\x0c", "203.0.113.7"))
        self.watcher.flush()

        self.assertEqual(self.helper.calls, [{"music": [IPv4Address("203.0.113.7")]}])

    def test_no_plan_or_no_lists_means_nothing_is_watched(self) -> None:
        for payload in (None, {}, plan_payload(), {"service_list_rules": "bad"}):
            self.payload = payload
            self.watcher.refresh_lists()
            self.answer("www.netflix.com", a_record(b"\xc0\x0c", "203.0.113.5"))
            self.assertFalse(self.watcher.flush())

    def parse_calls_for(self, *frames):
        calls = []
        original = dns_watch.parse_dns_frame

        def counting(frame):
            calls.append(frame)
            return original(frame)

        with patch.object(dns_watch, "parse_dns_frame", counting):
            for frame in frames:
                self.watcher.observe(frame, OUTGOING)
        return calls

    def test_answers_to_protected_clients_are_never_parsed(self) -> None:
        protected = "02:00:00:00:00:05"
        self.payload = plan_payload(STREAMING, WORK, protected=[protected])
        self.watcher.refresh_lists()
        message = dns_message("www.netflix.com", [a_record(b"\xc0\x0c", "203.0.113.5")])
        to_protected = frame_for(message, dst_mac=bytes.fromhex("020000000005"))
        to_other = frame_for(message)

        self.assertEqual(self.parse_calls_for(to_protected), [])
        self.assertEqual(self.watcher.pending_count("streaming"), 0)
        self.assertFalse(self.watcher.flush())

        self.assertEqual(self.parse_calls_for(to_other), [to_other])
        self.assertEqual(self.watcher.pending_count("streaming"), 1)

    def test_every_protected_client_is_skipped(self) -> None:
        macs = ["02:00:00:00:00:05", "02:00:00:00:00:06", "AA:BB:CC:DD:EE:00"]
        self.payload = plan_payload(STREAMING, protected=macs)
        self.watcher.refresh_lists()
        message = dns_message("www.netflix.com", [a_record(b"\xc0\x0c", "203.0.113.5")])
        frames = [
            frame_for(message, dst_mac=bytes.fromhex(mac.replace(":", ""))) for mac in macs
        ]

        self.assertEqual(self.parse_calls_for(*frames), [])

    def test_the_protected_set_follows_a_changed_plan_even_when_the_lists_do_not_change(
        self,
    ) -> None:
        protected = "02:00:00:00:00:05"
        message = dns_message("www.netflix.com", [a_record(b"\xc0\x0c", "203.0.113.5")])
        frame = frame_for(message, dst_mac=bytes.fromhex("020000000005"))

        self.payload = plan_payload(STREAMING, WORK, protected=[protected])
        self.watcher.refresh_lists()
        self.assertEqual(self.parse_calls_for(frame), [])

        self.payload = plan_payload(STREAMING, WORK)
        self.watcher.refresh_lists()
        self.assertEqual(self.parse_calls_for(frame), [frame])

        self.payload = plan_payload(STREAMING, WORK, protected=[protected])
        self.watcher.refresh_lists()
        self.assertEqual(self.parse_calls_for(frame), [])

    def test_a_plan_without_protected_clients_or_with_garbage_still_works(self) -> None:
        message = dns_message("www.netflix.com", [a_record(b"\xc0\x0c", "203.0.113.5")])
        frame = frame_for(message, dst_mac=bytes.fromhex("020000000005"))
        stripped = plan_payload(STREAMING, WORK)
        stripped.pop("protected_mac_addresses")
        for protected in (
            None,
            "02:00:00:00:00:05",
            [5, None, "zz:zz", "02:00:00:00:00", "", "0200000000050000"],
        ):
            with self.subTest(protected=protected):
                self.payload = {**plan_payload(STREAMING, WORK), "protected_mac_addresses": protected}
                self.watcher.refresh_lists()
                self.assertEqual(self.parse_calls_for(frame), [frame])
        self.payload = stripped
        self.watcher.refresh_lists()
        self.assertEqual(self.parse_calls_for(frame), [frame])

    def test_losing_the_plan_forgets_the_protected_clients_together_with_the_lists(self) -> None:
        self.payload = plan_payload(STREAMING, protected=["02:00:00:00:00:05"])
        self.watcher.refresh_lists()
        self.payload = None
        self.watcher.refresh_lists()
        message = dns_message("www.netflix.com", [a_record(b"\xc0\x0c", "203.0.113.5")])

        self.assertEqual(self.parse_calls_for(frame_for(message)), [])  # no lists: nothing parsed
        self.assertFalse(self.watcher.flush())

    def test_garbage_frames_never_stop_the_watcher(self) -> None:
        generator = random.Random(3)
        for _ in range(500):
            junk = bytes(generator.randrange(256) for _ in range(generator.randint(0, 200)))
            self.watcher.observe(junk, OUTGOING)
        self.assertFalse(self.watcher.flush())


class FilterTests(unittest.TestCase):
    def test_the_kernel_filter_passes_only_ipv4_udp_from_port_53(self) -> None:
        self.assertEqual(len(BPF_DNS_RESPONSES), 11)
        self.assertEqual(BPF_DNS_RESPONSES[0], (0x28, 0, 0, 0x0000000C))
        self.assertEqual(BPF_DNS_RESPONSES[8], (0x15, 0, 1, 53))
        self.assertEqual(BPF_DNS_RESPONSES[-1], (0x06, 0, 0, 0))

    def run_filter(self, frame: bytes) -> int:
        """Interpret the classic BPF program on one frame."""

        accumulator = index = 0
        program = BPF_DNS_RESPONSES
        counter = 0
        while True:
            code, jump_true, jump_false, constant = program[counter]
            counter += 1
            if code == 0x28:
                offset = constant
                accumulator = (
                    struct.unpack_from("!H", frame, offset)[0] if offset + 2 <= len(frame) else None
                )
            elif code == 0x30:
                accumulator = frame[constant] if constant < len(frame) else None
            elif code == 0x48:
                offset = index + constant
                accumulator = (
                    struct.unpack_from("!H", frame, offset)[0] if offset + 2 <= len(frame) else None
                )
            elif code == 0xB1:
                index = (frame[constant] & 0xF) * 4
            elif code == 0x15:
                counter += jump_true if accumulator == constant else jump_false
            elif code == 0x45:
                counter += (
                    jump_true if accumulator is not None and accumulator & constant else jump_false
                )
            elif code == 0x06:
                return constant
            if accumulator is None:
                return 0

    def test_the_filter_program_behaves_as_described(self) -> None:
        message = dns_message("a.example", [a_record(b"\xc0\x0c", "203.0.113.5")])

        self.assertGreater(self.run_filter(frame_for(message)), 0)
        self.assertGreater(self.run_filter(frame_for(message, options=b"\x01\x01\x01\x01")), 0)
        self.assertEqual(self.run_filter(frame_for(message, sport=5353)), 0)
        self.assertEqual(self.run_filter(frame_for(message, protocol=6)), 0)
        self.assertEqual(self.run_filter(frame_for(message, fragment=0x00B9)), 0)
        self.assertEqual(self.run_filter(frame_for(message, ethertype=0x86DD)), 0)


if __name__ == "__main__":
    unittest.main()


class ForwardedAnswerTests(unittest.TestCase):
    """Security audit 2026-10-18, finding A1.

    Without household DNS rules a test client may ask any DNS server on the internet.
    The Pi forwards that server's answer out of the test interface, so it is an
    outgoing frame from port 53 like the Pi's own. Such an answer must not add an
    address to a service list, which is shared by every affected test device.
    """

    FORGED = "8.8.8.8"

    def setUp(self) -> None:
        self.helper = Helper()
        self.redirected = False
        self.watcher = ServiceWatcher(
            helper_client=self.helper,
            applied_plan=lambda: plan_payload(STREAMING),
            clock=Clock(),
            dns_redirected=lambda: self.redirected,
        )
        self.watcher.refresh_lists()

    def forged_answer(self, source="203.0.113.53"):
        message = dns_message("www.netflix.com", [a_record(b"\xc0\x0c", self.FORGED)])
        self.watcher.observe(frame_for(message, source=source), OUTGOING)

    def test_an_answer_from_another_dns_server_is_ignored(self) -> None:
        self.forged_answer()

        self.assertFalse(self.watcher.flush())
        self.assertEqual(self.helper.calls, [])

    def test_the_pis_own_resolver_still_counts(self) -> None:
        self.forged_answer(source="192.168.50.1")

        self.assertTrue(self.watcher.flush())
        self.assertEqual(self.helper.calls, [{"streaming": [IPv4Address(self.FORGED)]}])

    def test_while_household_dns_rules_redirect_every_lookup_other_sources_count(self) -> None:
        # Then direct DNS is dropped and a redirected answer from the Pi's resolver
        # leaves with the address the client asked.
        self.redirected = True
        self.forged_answer()

        self.assertTrue(self.watcher.flush())

    def test_a_failing_redirect_check_trusts_only_the_resolver(self) -> None:
        def broken():
            raise OSError("unreadable")

        self.watcher = ServiceWatcher(
            helper_client=self.helper, applied_plan=lambda: plan_payload(STREAMING),
            clock=Clock(), dns_redirected=broken,
        )
        self.watcher.refresh_lists()
        self.forged_answer()

        self.assertFalse(self.watcher.flush())

    def test_frames_that_are_not_ipv4_have_no_source(self) -> None:
        self.assertIsNone(dns_watch.frame_source(b""))
        ipv6 = b"\x00" * 12 + b"\x86\xdd" + b"\x60" + b"\x00" * 40
        self.assertIsNone(dns_watch.frame_source(ipv6))
        self.assertEqual(dns_watch.frame_source(frame_for(b"x" * 12, source="198.51.100.7")),
                         IPv4Address("198.51.100.7"))


class SourceMacTests(unittest.TestCase):
    """A frame that only looks like the Pi's answer: another station's frame relayed on the air.

    Without client isolation the access point passes a frame from one client to another.
    It leaves the interface like the Pi's own frames, with the first client's MAC address as
    its source, and a client can write any IP source address into it.
    """

    OWN = b"\x02\xaa\xbb\xcc\xdd\xee"
    OTHER = b"\x02\x11\x22\x33\x44\x55"
    ADDRESS = "203.0.113.7"

    def setUp(self) -> None:
        self.helper = Helper()
        self.redirected = False
        self.watcher = ServiceWatcher(
            helper_client=self.helper,
            applied_plan=lambda: plan_payload(STREAMING),
            clock=Clock(),
            dns_redirected=lambda: self.redirected,
        )
        self.watcher.refresh_lists()
        self.message = dns_message("www.netflix.com", [a_record(b"\xc0\x0c", self.ADDRESS)])

    def test_only_frames_with_the_interface_address_as_source_count(self) -> None:
        self.watcher.interface_mac = self.OWN

        self.watcher.observe(frame_for(self.message, src_mac=self.OTHER), OUTGOING)
        self.assertFalse(self.watcher.flush())
        # The same holds while household DNS rules make every IP source address count.
        self.redirected = True
        self.watcher.observe(frame_for(self.message, src_mac=self.OTHER, source="203.0.113.53"),
                             OUTGOING)
        self.assertFalse(self.watcher.flush())

        self.watcher.observe(frame_for(self.message, src_mac=self.OWN), OUTGOING)
        self.assertTrue(self.watcher.flush())
        self.assertEqual(self.helper.calls, [{"streaming": [IPv4Address(self.ADDRESS)]}])

    def test_without_a_known_interface_address_the_source_ip_rules_alone_decide(self) -> None:
        self.assertIsNone(self.watcher.interface_mac)

        self.watcher.observe(frame_for(self.message, src_mac=self.OTHER), OUTGOING)

        self.assertTrue(self.watcher.flush())

    def test_the_watch_loop_takes_the_interface_address_from_the_socket(self) -> None:
        stop = threading.Event()
        relayed = dns_message("www.netflix.com", [a_record(b"\xc0\x0c", "203.0.113.8")])
        frames = [
            (frame_for(self.message, src_mac=self.OWN), ("wlan0", 3, OUTGOING, 1, self.OWN)),
            (frame_for(relayed, src_mac=self.OTHER), ("wlan0", 3, OUTGOING, 1, self.OTHER)),
        ]

        class Socket:
            def getsockname(self):
                return "wlan0", 3, 0, 1, SourceMacTests.OWN

            def recvfrom(self, _size):
                if frames:
                    return frames.pop(0)
                stop.set()
                raise socket.timeout

            def close(self):
                pass

        watch(self.watcher, Socket, stop)
        self.watcher.flush()

        self.assertEqual(self.watcher.interface_mac, self.OWN)
        sent = [address for call in self.helper.calls for address in call["streaming"]]
        self.assertEqual(sent, [IPv4Address(self.ADDRESS)])


class RedirectFileTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "access-policy.json"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def write(self, text):
        import os

        temporary = self.path.with_suffix(".new")
        temporary.write_text(text)
        os.replace(temporary, self.path)

    def test_follows_whether_household_dns_rules_are_on(self) -> None:
        import json

        from jittersim.config.models import AccessOptions
        from jittersim.engine.access import AccessPlan

        state = dns_watch._RedirectFile(self.path)
        self.assertFalse(state())
        def policy(enabled):
            options = AccessOptions(enabled=enabled, dns_failure_percent=5)
            return json.dumps(AccessPlan(options=options).payload())

        self.write(policy(True))
        self.assertTrue(state())
        self.write(policy(False))
        self.assertFalse(state())

    def test_garbage_means_not_redirected(self) -> None:
        state = dns_watch._RedirectFile(self.path)
        for text in ("not json", "[]", '{"version": 1, "options": 5}'):
            self.write(text)
            self.assertFalse(state(), text)

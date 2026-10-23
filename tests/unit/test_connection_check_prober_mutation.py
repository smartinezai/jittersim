"""Synthetic sockets hold the probe measurements exposed by connection checks."""

import socket
import struct
from unittest.mock import Mock


from jittersim import speed_test as speed


class Socket:
    def __init__(self):
        self.sent = []
        self.answers = []
        self.closed = False

    def setblocking(self, value):
        assert value is False

    def connect(self, address):
        assert address == ("192.0.2.10", 0)

    def getsockname(self):
        return ("192.0.2.1", 0)

    def send(self, packet):
        self.sent.append(packet)

    def recv(self, size):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def close(self):
        self.closed = True


def test_default_icmp_socket_is_nonblocking_and_has_the_right_protocol(monkeypatch):
    sock = Socket()
    factory = Mock(return_value=sock)
    monkeypatch.setattr(speed.socket, "socket", factory)
    prober = speed.IcmpProber("192.0.2.10")
    factory.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    assert prober.status == "measured"
    prober.close()
    assert sock.closed


def test_replies_after_a_receive_error_or_unknown_probe_still_count_with_precise_rtt(monkeypatch):
    sock, clock = Socket(), [10.0]
    prober = speed.IcmpProber("192.0.2.10", socket_factory=lambda: sock,
                              clock=lambda: clock[0], payload_size=56)
    for _ in range(3):
        prober.send()
    # A transient receive error and an unrelated reply must not lose the good
    # replies already queued behind them. Arrivals are 3, 1, 2, then a copy of 3.
    sock.answers = [OSError("transient"), b"unknown", b"third", b"first", b"second", b"third"]
    sequence = {b"unknown": 999, b"first": 1, b"second": 2, b"third": 3}
    monkeypatch.setattr(speed, "parse_echo_reply", lambda packet, nonce: sequence[packet])

    def ready(*args):
        clock[0] += 0.125
        return ([sock], [], []) if sock.answers else ([], [], [])

    monkeypatch.setattr(speed.select, "select", ready)
    prober.collect(until=12)
    assert prober.records() == [
        {"rtt_ms": 500, "copies": 1, "arrival": 1},
        {"rtt_ms": 625, "copies": 1, "arrival": 2},
        {"rtt_ms": 375, "copies": 2, "arrival": 0},
    ]
    assert prober.counts() == (3, 3)
    assert [struct.unpack("!BBHHH", packet[:8])[-1] for packet in sock.sent] == [1, 2, 3]
    assert all(len(packet) == 64 and speed.icmp_checksum(packet) == 0 for packet in sock.sent)
    prober.close()


def test_echo_sequence_wraps_as_an_unsigned_sixteen_bit_value():
    packet = speed.echo_request(65535, b"nonce", 56)
    assert struct.unpack("!BBHHH", packet[:8]) == (8, 0, speed.icmp_checksum(
        struct.pack("!BBHHH", 8, 0, 0, 0, 65535) + b"nonce" + bytes(51)), 0, 65535)
    assert speed.icmp_checksum(packet) == 0


def test_collect_at_its_deadline_does_not_poll_the_socket(monkeypatch):
    prober = speed.IcmpProber("192.0.2.10", socket_factory=Socket, clock=lambda: 10)
    poll = Mock(side_effect=AssertionError("deadline passed"))
    monkeypatch.setattr(speed.select, "select", poll)
    prober.collect(until=10)
    poll.assert_not_called()
    prober.close()


def test_unavailable_prober_keeps_the_explanation():
    prober = speed.NoProber("No verified address")
    assert prober.summary()["reason"] == "No verified address"
    assert prober.status == "unavailable"

"""Bounded parsing of actual Unbound actions and replies from a private pipe.

This module never writes a query log. Parsing an NXDOMAIN reply never creates a
policy-block decision. The root service wrapper is the trusted producer boundary.
"""

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from jittersim.config.dns_outcomes import MAX_DNS_OUTCOME_BATCH, DnsDecision

MAX_DECISION_LINE = 1024
MAX_DECISION_READ = 4096
_LINE = re.compile(
    rb"\[(\d{1,16})\] jittersim-dns-([a-f0-9]{64})\[\d{1,10}:\d{1,6}\] "
    rb"(info|reply): (.*)\Z"
)
_ACTION = re.compile(
    rb"[^ ]{1,254} always_nxdomain ([0-9.]{7,15})@\d{1,5} "
    rb"([^ ]{1,254}) [A-Z0-9]{1,16} IN\Z"
)
_REPLY = re.compile(
    rb"([0-9.]{7,15}) ([^ ]{1,254}) [A-Z0-9]{1,16} IN "
    rb"([A-Z0-9]{1,16}) [0-9.]{1,24} [01] \d{1,8}(?: .{0,128})?\Z"
)


@dataclass(frozen=True)
class ParsedDecision:
    generation: str
    event: DnsDecision


class DecisionLines:
    """Finite partial-line state. Oversized lines are discarded through their newline."""

    def __init__(self):
        self.buffer = bytearray()
        self.discarding = False
        self.sequence = 0
        self.rejected = 0

    def clear(self):
        self.buffer.clear()
        self.discarding = False

    def feed(self, data):
        if not isinstance(data, bytes) or len(data) > MAX_DECISION_READ:
            raise ValueError("DNS input read exceeds its bound")
        results = []
        for byte in data:
            if byte == 10:
                if not self.discarding:
                    parsed = self._parse(bytes(self.buffer))
                    if parsed is not None:
                        results.append(parsed)
                self.buffer.clear()
                self.discarding = False
                continue
            if not self.discarding:
                if len(self.buffer) >= MAX_DECISION_LINE:
                    self.buffer.clear()
                    self.discarding = True
                    self.rejected += 1
                else:
                    self.buffer.append(byte)
        # Minimum line lengths make this unreachable for valid reads, but retain the bound.
        if len(results) > MAX_DNS_OUTCOME_BATCH:
            self.rejected += len(results) - MAX_DNS_OUTCOME_BATCH
        return tuple(results[:MAX_DNS_OUTCOME_BATCH])

    def _parse(self, line) -> Optional[ParsedDecision]:
        match = _LINE.fullmatch(line)
        if match is None:
            return None
        observed_at, generation, kind, body = match.groups()
        action = _ACTION.fullmatch(body) if kind == b"info" else None
        reply = _REPLY.fullmatch(body) if kind == b"reply" else None
        if action is not None:
            address, domain = action.groups()
            outcome = "blocked_decisions"
        elif reply is not None:
            address, domain, rcode = reply.groups()
            outcome = "answered_noerror" if rcode == b"NOERROR" else "other_dns_errors"
        else:
            return None
        self.sequence += 1
        try:
            return ParsedDecision(
                generation.decode("ascii"),
                DnsDecision(
                    sequence=self.sequence,
                    ipv4_address=address.decode("ascii"),
                    domain=domain.decode("ascii").removesuffix("."),
                    kind=outcome,
                    observed_at=int(observed_at),
                ),
            )
        except (UnicodeDecodeError, TypeError, ValueError):
            self.rejected += 1
            return None


def open_private_resolver(command):
    """Launch with an inherited nonblocking stderr pipe and no journal/raw-file output.

    The caller owns the returned process and read descriptor and must reap/close
    both. A dead reader never blocks this producer. Unbound ignores SIGPIPE.
    """
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    os.set_blocking(write_fd, False)
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=write_fd
        )
    except BaseException:
        os.close(read_fd)
        raise
    finally:
        os.close(write_fd)
    return process, read_fd


def supervise_resolver(
    config, socket_path, pid_path, *, executable="/usr/sbin/unbound", expected_socket_uid=None
):
    """Drain continuously and drop telemetry on failure while the resolver keeps serving."""
    import secrets
    import select
    import signal
    import socket
    import time
    from pathlib import Path

    from .dns_decision_ipc import decode_packet, encode_packet, write_producer_record

    session = secrets.token_hex(16)
    started_ns = time.monotonic_ns()
    record = {"pid": os.getpid(), "session": session, "started_ns": started_ns}
    process, read_fd = open_private_resolver([executable, "-d", "-c", str(config)])
    parser = DecisionLines()
    connection = None
    generation = None
    nonce = None
    retired = False
    stopping = False
    last_attempt = last_heartbeat = 0.0

    def stop(_number, _frame):
        nonlocal stopping
        stopping = True

    previous = {number: signal.signal(number, stop) for number in (signal.SIGTERM, signal.SIGINT)}
    try:
        try:
            write_producer_record(pid_path, record)
            publishable = True
        except (OSError, ValueError):
            publishable = False
        while not stopping and process.poll() is None:
            readers = [read_fd] + ([connection] if connection is not None else [])
            ready, _, _ = select.select(readers, [], [], 0.02)
            if read_fd in ready:
                # Drain finite chunks even with no collector or a full forwarding socket.
                for _ in range(8):
                    try:
                        content = os.read(read_fd, MAX_DECISION_READ)
                    except BlockingIOError:
                        break
                    if not content:
                        stopping = True
                        break
                    for parsed in parser.feed(content):
                        if generation is None:
                            generation = parsed.generation
                        elif generation != parsed.generation:
                            retired = True  # An actual service restart is the next privacy epoch.
                            if connection is not None:
                                connection.close()
                                connection = None
                            nonce = None
                        if connection is not None and nonce is not None and not retired:
                            event = parsed.event
                            try:
                                connection.send(
                                    encode_packet(
                                        {
                                            "type": "event",
                                            "nonce": nonce,
                                            "session": session,
                                            "generation": generation,
                                            "sequence": event.sequence,
                                            "client": event.ipv4_address,
                                            "name": event.domain,
                                            "kind": event.kind,
                                            "at": event.observed_at,
                                        }
                                    )
                                )
                            except BlockingIOError:
                                pass  # Never wait for the dashboard, including its kernel queue.
                            except OSError:
                                connection.close()
                                connection = None
                                nonce = None
            now = time.monotonic()
            if (
                connection is None
                and publishable
                and generation is not None
                and not retired
                and now - last_attempt >= 1
            ):
                last_attempt = now
                candidate = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                candidate.setblocking(False)
                try:
                    import pwd
                    import stat

                    parent = Path(socket_path).parent.lstat()
                    target = Path(socket_path).lstat()
                    expected_owner = (
                        pwd.getpwnam("jittersim").pw_uid
                        if expected_socket_uid is None
                        else expected_socket_uid
                    )
                    if (
                        not stat.S_ISDIR(parent.st_mode)
                        or stat.S_IMODE(parent.st_mode) != 0o700
                        or parent.st_uid != expected_owner
                        or not stat.S_ISSOCK(target.st_mode)
                        or stat.S_IMODE(target.st_mode) != 0o600
                        or target.st_uid != expected_owner
                    ):
                        raise OSError("private DNS receiver permissions refused")
                    candidate.connect(str(socket_path))
                    candidate.send(
                        encode_packet(
                            {
                                "type": "hello",
                                "session": session,
                                "started_ns": started_ns,
                                "generation": generation,
                            }
                        )
                    )
                    connection = candidate
                except (OSError, KeyError):
                    candidate.close()
            if connection is not None and connection in ready:
                try:
                    challenge = decode_packet(connection.recv(1024))
                    if (
                        set(challenge) != {"type", "nonce", "generation"}
                        or challenge["type"] != "challenge"
                        or challenge["generation"] != generation
                        or not re.fullmatch(r"[a-f0-9]{32}", challenge["nonce"])
                    ):
                        raise ValueError("invalid DNS epoch challenge")
                    # Discard all pre-ack data. If a hot producer cannot reach an empty
                    # pipe within this finite budget, leave coverage unavailable.
                    parser.clear()
                    empty = False
                    for _ in range(8):
                        try:
                            content = os.read(read_fd, MAX_DECISION_READ)
                        except BlockingIOError:
                            empty = True
                            break
                        if not content:
                            break
                    if not empty:
                        raise ValueError("DNS pipe did not reach the epoch barrier")
                    nonce = challenge["nonce"]
                    connection.send(
                        encode_packet(
                            {
                                "type": "ack",
                                "nonce": nonce,
                                "session": session,
                                "generation": generation,
                            }
                        )
                    )
                except BlockingIOError:
                    pass
                except (OSError, ValueError, TypeError):
                    connection.close()
                    connection = None
                    nonce = None
            if connection is not None and nonce is not None and now - last_heartbeat >= 1:
                last_heartbeat = now
                try:
                    connection.send(encode_packet({"type": "heartbeat", "nonce": nonce}))
                except BlockingIOError:
                    pass
                except OSError:
                    connection.close()
                    connection = None
                    nonce = None
        return process.poll() if process.poll() is not None else 0
    finally:
        if connection is not None:
            connection.close()
        os.close(read_fd)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        else:
            process.wait()
        # Remove only this process's unchanged root-owned session record.
        try:
            path = Path(pid_path)
            from .dns_decision_ipc import producer_record

            if producer_record(path, os.getuid()) == record:
                path.unlink()
        except (OSError, ValueError):
            pass
        for number, handler in previous.items():
            signal.signal(number, handler)


def main(argv=None):
    import argparse
    from pathlib import Path

    from .dns_decision_ipc import PID_PATH, SOCKET_PATH

    parser = argparse.ArgumentParser(description="Run filtered DNS with private bounded outcomes")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--socket", type=Path, default=SOCKET_PATH)
    parser.add_argument("--pid-file", type=Path, default=PID_PATH)
    args = parser.parse_args(argv)
    return supervise_resolver(args.config, args.socket, args.pid_file)


if __name__ == "__main__":
    raise SystemExit(main())

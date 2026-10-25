"""One private resolver producer and an authenticated, bounded dashboard reader.

Local root is trusted. A root-owned PID/session record binds the expected service
process. No HTTP request can submit an event. The dashboard polls only while its
private Traffic API is being read, using that request's fresh identity snapshot.
"""

import array
import fcntl
import json
import os
import secrets
import socket
import sqlite3
import stat
import struct
import time
from pathlib import Path

from jittersim.config.dns_outcomes import MAX_DNS_OUTCOME_BATCH, DnsDecision

SOCKET_PATH = Path("/run/jittersim/dns-observations/events.sock")
PID_PATH = Path("/run/jittersim/dns-producer.json")
MAX_PACKET = 1024
_CREDENTIAL_BYTES = struct.calcsize("3i")


def encode_packet(value):
    content = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(content) > MAX_PACKET:
        raise ValueError("DNS event packet exceeds its bound")
    return content


def decode_packet(content):
    if not content or len(content) > MAX_PACKET:
        raise ValueError("invalid DNS event packet length")
    try:
        value = json.loads(content)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("invalid DNS event packet") from error
    if not isinstance(value, dict):
        raise ValueError("DNS event packet must be an object")
    return value


def credential_packet(connection, *, expected_uid, expected_pid):
    """Reject truncation, unauthenticated packets and passed descriptors without leaking FDs."""
    data, ancillary, flags, _ = connection.recvmsg(
        MAX_PACKET, socket.CMSG_SPACE(_CREDENTIAL_BYTES) + socket.CMSG_SPACE(64)
    )
    credentials = []
    rejected = bool(flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC))
    for level, kind, content in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            descriptors = array.array("i")
            descriptors.frombytes(content[: len(content) - len(content) % descriptors.itemsize])
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            rejected = True
        elif (
            level == socket.SOL_SOCKET
            and kind == getattr(socket, "SCM_CREDENTIALS", 2)
            and len(content) == _CREDENTIAL_BYTES
        ):
            credentials.append(struct.unpack("3i", content))
        else:
            rejected = True
    if rejected or len(credentials) != 1 or credentials[0][:2] != (expected_pid, expected_uid):
        raise ValueError("DNS producer credentials were refused")
    return decode_packet(data)


def producer_record(path, expected_uid=0):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_mode & 0o022
            or metadata.st_size > 512
        ):
            raise ValueError("DNS producer record permissions were refused")
        value = decode_packet(os.read(descriptor, 513))
        if set(value) != {"pid", "session", "started_ns"}:
            raise ValueError("invalid DNS producer record")
        if (
            type(value["pid"]) is not int
            or value["pid"] <= 0
            or type(value["started_ns"]) is not int
            or value["started_ns"] < 0
            or not isinstance(value["session"], str)
            or len(value["session"]) != 32
        ):
            raise ValueError("invalid DNS producer identity")
        return value
    finally:
        os.close(descriptor)


def write_producer_record(path, record):
    path = Path(path)
    parent = path.parent.stat()
    if parent.st_mode & 0o002:
        raise ValueError("DNS producer directory is writable by others")
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None and (
        not stat.S_ISREG(existing.st_mode) or existing.st_uid != os.getuid()
    ):
        raise ValueError("DNS producer record is not owned by this service")
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    try:
        os.fchmod(descriptor, 0o640)
        if os.getuid() == 0:
            os.fchown(descriptor, 0, parent.st_gid)
        os.write(descriptor, encode_packet(record))
        os.replace(temporary, path)
    finally:
        os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class DecisionReceiver:
    """No worker thread or helper polling. One finite nonblocking drain per private API read."""

    def __init__(self, repository, path=SOCKET_PATH, pid_path=PID_PATH, *, trusted_uid=0):
        self.repository, self.path, self.pid_path = repository, Path(path), Path(pid_path)
        self.trusted_uid = trusted_uid
        self.connection = self.pending = self.listener = None
        self.lock_fd = None
        self.owned_inode = None
        self.active = False
        self.last_packet = 0.0
        metadata = self.path.parent.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ValueError("DNS socket directory must be private and owned by the dashboard")
        try:
            self.lock_fd = os.open(
                self.path.parent / "owner.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
            )
            lock_metadata = os.fstat(self.lock_fd)
            if (
                not stat.S_ISREG(lock_metadata.st_mode)
                or lock_metadata.st_uid != os.getuid()
                or stat.S_IMODE(lock_metadata.st_mode) != 0o600
            ):
                raise ValueError("DNS socket owner lock is unsafe")
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                previous = self.path.lstat()
            except FileNotFoundError:
                previous = None
            if previous is not None:
                if not stat.S_ISSOCK(previous.st_mode) or previous.st_uid != os.getuid():
                    raise ValueError("DNS socket path is not an owned socket")
                self.path.unlink()
            self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            self.listener.setblocking(False)
            self.listener.bind(str(self.path))
            os.chmod(self.path, 0o600)
            self.owned_inode = (self.path.stat().st_dev, self.path.stat().st_ino)
            self.listener.listen(1)
        except BaseException:
            self.close()
            raise

    def _disconnect(self):
        owned = self.connection is not None or self.active
        if self.connection is not None:
            self.connection.close()  # Closing also discards the kernel's queued packets.
        self.connection = self.pending = None
        self.active = False
        if owned:
            self.repository._dns_outcomes.available = False

    def poll(self, snapshot, now):
        started = time.monotonic()
        try:
            with self.repository.locked_within(0):
                if self.active and self.repository._dns_outcomes.epoch is None:
                    self._disconnect()
                if self.connection is None:
                    try:
                        connection, _ = self.listener.accept()
                    except BlockingIOError:
                        return
                    self.connection = connection
                    connection.setblocking(False)
                    connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                    self.peer = struct.unpack(
                        "3i",
                        connection.getsockopt(
                            socket.SOL_SOCKET, socket.SO_PEERCRED, _CREDENTIAL_BYTES
                        ),
                    )
                    record = producer_record(self.pid_path, self.trusted_uid)
                    if self.peer[:2] != (record["pid"], self.trusted_uid):
                        raise ValueError("unexpected DNS producer process")
                    self.record = record
                    self.last_packet = started
                events = []
                for _ in range(MAX_DNS_OUTCOME_BATCH):
                    if time.monotonic() - started > 0.005:
                        break
                    try:
                        packet = credential_packet(
                            self.connection,
                            expected_uid=self.trusted_uid,
                            expected_pid=self.record["pid"],
                        )
                    except BlockingIOError:
                        break
                    self.last_packet = time.monotonic()
                    kind = packet.get("type")
                    if kind == "hello" and self.pending is None and not self.active:
                        if set(packet) != {"type", "session", "started_ns", "generation"} or any(
                            packet[key] != self.record[key] for key in ("session", "started_ns")
                        ):
                            raise ValueError("DNS source session mismatch")
                        self.pending = {
                            "epoch": secrets.token_hex(16),
                            "session": packet["session"],
                            "generation": packet["generation"],
                            "producer_started_ns": packet["started_ns"],
                            "expected_version": (
                                self.repository.get_runtime_state().configuration_version
                            ),
                        }
                        self.connection.send(
                            encode_packet(
                                {
                                    "type": "challenge",
                                    "nonce": self.pending["epoch"],
                                    "generation": self.pending["generation"],
                                }
                            )
                        )
                    elif kind == "ack" and self.pending is not None and not self.active:
                        if (
                            set(packet) != {"type", "nonce", "session", "generation"}
                            or packet["nonce"] != self.pending["epoch"]
                            or packet["session"] != self.pending["session"]
                            or packet["generation"] != self.pending["generation"]
                        ):
                            raise ValueError("DNS source acknowledgement mismatch")
                        self.repository.begin_dns_outcome_epoch(
                            **self.pending, snapshot=snapshot, now=now
                        )
                        self.active = True
                    elif kind == "event" and self.active:
                        if (
                            set(packet)
                            != {
                                "type",
                                "nonce",
                                "session",
                                "generation",
                                "sequence",
                                "client",
                                "name",
                                "kind",
                                "at",
                            }
                            or packet["nonce"] != self.pending["epoch"]
                            or packet["session"] != self.pending["session"]
                            or packet["generation"] != self.pending["generation"]
                        ):
                            raise ValueError("DNS event belongs to another epoch")
                        events.append(
                            DnsDecision(
                                packet["sequence"],
                                packet["client"],
                                packet["name"],
                                packet["kind"],
                                packet["at"],
                            )
                        )
                    elif kind == "heartbeat" and self.active:
                        if packet != {"type": "heartbeat", "nonce": self.pending["epoch"]}:
                            raise ValueError("DNS heartbeat belongs to another epoch")
                    else:
                        raise ValueError("unexpected DNS protocol message")
                if self.active:
                    context = {
                        key: self.pending[key]
                        for key in ("epoch", "session", "generation", "expected_version")
                    }
                    self.repository.record_dns_decisions(
                        events, **context, snapshot=snapshot, now=now
                    )
                    self.repository._dns_outcomes.available = True
                if time.monotonic() - self.last_packet > 3:
                    self._disconnect()
        except (OSError, ValueError, RuntimeError, sqlite3.Error):
            self._disconnect()

    def close(self):
        self._disconnect()
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        if self.owned_inode is not None:
            try:
                metadata = self.path.lstat()
                if (metadata.st_dev, metadata.st_ino) == self.owned_inode:
                    self.path.unlink()
            except FileNotFoundError:
                pass
            self.owned_inode = None
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

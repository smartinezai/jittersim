"""The root event bridge accepts no public payload or passed descriptor."""

import array
import json
import os
import socket
import sqlite3
import struct
import time

import pytest

from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.storage import ConfigRepository
from jittersim.networking.dns_decision_ipc import (
    DecisionReceiver,
    credential_packet,
    decode_packet,
    encode_packet,
    producer_record,
    write_producer_record,
)
from tests.unit.test_dns_decision_outcomes import IP, MAC, snapshot


class Received:
    def __init__(self, data=None, ancillary=None, flags=0):
        self.data = encode_packet({"type": "hello"}) if data is None else data
        self.ancillary = ancillary or []
        self.flags = flags

    def recvmsg(self, *_args):
        return self.data, self.ancillary, self.flags, None


def credentials(pid=123, uid=0):
    return [
        (socket.SOL_SOCKET, getattr(socket, "SCM_CREDENTIALS", 2), struct.pack("3i", pid, uid, 0))
    ]


@pytest.mark.parametrize(
    "ancillary,flags",
    [
        ([], 0),
        (credentials(uid=1), 0),
        (credentials(pid=999), 0),
        (credentials() * 2, 0),
        (credentials(), socket.MSG_TRUNC),
        (credentials(), socket.MSG_CTRUNC),
        ([(socket.SOL_SOCKET, getattr(socket, "SCM_CREDENTIALS", 2), b"bad")], 0),
    ],
)
def test_missing_wrong_duplicate_or_truncated_credentials_are_rejected(ancillary, flags):
    with pytest.raises(ValueError):
        credential_packet(
            Received(ancillary=ancillary, flags=flags), expected_uid=0, expected_pid=123
        )


def test_authenticated_packet_and_rights_rejection_close_received_descriptors():
    assert credential_packet(
        Received(ancillary=credentials()), expected_uid=0, expected_pid=123
    ) == {"type": "hello"}
    read_fd, write_fd = os.pipe()
    try:
        ancillary = credentials() + [
            (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [read_fd]).tobytes())
        ]
        with pytest.raises(ValueError):
            credential_packet(Received(ancillary=ancillary), expected_uid=0, expected_pid=123)
        with pytest.raises(OSError):
            os.fstat(read_fd)
    finally:
        os.close(write_fd)


@pytest.mark.parametrize("data", [b"", b"x" * 1025, b"[]", b"null", b"\xff", b"[" * 1024])
def test_packet_shape_and_bytes_are_bounded(data):
    with pytest.raises(ValueError):
        decode_packet(data)
    with pytest.raises(ValueError):
        encode_packet({"name": "x" * 1025})


def test_pid_record_has_bounded_owned_metadata_and_rejects_symlinks(tmp_path):
    record = {"pid": os.getpid(), "session": "a" * 32, "started_ns": time.monotonic_ns()}
    target = tmp_path / "producer.json"
    write_producer_record(target, record)
    assert producer_record(target, os.getuid()) == record
    target.chmod(0o666)
    with pytest.raises(ValueError):
        producer_record(target, os.getuid())
    target.unlink()
    target.symlink_to(tmp_path / "missing")
    with pytest.raises((OSError, ValueError)):
        producer_record(target, os.getuid())
    with pytest.raises(ValueError):
        write_producer_record(target, record)


LINUX = pytest.mark.skipif(not hasattr(socket, "SO_PASSCRED"), reason="Linux Unix credentials")


def client_fixture(tmp_path, repository):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    record_path = tmp_path / "producer.json"
    record = {"pid": os.getpid(), "session": "d" * 32, "started_ns": time.monotonic_ns()}
    write_producer_record(record_path, record)
    receiver = DecisionReceiver(
        repository, directory / "events.sock", record_path, trusted_uid=os.getuid()
    )
    client = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    client.connect(str(receiver.path))
    client.settimeout(0.5)
    generation = repository.access_plan().digest()
    client.send(
        encode_packet(
            {
                "type": "hello",
                "session": record["session"],
                "started_ns": record["started_ns"],
                "generation": generation,
            }
        )
    )
    receiver.poll(snapshot(), 100)
    challenge = decode_packet(client.recv(1024))
    client.send(
        encode_packet(
            {
                "type": "ack",
                "nonce": challenge["nonce"],
                "session": record["session"],
                "generation": generation,
            }
        )
    )
    receiver.poll(snapshot(), 100)
    assert receiver.active
    return receiver, client, challenge["nonce"], record, generation


@LINUX
def test_real_credentials_nonce_replay_and_privacy_session_restart_barrier(tmp_path):
    with ConfigRepository(":memory:") as repository:
        receiver, client, nonce, record, generation = client_fixture(tmp_path, repository)
        packet = {
            "type": "event",
            "nonce": nonce,
            "session": record["session"],
            "generation": generation,
            "sequence": 1,
            "client": IP,
            "name": "blocked.example",
            "kind": "blocked_decisions",
            "at": 100,
        }
        try:
            client.send(encode_packet(packet))
            client.send(encode_packet(packet))
            receiver.poll(snapshot(), 100)
            assert next(iter(repository._dns_outcomes.rows.values()))["blocked_decisions"] == 1
            client.send(encode_packet(dict(packet, sequence=2)))
            repository.save_unaffected_device(UnaffectedDevice(MAC))
            receiver.poll(snapshot(), 100)
            assert not repository._dns_outcomes.rows and not receiver.active
            repository.delete_unaffected_device(MAC)
            assert record["started_ns"] < repository._dns_outcomes.minimum_started_ns
        finally:
            client.close()
            receiver.close()


@LINUX
def test_wrong_nonce_disconnects_without_aggregating(tmp_path):
    with ConfigRepository(":memory:") as repository:
        receiver, client, _, record, generation = client_fixture(tmp_path, repository)
        try:
            client.send(
                encode_packet(
                    {
                        "type": "event",
                        "nonce": "e" * 32,
                        "session": record["session"],
                        "generation": generation,
                        "sequence": 1,
                        "client": IP,
                        "name": "private.example",
                        "kind": "blocked_decisions",
                        "at": 100,
                    }
                )
            )
            receiver.poll(snapshot(), 100)
            assert not receiver.active and not repository._dns_outcomes.rows
        finally:
            client.close()
            receiver.close()


@LINUX
def test_socket_single_owner_and_cleanup_never_remove_foreign_paths(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "events.sock"
    with ConfigRepository(":memory:") as repository:
        path.write_text("keep")
        with pytest.raises(ValueError):
            DecisionReceiver(repository, path)
        assert path.read_text() == "keep"
        path.unlink()
        receiver = DecisionReceiver(repository, path)
        try:
            with pytest.raises(OSError):
                DecisionReceiver(repository, path)
            path.unlink()
            path.write_text("replacement")
        finally:
            receiver.close()
        assert path.read_text() == "replacement"
    directory.chmod(0o777)
    with ConfigRepository(":memory:") as repository, pytest.raises(ValueError):
        DecisionReceiver(repository, path)


@LINUX
def test_receiver_sqlite_contention_keeps_private_traffic_read_available(tmp_path):
    from jittersim.api.server import ControlServer
    from jittersim.config.models import DomainObservation

    path = tmp_path / "settings.db"
    with ConfigRepository(path) as repository, sqlite3.connect(path) as external:
        repository.save_domain_observations(
            [DomainObservation(IP, "ordinary.example", 7, 100, 100, MAC)], now=100
        )
        receiver, client, nonce, record, generation = client_fixture(tmp_path, repository)
        server = ControlServer.__new__(ControlServer)
        server.repository = repository
        server._dns_outcome_receiver = receiver
        server.helper_client = type(
            "Helper", (), {"connected_device_snapshot": lambda self: snapshot()}
        )()
        try:
            external.execute("BEGIN IMMEDIATE")
            client.send(
                encode_packet(
                    {
                        "type": "event",
                        "nonce": nonce,
                        "session": record["session"],
                        "generation": generation,
                        "sequence": 1,
                        "client": IP,
                        "name": "ordinary.example",
                        "kind": "answered_noerror",
                        "at": 100,
                    }
                )
            )
            status, _, body = server._get_observations("")
            result = json.loads(body)
            assert status == 200
            row = result["devices"][0]["domains"][0]
            assert row["domain"] == "ordinary.example" and row["query_count"] == 7
            assert row["dns_outcomes"] is None
            assert result["dns_outcome_collection"]["status"] == "unavailable"
            assert not receiver.active
        finally:
            external.rollback()
            client.close()
            receiver.close()


@LINUX
def test_elapsed_epoch_requires_new_nonce_and_rejects_old_queued_packets(tmp_path):
    with ConfigRepository(":memory:") as repository:
        receiver, client, old_nonce, record, generation = client_fixture(tmp_path, repository)
        old_packet = {
            "type": "event",
            "nonce": old_nonce,
            "session": record["session"],
            "generation": generation,
            "sequence": 1,
            "client": IP,
            "name": "blocked.example",
            "kind": "blocked_decisions",
            "at": 100,
        }
        try:
            client.send(encode_packet(old_packet))
            receiver.poll(snapshot(), 100)
            receiver.poll(snapshot(), 401)
            assert not receiver.active and not repository._dns_outcomes.rows
            client.close()

            def handshake(now):
                connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                connection.settimeout(0.5)
                connection.connect(str(receiver.path))
                connection.send(
                    encode_packet(
                        {
                            "type": "hello",
                            "session": record["session"],
                            "started_ns": record["started_ns"],
                            "generation": generation,
                        }
                    )
                )
                receiver.poll(snapshot(), now)
                challenge = decode_packet(connection.recv(1024))
                connection.send(
                    encode_packet(
                        {
                            "type": "ack",
                            "nonce": challenge["nonce"],
                            "session": record["session"],
                            "generation": generation,
                        }
                    )
                )
                receiver.poll(snapshot(), now)
                assert receiver.active and challenge["nonce"] != old_nonce
                return connection, challenge["nonce"]

            client, _ = handshake(401)
            client.send(encode_packet(old_packet))
            receiver.poll(snapshot(), 401)
            assert not receiver.active and not repository._dns_outcomes.rows
            client.close()
            client, nonce = handshake(402)
            client.send(encode_packet(dict(old_packet, nonce=nonce)))
            receiver.poll(snapshot(), 402)
            assert not repository._dns_outcomes.rows
            client.send(encode_packet(dict(old_packet, nonce=nonce, sequence=2, at=402)))
            receiver.poll(snapshot(), 402)
            assert next(iter(repository._dns_outcomes.rows.values()))["blocked_decisions"] == 1
        finally:
            client.close()
            receiver.close()

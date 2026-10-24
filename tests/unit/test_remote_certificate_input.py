"""CA input checks use only public certificates, without keys, tokens or requests."""

import argparse
import os
import re
import ssl
from pathlib import Path
from unittest.mock import patch

import certifi
import pytest

from jittersim import remote_cli, remote_client
from jittersim.remote_client import Connection, DashboardClient, RemoteError


@pytest.fixture
def certificates():
    # requests already depends on certifi. These are public trust anchors, not test keys.
    certificates = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
        Path(certifi.where()).read_text(), re.S,
    )[:2]
    assert len(certificates) == 2
    return certificates


def write_certificate(path, contents, mode=0o600):
    path.write_bytes(contents.encode("ascii") if isinstance(contents, str) else contents)
    path.chmod(mode)
    return path


def client(path):
    return DashboardClient(Connection("https://dashboard.example.invalid:8080", path), None)


@pytest.mark.parametrize("format", ["pem", "bundle", "der"])
def test_only_the_checked_public_certificates_are_trusted(tmp_path, certificates, format):
    contents = "\n".join(certificates) if format == "bundle" else certificates[0]
    if format == "der":
        contents = ssl.PEM_cert_to_DER_cert(contents)
    path = write_certificate(tmp_path / "ca.crt", contents)
    trusted = client(path).context.get_ca_certs(binary_form=True)
    expected = certificates if format == "bundle" else certificates[:1]
    assert set(trusted) == {ssl.PEM_cert_to_DER_cert(pem) for pem in expected}


def test_a_symlink_to_a_secure_certificate_is_allowed(tmp_path, certificates):
    target = write_certificate(tmp_path / "target.crt", certificates[0])
    link = tmp_path / "ca.crt"
    link.symlink_to(target)
    assert remote_client.checked_certificate(link) == link
    assert client(link).context.get_ca_certs(binary_form=True) == [
        ssl.PEM_cert_to_DER_cert(certificates[0])
    ]


def test_a_certificate_replaced_at_the_hidden_prompt_is_refused(tmp_path, certificates):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o777)
    shared.chmod(0o777)
    path = write_certificate(shared / "ca.crt", certificates[0])

    def substitute():
        # Model unlink/create through a writable non-sticky parent; no original-inode write.
        path.unlink()
        write_certificate(path, certificates[1], 0o666)
        return None

    arguments = argparse.Namespace(url="https://dashboard.example.invalid:8080", ca_cert=path)
    with patch.object(remote_cli, "read_token", side_effect=substitute) as prompt, patch.object(
        remote_client.ssl, "create_default_context", wraps=ssl.create_default_context
    ) as create:
        with pytest.raises(RemoteError, match="must not be writable by others"):
            remote_cli._client(arguments)
    prompt.assert_called_once()
    create.assert_not_called()


def test_a_path_replaced_after_open_cannot_change_the_loaded_ca(tmp_path, certificates):
    path = write_certificate(tmp_path / "ca.crt", certificates[0])
    original_open = os.open
    descriptors = []

    def open_then_replace(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        descriptors.append(descriptor)
        path.unlink()
        write_certificate(path, certificates[1], 0o666)
        return descriptor

    with patch.object(remote_client.os, "open", side_effect=open_then_replace):
        trusted = client(path).context.get_ca_certs(binary_form=True)
    assert trusted == [ssl.PEM_cert_to_DER_cert(certificates[0])]
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_a_writable_opened_certificate_is_refused(tmp_path, certificates, mode):
    path = write_certificate(tmp_path / "ca.crt", certificates[0], mode)
    with pytest.raises(RemoteError, match="must not be writable by others"):
        client(path)


def test_an_opened_certificate_owned_by_another_uid_is_refused(tmp_path, certificates):
    path = write_certificate(tmp_path / "ca.crt", certificates[0])
    info = list(path.stat())
    info[4] = max(os.geteuid(), 0) + 1
    with patch.object(remote_client.os, "fstat", return_value=os.stat_result(info)):
        with pytest.raises(RemoteError, match="must belong to you"):
            client(path)


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_nonregular_opened_certificates_are_refused_without_blocking(tmp_path, kind):
    path = tmp_path / "ca.crt"
    if kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    with pytest.raises(RemoteError, match="not a regular file"):
        client(path)


@pytest.mark.parametrize("reported_size", [0, remote_client.MAX_CERTIFICATE_BYTES + 1])
def test_oversized_certificates_are_refused_even_when_they_grow(tmp_path, reported_size):
    path = write_certificate(tmp_path / "ca.crt", b"x" * (remote_client.MAX_CERTIFICATE_BYTES + 1))
    info = list(path.stat())
    info[6] = reported_size
    with patch.object(remote_client.os, "fstat", return_value=os.stat_result(info)):
        with pytest.raises(RemoteError, match="too large"):
            client(path)


@pytest.mark.parametrize("contents", [b"", b"invalid certificate", b"\xff-----BEGIN CERTIFICATE-----"])
def test_empty_or_invalid_certificates_never_load_system_trust(tmp_path, contents):
    path = write_certificate(tmp_path / "ca.crt", contents)
    with pytest.raises(RemoteError):
        client(path)


@pytest.mark.parametrize("failure", ["metadata", "read", "mode", "size", "empty"])
def test_certificate_descriptors_close_on_errors(tmp_path, certificates, failure):
    contents = "" if failure == "empty" else certificates[0]
    path = write_certificate(tmp_path / "ca.crt", contents)
    if failure == "mode":
        path.chmod(0o666)
    if failure == "size":
        path.write_bytes(b"x" * (remote_client.MAX_CERTIFICATE_BYTES + 1))
    original_open = os.open
    original_fstat = os.fstat
    original_fdopen = os.fdopen
    descriptors = []

    def record_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    def fstat(descriptor):
        if failure == "metadata":
            raise OSError("metadata detail must not escape")
        return original_fstat(descriptor)

    def fdopen(*args, **kwargs):
        if failure == "read":
            raise OSError("read detail must not escape")
        return original_fdopen(*args, **kwargs)

    with patch.object(remote_client.os, "open", side_effect=record_open), patch.object(
        remote_client.os, "fstat", side_effect=fstat
    ), patch.object(remote_client.os, "fdopen", side_effect=fdopen):
        with pytest.raises(RemoteError) as error:
            client(path)
    assert "detail must not escape" not in error.value.message
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        original_fstat(descriptors[0])

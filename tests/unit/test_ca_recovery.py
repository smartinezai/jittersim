"""Keep the current certificate identity when installation or recovery fails."""

import hashlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim import localca, localname, updates
from tests.unit import test_updates as update_fixture
from tests.tls_support import needs_openssl

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="OpenSSL is required")


def prepare(config):
    config.mkdir(exist_ok=True)
    return localca.prepare(config, host_name="pi-lab.local", neutral_name="netlab.local",
                           ethernet_ip="192.168.1.20")


def ca_cli(arguments):
    config = Path(arguments[arguments.index("--config-dir") + 1])
    return localca.main(arguments, operation=updates.update_lock, update_root=config / "operations")


@pytest.mark.parametrize("command", ["ensure", "recover-adoption", "finish-adoption", "check-health"])
def test_manual_certificate_entrypoints_refuse_a_busy_shared_operation(tmp_path, command):
    root, config = tmp_path / "operations", tmp_path / "config"
    with updates.update_lock(root), patch.object(localca, "_execute", side_effect=AssertionError("mutated identity")):
        assert localca.main([command, "--config-dir", str(config)],
                            operation=updates.update_lock, update_root=root) == 1
    assert not config.exists()


@pytest.mark.parametrize("command", ["ensure", "recover-adoption", "finish-adoption", "check-health"])
def test_default_certificate_entrypoints_refuse_retained_or_unsupported_ownership(tmp_path, command):
    root, config = tmp_path / "operations", tmp_path / "config"
    root.mkdir(mode=0o700)
    (root / "request.json").write_text("malformed retained operation")
    with patch.object(localca, "_execute", side_effect=AssertionError("mutated identity")):
        assert localca.main([command, "--config-dir", str(config)], update_root=root) == 1
    assert not config.exists()


def test_manual_certificate_unknown_owner_escapes_to_the_shared_operation(tmp_path):
    root = tmp_path / "operations"
    error = updates.CommandNotStopped("openssl", object())
    with patch.object(updates, "retain_command", create=True), \
            patch.object(localca, "_execute", side_effect=error), pytest.raises(updates.CommandNotStopped) as raised:
        localca.main(["ensure", "--config-dir", str(tmp_path / "config")],
                     operation=updates.update_lock, update_root=root)
    assert raised.value is error


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(directory):
    return {str(path.relative_to(directory)): digest(path)
            for path in directory.rglob("*") if path.is_file()}


@pytest.mark.parametrize("damage", ["missing-key", "broken-certificate", "mismatched-key"])
def test_damaged_existing_authority_is_refused_without_rotating_it(tmp_path, damage):
    config = tmp_path / "config"
    prepare(config)
    if damage == "missing-key":
        (config / "ca/ca.key").unlink()
    elif damage == "broken-certificate":
        (config / "ca/ca.crt").write_text("damaged certificate")
    else:
        other = tmp_path / "other"
        prepare(other)
        shutil.copyfile(other / "ca/ca.key", config / "ca/ca.key")
    before = identity(config)

    with pytest.raises(localca.TrustError):
        localca.ensure_authority(config / "ca")

    assert identity(config) == before


def test_authority_permissions_failure_is_visible(tmp_path):
    prepare(tmp_path)
    with patch.object(localca.os, "geteuid", return_value=0), \
            patch.object(localca.shutil, "chown", side_effect=PermissionError("denied")):
        with pytest.raises((localca.TrustError, OSError)):
            localca.set_permissions(tmp_path, "dashboard-group")


def test_existing_custom_legacy_address_remains_in_dns(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    saved = config / "trust.env"
    saved.write_text("JITTERSIM_LOCAL_NAME=custom-dashboard.home.arpa\n")
    dns = tmp_path / "dns"
    dns.mkdir()

    localname.install_config(config, dns)

    assert "custom-dashboard.home.arpa" in (dns / localname.SHARED_DNS_FILE).read_text()
    prepare(config)
    for name in ("dashboard.crt", "dashboard-wifi.crt"):
        assert "DNS:custom-dashboard.home.arpa" in localca._alternative_names(config / name)


def test_invalid_explicit_name_is_refused_instead_of_defaulted(tmp_path):
    saved = tmp_path / "trust.env"
    saved.write_text("JITTERSIM_DASHBOARD_NAME=unknown.example.com\n")
    before = digest(saved)

    with pytest.raises(ValueError):
        localname.read_local_name(saved)

    assert digest(saved) == before


def test_fresh_certificate_does_not_add_a_branded_alias(tmp_path):
    prepare(tmp_path)
    text = subprocess.check_output([
        "openssl", "x509", "-in", str(tmp_path / "dashboard.crt"), "-noout", "-text"
    ], text=True)
    assert "jittersim.local" not in text


def test_rollback_keeps_current_authority_when_backup_has_another(tmp_path):
    fixture = update_fixture.UpdateTests(methodName="test_request_names_only_an_approved_release_and_cannot_queue_twice")
    fixture.setUp()
    try:
        prepare(fixture.config)
        old = digest(fixture.config / "ca/ca.crt")
        fixture.install()
        current = tmp_path / "current"
        prepare(current)
        for name in ("ca/ca.crt", "ca/ca.key", "dashboard.crt", "dashboard.key", "dashboard-wifi.crt"):
            shutil.copyfile(current / name, fixture.config / name)
        expected = digest(current / "ca/ca.crt")
        assert expected != old

        fixture.rollback()

        assert digest(fixture.config / "ca/ca.crt") == expected
        assert digest(fixture.config / "dashboard.crt") == digest(current / "dashboard.crt")
        assert digest(fixture.config / "dashboard.key") == digest(current / "dashboard.key")
    finally:
        fixture.tearDown()


def transaction(label):
    return hashlib.sha256(label.encode()).hexdigest()


def retained(config):
    return {name: digest(config / name) for name in updates.CERTIFICATE_FILES
            if (config / name).is_file()}


def legacy(config, *, repeated_name=False):
    config.mkdir(exist_ok=True)
    request = config / "legacy-request.conf"
    request.write_text("[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=leaf\n"
                       "[dn]\nCN=old-dashboard.local\n[leaf]\n"
                       "subjectAltName=DNS:old-dashboard.local," +
                       ("DNS:old-dashboard.local," if repeated_name else "") + "IP:192.168.50.1\n"
                       "extendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                    "-nodes", "-days", "1", "-config", str(request), "-keyout", str(config / "dashboard.key"),
                    "-out", str(config / "dashboard.crt")], check=True, capture_output=True)
    (config / "dashboard.key").chmod(0o600)
    (config / "trust.env").write_text("JITTERSIM_LOCAL_NAME=old-dashboard.local\n")
    request.unlink()


def test_legacy_repeated_dns_names_preserve_the_exact_owned_identity(tmp_path):
    legacy(tmp_path, repeated_name=True)
    files = updates._certificate_live_files(tmp_path)
    assert updates.verify_certificate_identity(files) == "legacy"
    before = retained(tmp_path)
    root = tmp_path / "recovery"
    root.mkdir(mode=0o700)
    pointer = updates.keep_certificate_identity(root, tmp_path, transaction("restore"))
    (tmp_path / "dashboard.crt").unlink()
    updates.restore_certificate_identity(root, tmp_path, pointer)
    assert retained(tmp_path) == before


def test_legacy_repeated_dns_names_still_require_the_matching_private_key(tmp_path):
    legacy(tmp_path, repeated_name=True)
    other = tmp_path / "other"
    legacy(other)
    files = updates._certificate_live_files(tmp_path)
    files["dashboard.key"] = (other / "dashboard.key").read_bytes()
    with pytest.raises(updates.UpdateError, match="does not match"):
        updates.verify_certificate_identity(files)


def test_repeated_dns_names_are_strict_unless_adopting_a_bounded_legacy_identity():
    text = "X509v3 Subject Alternative Name:\n    DNS:old-dashboard.local, DNS:old-dashboard.local\n"
    with pytest.raises(updates.UpdateError, match="names are invalid"):
        updates._certificate_names(text)
    assert updates._certificate_names(text, allow_legacy_repeated_names=True) == ("old-dashboard.local",)
    oversized = "X509v3 Subject Alternative Name:\n    " + ", ".join(["DNS:old-dashboard.local"] * 33) + "\n"
    with pytest.raises(updates.UpdateError, match="names are invalid"):
        updates._certificate_names(oversized, allow_legacy_repeated_names=True)


def test_retry_uses_the_durable_identity_after_partial_live_restoration(tmp_path):
    config, root = tmp_path / "config", tmp_path / "recovery"
    prepare(config)
    root.mkdir()
    expected = retained(config)
    pointer = updates.keep_certificate_identity(root, config, transaction("restore"))
    (config / "dashboard.key").unlink()
    shutil.rmtree(config / "ca")

    assert updates.keep_certificate_identity(root, config, transaction("restore")) == pointer
    updates.restore_certificate_identity(root, config, pointer)

    assert retained(config) == expected
    assert json.loads((root / "certificate-recovery/current.json").read_text())["active"]
    updates.finish_certificate_recovery(root)
    assert not json.loads((root / "certificate-recovery/current.json").read_text())["active"]


def test_next_restore_captures_the_new_live_identity(tmp_path):
    config, root = tmp_path / "config", tmp_path / "recovery"
    prepare(config)
    root.mkdir()
    updates.keep_certificate_identity(root, config, transaction("first"))
    updates.finish_certificate_recovery(root)
    other = tmp_path / "other"
    prepare(other)
    for name in updates.CERTIFICATE_FILES:
        if (other / name).exists():
            shutil.copyfile(other / name, config / name)
    expected = retained(config)

    pointer = updates.keep_certificate_identity(root, config, transaction("second"))
    (config / "dashboard.crt").unlink()
    updates.restore_certificate_identity(root, config, pointer)

    assert retained(config) == expected
    assert len(list((root / "certificate-recovery").glob("[ab]"))) == 1


@pytest.mark.parametrize("boundary", ["dashboard.key", "complete.json", "current.json"])
def test_interrupted_capture_retries_without_discarding_the_previous_record(tmp_path, boundary):
    config, root = tmp_path / "config", tmp_path / "recovery"
    prepare(config)
    root.mkdir()
    updates.keep_certificate_identity(root, config, transaction("first"))
    updates.finish_certificate_recovery(root)
    before = digest(root / "certificate-recovery/current.json")
    write = updates._certificate_write

    def interrupted(path, content, **kwargs):
        if path.name == boundary:
            raise OSError("interrupted capture")
        return write(path, content, **kwargs)

    with patch.object(updates, "_certificate_write", side_effect=interrupted):
        with pytest.raises(OSError, match="interrupted capture"):
            updates.keep_certificate_identity(root, config, transaction("second"))
    assert digest(root / "certificate-recovery/current.json") == before
    pointer = updates.keep_certificate_identity(root, config, transaction("second"))
    updates.restore_certificate_identity(root, config, pointer)
    assert len(list((root / "certificate-recovery").glob("[ab]"))) == 1


def test_restore_permission_failure_keeps_a_retryable_active_record(tmp_path):
    config, root = tmp_path / "config", tmp_path / "recovery"
    prepare(config)
    root.mkdir()
    expected = retained(config)
    pointer = updates.keep_certificate_identity(root, config, transaction("permissions"))
    with patch.object(updates.os, "chown", side_effect=PermissionError("denied")):
        with pytest.raises(updates.UpdateError, match="permissions"):
            updates.restore_certificate_identity(root, config, pointer)
    assert json.loads((root / "certificate-recovery/current.json").read_text())["active"]
    updates.restore_certificate_identity(root, config, pointer)
    assert retained(config) == expected


def test_complete_legacy_identity_restores_explicit_authority_absence(tmp_path):
    config, root = tmp_path / "config", tmp_path / "recovery"
    legacy(config)
    root.mkdir()
    expected = retained(config)
    pointer = updates.keep_certificate_identity(root, config, transaction("legacy"))
    other = tmp_path / "other"
    prepare(other)
    shutil.copytree(other / "ca", config / "ca")
    for name in ("dashboard.crt", "dashboard.key", "dashboard-wifi.crt"):
        shutil.copyfile(other / name, config / name)

    updates.restore_certificate_identity(root, config, pointer)

    assert not (config / "ca").exists()
    assert not (config / "dashboard-wifi.crt").exists()
    assert retained(config) == expected


@pytest.mark.parametrize("kind", ["link", "fifo", "oversized"])
def test_capture_refuses_unsafe_material_without_publishing_a_pointer(tmp_path, kind):
    config, root = tmp_path / "config", tmp_path / "recovery"
    prepare(config)
    root.mkdir()
    path = config / "dashboard.key"
    path.unlink()
    if kind == "link":
        path.symlink_to(config / "ca/ca.key")
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    else:
        path.write_bytes(b"x" * (updates.CERTIFICATE_LIMIT + 1))
    with pytest.raises((updates.UpdateError, OSError)):
        updates.keep_certificate_identity(root, config, transaction(kind))
    assert not (root / "certificate-recovery/current.json").exists()


def test_crypto_operations_share_one_total_deadline(tmp_path):
    prepare(tmp_path)
    files = updates._certificate_live_files(tmp_path)
    timeouts = []
    run = updates.capture_command

    def measured(command, timeout, **kwargs):
        timeouts.append(timeout)
        return run(command, timeout, **kwargs)

    with patch.object(updates, "capture_command", side_effect=measured):
        assert updates.verify_certificate_identity(files, deadline=time.monotonic() + 2) == "ca"
    assert len(timeouts) > 5
    assert all(0 < timeout <= 2 for timeout in timeouts)
    assert timeouts == sorted(timeouts, reverse=True)


def test_expired_budget_prevents_capture_and_its_side_effects(tmp_path):
    prepare(tmp_path)

    class Expired:
        def check(self):
            raise updates.UpdateError("overall recovery budget expired")

        def limit(self, seconds):
            return 0

    with pytest.raises(updates.UpdateError, match="overall recovery budget"):
        updates.keep_certificate_identity(tmp_path / "recovery", tmp_path,
                                          transaction("expired"), budget=Expired())
    assert not (tmp_path / "recovery").exists()


@needs_openssl
def test_restored_identity_serves_tls_to_a_client_with_only_the_retained_root(tmp_path):
    config, root = tmp_path / "config", tmp_path / "recovery"
    prepare(config)
    root.mkdir()
    pointer = updates.keep_certificate_identity(root, config, transaction("tls"))
    (config / "dashboard.key").unlink()
    updates.restore_certificate_identity(root, config, pointer)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(config / "dashboard.crt", config / "dashboard.key")
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.load_verify_locations(cafile=str(config / "ca/ca.crt"))
    errors = []
    with socket.socket() as listener:
        listener.settimeout(3)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        def accept():
            try:
                connection, _ = listener.accept()
                connection.settimeout(3)
                with server.wrap_socket(connection, server_side=True) as secured:
                    secured.sendall(secured.recv(4))
            except Exception as error:
                errors.append(type(error).__name__)

        worker = threading.Thread(target=accept)
        worker.start()
        try:
            with socket.create_connection(listener.getsockname(), timeout=3) as connection:
                with client.wrap_socket(connection, server_hostname="netlab.local") as secured:
                    secured.sendall(b"ping")
                    assert secured.recv(4) == b"ping"
        finally:
            worker.join(timeout=4)
        assert not worker.is_alive()
        assert not errors


def test_diagnostic_representations_do_not_contain_private_key_bytes(tmp_path):
    prepare(tmp_path)
    files = updates._certificate_live_files(tmp_path)
    key = (tmp_path / "ca/ca.key").read_text()
    assert key not in repr(files)
    assert key not in repr(files["ca/ca.key"])


@pytest.mark.parametrize("boundary", ["ca/ca.crt", "ca/ca.key", "dashboard.crt", "dashboard.key", "dashboard-wifi.crt"])
def test_first_adoption_interruption_owns_every_published_file(tmp_path, boundary):
    legacy(tmp_path)
    expected = retained(tmp_path)
    write = updates._certificate_write

    def interrupted(path, content, **kwargs):
        result = write(path, content, **kwargs)
        if path == tmp_path / boundary:
            raise OSError("interrupted adoption publication")
        return result

    with patch.object(updates, "_certificate_write", side_effect=interrupted):
        with pytest.raises(localca.TrustError):
            prepare(tmp_path)
    proof = json.loads((tmp_path / ".certificate-adoption/adoption.json").read_text())
    assert proof["original"] == expected
    assert set(proof["candidate"]) >= {"ca/ca.crt", "ca/ca.key", "dashboard.crt", "dashboard.key", "dashboard-wifi.crt"}
    if boundary == "dashboard-wifi.crt":
        assert not updates.recover_incomplete_certificate_adoption(tmp_path)
        assert updates.verify_certificate_identity(updates._certificate_live_files(tmp_path)) == "ca"
    else:
        assert updates.recover_incomplete_certificate_adoption(tmp_path)
        assert retained(tmp_path) == expected
        assert not (tmp_path / "ca").exists()


def test_adoption_restore_interruption_retries_the_same_legacy_generation(tmp_path):
    legacy(tmp_path)
    expected = retained(tmp_path)
    write = updates._certificate_write

    def fail_adoption(path, content, **kwargs):
        result = write(path, content, **kwargs)
        if path == tmp_path / "dashboard.crt":
            raise OSError("adoption interrupted")
        return result

    with patch.object(updates, "_certificate_write", side_effect=fail_adoption):
        with pytest.raises(localca.TrustError):
            prepare(tmp_path)

    def fail_restore(path, content, **kwargs):
        result = write(path, content, **kwargs)
        if path == tmp_path / "dashboard.key":
            raise OSError("restoration interrupted")
        return result

    with patch.object(updates, "_certificate_write", side_effect=fail_restore):
        with pytest.raises(OSError, match="restoration interrupted"):
            updates.recover_incomplete_certificate_adoption(tmp_path)
    assert updates.recover_incomplete_certificate_adoption(tmp_path)
    assert retained(tmp_path) == expected
    with pytest.raises(localca.TrustError, match="earlier certificate adoption"):
        prepare(tmp_path)


def test_adoption_refuses_an_unrelated_manual_authority(tmp_path):
    legacy(tmp_path)
    write = updates._certificate_write

    def interrupted(path, content, **kwargs):
        result = write(path, content, **kwargs)
        if path == tmp_path / "ca/ca.key":
            raise OSError("adoption interrupted")
        return result

    with patch.object(updates, "_certificate_write", side_effect=interrupted):
        with pytest.raises(localca.TrustError):
            prepare(tmp_path)
    other = tmp_path / "other"
    prepare(other)
    shutil.copyfile(other / "ca/ca.crt", tmp_path / "ca/ca.crt")
    before = retained(tmp_path)
    with pytest.raises(updates.UpdateError, match="unrelated current"):
        updates.recover_incomplete_certificate_adoption(tmp_path)
    assert retained(tmp_path) == before


def test_retired_adoption_cannot_restore_legacy_after_a_later_identity_change(tmp_path):
    legacy(tmp_path)
    prepare(tmp_path)
    adoption = tmp_path / ".certificate-adoption"
    first = json.loads((adoption / "adoption.json").read_text())["transaction"]
    updates.finish_certificate_recovery(adoption)
    (tmp_path / "ca/ca.crt").write_text("unrelated authority")
    before = retained(tmp_path)
    assert not updates.recover_incomplete_certificate_adoption(tmp_path)
    assert retained(tmp_path) == before
    shutil.rmtree(tmp_path / "ca")
    (tmp_path / "dashboard-wifi.crt").unlink()
    legacy(tmp_path)
    prepare(tmp_path)
    second = json.loads((adoption / "adoption.json").read_text())["transaction"]
    assert second != first


def test_adoption_resume_keeps_the_original_candidate_authority(tmp_path):
    legacy(tmp_path)
    write = updates._certificate_write

    def interrupted(path, content, **kwargs):
        result = write(path, content, **kwargs)
        if path == tmp_path / "ca/ca.key":
            raise OSError("adoption interrupted")
        return result

    with patch.object(updates, "_certificate_write", side_effect=interrupted):
        with pytest.raises(localca.TrustError):
            prepare(tmp_path)
    expected = digest(tmp_path / "ca/ca.crt")
    prepare(tmp_path)
    assert digest(tmp_path / "ca/ca.crt") == expected
    assert updates.verify_certificate_identity(updates._certificate_live_files(tmp_path)) == "ca"


def test_manual_recovery_cli_requires_health_before_a_fresh_adoption(tmp_path):
    legacy(tmp_path)
    write = updates._certificate_write

    def interrupted(path, content, **kwargs):
        result = write(path, content, **kwargs)
        if path == tmp_path / "dashboard.crt":
            raise OSError("publication interrupted")
        return result

    with patch.object(updates, "_certificate_write", side_effect=interrupted):
        with pytest.raises(localca.TrustError):
            prepare(tmp_path)
    first = json.loads((tmp_path / ".certificate-adoption/adoption.json").read_text())["transaction"]
    with patch.object(updates, "healthy", return_value=False):
        assert ca_cli(["recover-adoption", "--config-dir", str(tmp_path)]) == 1
    with pytest.raises(localca.TrustError):
        prepare(tmp_path)

    with patch.object(updates, "healthy", return_value=True):
        assert ca_cli(["recover-adoption", "--config-dir", str(tmp_path)]) == 0
    assert ca_cli(["ensure", "--config-dir", str(tmp_path), "--neutral-name", "netlab.local",
                         "--ethernet-ip", "192.168.1.20"]) == 0
    assert json.loads((tmp_path / ".certificate-adoption/adoption.json").read_text())["transaction"] != first


def test_adoption_cannot_retire_before_every_owned_file_is_complete(tmp_path):
    legacy(tmp_path)
    write = updates._certificate_write

    def interrupted(path, content, **kwargs):
        result = write(path, content, **kwargs)
        if path == tmp_path / "ca/ca.key":
            raise OSError("publication interrupted")
        return result

    with patch.object(updates, "_certificate_write", side_effect=interrupted):
        with pytest.raises(localca.TrustError):
            prepare(tmp_path)
    with pytest.raises(updates.UpdateError, match="has not completed"):
        updates.finish_certificate_recovery(tmp_path / ".certificate-adoption")


def test_completed_candidate_retry_defers_dhcp_until_health_then_keeps_the_authority(tmp_path, capsys):
    legacy(tmp_path)
    prepare(tmp_path)
    authority = digest(tmp_path / "ca/ca.crt")
    key = digest(tmp_path / "dashboard.key")
    assert ca_cli(["recover-adoption", "--config-dir", str(tmp_path)]) == 0
    assert ca_cli(["ensure", "--config-dir", str(tmp_path), "--neutral-name", "netlab.local",
                         "--ethernet-ip", "192.168.1.21"]) == 0
    assert "192.168.1.21" not in localca._alternative_names(tmp_path / "dashboard.crt")
    assert capsys.readouterr().out
    with patch.object(updates, "healthy", return_value=True):
        assert ca_cli(["finish-adoption", "--config-dir", str(tmp_path)]) == 0
    assert ca_cli(["ensure", "--config-dir", str(tmp_path), "--neutral-name", "netlab.local",
                         "--ethernet-ip", "192.168.1.21"]) == 0
    assert "IP Address:192.168.1.21" in localca._alternative_names(tmp_path / "dashboard.crt")
    assert digest(tmp_path / "ca/ca.crt") == authority
    assert digest(tmp_path / "dashboard.key") == key
    with patch.object(updates, "healthy", return_value=False):
        assert ca_cli(["check-health", "--config-dir", str(tmp_path)]) == 1
    with patch.object(updates, "healthy", return_value=True):
        assert ca_cli(["check-health", "--config-dir", str(tmp_path)]) == 0


def test_delayed_health_cannot_retire_a_different_adoption_transition(tmp_path):
    legacy(tmp_path)
    prepare(tmp_path)
    root = tmp_path / ".certificate-adoption"
    namespace = root / "certificate-recovery"
    pointer = json.loads((namespace / "current.json").read_text())
    next_id = transaction("replacement during health")

    def delayed_health(**kwargs):
        pointer["transaction"] = next_id
        proof = json.loads((root / "adoption.json").read_text())
        proof["transaction"] = next_id
        manifest_path = namespace / pointer["slot"] / "complete.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["transaction"] = next_id
        for path, record in ((namespace / "current.json", pointer), (root / "adoption.json", proof),
                             (manifest_path, manifest)):
            updates._certificate_write(path, json.dumps(record).encode())
        return True

    with patch.object(updates, "healthy", side_effect=delayed_health):
        assert ca_cli(["finish-adoption", "--config-dir", str(tmp_path)]) == 1
    current = json.loads((namespace / "current.json").read_text())
    assert current["active"] and current["transaction"] == next_id


def test_specialized_unknown_ownership_failure_is_not_a_damaged_authority(tmp_path):
    prepare(tmp_path)

    class OwnershipUnknown(updates.UpdateError):
        pass

    before = retained(tmp_path)
    with patch.object(updates, "verify_certificate_identity", side_effect=OwnershipUnknown("unknown child ownership")):
        with pytest.raises(OwnershipUnknown):
            localca.ensure_authority(tmp_path / "ca")
    assert retained(tmp_path) == before


def test_specialized_verification_failure_cannot_start_adoption_recovery(tmp_path):
    legacy(tmp_path)
    prepare(tmp_path)
    before = retained(tmp_path)
    original = updates.verify_certificate_identity
    calls = 0

    class OwnershipUnknown(updates.UpdateError):
        pass

    def unknown_current(files, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OwnershipUnknown("unknown child ownership")
        return original(files, **kwargs)

    with patch.object(updates, "verify_certificate_identity", side_effect=unknown_current):
        with pytest.raises(OwnershipUnknown):
            updates.recover_incomplete_certificate_adoption(tmp_path)
    assert retained(tmp_path) == before
    assert json.loads((tmp_path / ".certificate-adoption/adoption.json").read_text())["state"] == "publishing"


def test_a_ca_signed_leaf_without_its_authority_is_not_a_legacy_identity(tmp_path):
    prepare(tmp_path)
    files = updates._certificate_live_files(tmp_path)
    standalone = {name: files[name] for name in ("dashboard.crt", "dashboard.key")}
    with pytest.raises(updates.UpdateError, match="not self-signed"):
        updates.verify_certificate_identity(standalone)


def test_a_leaf_from_an_unrelated_authority_is_refused_by_the_retained_root(tmp_path):
    prepare(tmp_path)
    other = tmp_path / "other"
    prepare(other)
    files = updates._certificate_live_files(tmp_path)
    foreign = updates._certificate_live_files(other)
    for name in ("dashboard.crt", "dashboard.key", "dashboard-wifi.crt"):
        files[name] = foreign[name]
    with pytest.raises(updates.UpdateError, match="verification failed"):
        updates.verify_certificate_identity(files)


@pytest.mark.parametrize("days", [366, 7301])
def test_real_chain_verification_refuses_expired_leaf_or_authority(tmp_path, days):
    prepare(tmp_path)
    command = updates._certificate_command
    future = str(int(time.time()) + days * 86400)

    def future_verification(arguments, deadline):
        if arguments[0] == "verify":
            arguments = [arguments[0], "-attime", future, *arguments[1:]]
        return command(arguments, deadline)

    files = updates._certificate_live_files(tmp_path)
    before = retained(tmp_path)
    with patch.object(updates, "_certificate_command", side_effect=future_verification):
        with pytest.raises(updates.UpdateError, match="verification failed"):
            updates.verify_certificate_identity(files)
    assert retained(tmp_path) == before


@pytest.mark.parametrize("unsafe", ["not-critical", "subordinate-authority", "external-domain"])
def test_actual_unsafe_ca_constraints_are_refused(tmp_path, unsafe):
    prepare(tmp_path)
    configuration = localca._authority_config("192.168.1.20")
    if unsafe == "not-critical":
        configuration = configuration.replace("basicConstraints=critical,", "basicConstraints=")
    elif unsafe == "subordinate-authority":
        configuration = configuration.replace("pathlen:0", "pathlen:1")
    else:
        configuration = configuration.replace("permitted;DNS:local", "permitted;DNS:example.com")
    request = tmp_path / "unsafe-ca.conf"
    request.write_text(configuration)
    certificate = tmp_path / "unsafe-ca.crt"
    subprocess.run(["openssl", "req", "-new", "-x509", "-key", str(tmp_path / "ca/ca.key"),
                    "-sha256", "-days", "365", "-config", str(request), "-out", str(certificate)],
                   check=True, capture_output=True)
    files = updates._certificate_live_files(tmp_path)
    files["ca/ca.crt"] = certificate.read_bytes()
    with pytest.raises(updates.UpdateError, match="constraints"):
        updates.verify_certificate_identity(files)


def test_boolean_format_is_not_a_valid_recovery_version(tmp_path):
    config, root = tmp_path / "config", tmp_path / "recovery"
    prepare(config)
    root.mkdir()
    updates.keep_certificate_identity(root, config, transaction("format"))
    path = root / "certificate-recovery/current.json"
    pointer = json.loads(path.read_text())
    pointer["format"] = True
    updates._certificate_write(path, json.dumps(pointer).encode())
    with pytest.raises(updates.UpdateError, match="pointer"):
        updates.keep_certificate_identity(root, config, transaction("format"))


def test_direct_prepare_routes_all_crypto_through_the_owning_runner(tmp_path):
    legacy(tmp_path)
    commands = []
    deadline = time.monotonic() + 20

    class Budget:
        def check(self):
            assert time.monotonic() < deadline

        def limit(self, seconds):
            return min(seconds, deadline - time.monotonic())

    def owned(command, timeout, **kwargs):
        assert command[0] == "openssl"
        assert 0 < timeout <= deadline - time.monotonic() + 0.01
        commands.append(command)
        return updates.capture_command(command, timeout, **kwargs)

    with patch.object(localca.subprocess, "run", side_effect=AssertionError("unowned subprocess")):
        localca.prepare(tmp_path, host_name="ignored.local", neutral_name="netlab.local",
                        ethernet_ip="192.168.1.20", budget=Budget(), run=owned)
    assert len(commands) > 15
    assert any("verify" in command for command in commands)
    assert updates._CERTIFICATE_TIME.get() is None
    assert updates._CERTIFICATE_RUN.get() is None


def test_direct_prepare_expired_parent_has_no_filesystem_effect(tmp_path):
    config = tmp_path / "absent"

    class Expired:
        def check(self):
            raise updates.UpdateError("parent expired")

        def limit(self, seconds):
            raise AssertionError("already expired")

    with pytest.raises(updates.UpdateError, match="parent expired"):
        localca.prepare(config, host_name="ignored.local", neutral_name="netlab.local",
                        ethernet_ip=None, budget=Expired(), run=lambda *a, **k: pytest.fail("ran command"))
    assert not config.exists()


def test_direct_adoption_health_failure_keeps_evidence_then_success_retires(tmp_path):
    legacy(tmp_path)
    prepare(tmp_path)
    pointer = tmp_path / ".certificate-adoption/certificate-recovery/current.json"
    with pytest.raises(localca.TrustError, match="passed health"):
        localca.complete_adoption(tmp_path, health=lambda: False)
    assert json.loads(pointer.read_text())["active"]
    commands = []

    def owned(command, timeout, **kwargs):
        commands.append(command)
        assert command[0] == "openssl"
        return updates.capture_command(command, timeout, **kwargs)

    assert localca.complete_adoption(tmp_path, health=lambda: True, run=owned)
    assert commands
    assert not json.loads(pointer.read_text())["active"]


def test_direct_health_unknown_command_owner_never_retires_adoption(tmp_path):
    legacy(tmp_path)
    prepare(tmp_path)
    pointer = tmp_path / ".certificate-adoption/certificate-recovery/current.json"
    error = updates.CommandNotStopped("health", object())

    def health():
        raise error

    with pytest.raises(updates.CommandNotStopped) as raised:
        localca.complete_adoption(tmp_path, health=health)
    assert raised.value is error
    assert json.loads(pointer.read_text())["active"]

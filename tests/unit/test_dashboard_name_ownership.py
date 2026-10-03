"""A manual rename owns direct commands and excludes configuration replacement."""

import hashlib
from unittest.mock import patch

import pytest

from jittersim import dashboard_name_cli, localca, localname, updates


class UncertainLaunch(updates.UpdateError):
    """An update-manager launch whose completion cannot be established."""


def identity(config):
    return {name: hashlib.sha256((config / name).read_bytes()).hexdigest()
            for name in updates.CERTIFICATE_FILES if (config / name).exists()}


def run_cli(config, root, run, **kwargs):
    operation = kwargs.pop("operation", updates.update_lock)
    with patch.object(dashboard_name_cli, "GROUP", None), \
            patch.object(localca, "detect_ethernet_ip", return_value="192.168.1.20"):
        return dashboard_name_cli.main(["set", "bench.local", "--yes"], euid=lambda: 0,
                                       config_dir=config, update_root=root, dns_dir=config / "dns",
                                       run=run, operation=operation, **kwargs)


def test_actual_rename_keeps_authority_and_owns_every_crypto_and_restart(tmp_path):
    config, root = tmp_path / "config", tmp_path / "updates"
    localca.prepare(config, host_name="ignored.local", neutral_name="netlab.local", ethernet_ip="192.168.1.20")
    localname.set_local_name("netlab.local", config)
    authority = identity(config)["ca/ca.crt"]
    commands = []

    def owned(command, timeout, **kwargs):
        with pytest.raises(updates.UpdateError, match="already running"):
            with updates.update_lock(root):
                pytest.fail("a concurrent configuration writer entered")
        commands.append(command)
        if command[0] == "openssl":
            return updates.capture_command(command, timeout, **kwargs)
        assert command[:2] == ["systemctl", "restart"]
        return b""

    assert run_cli(config, root, owned) == 0
    assert localname.read_local_name(config / "trust.env") == "bench.local"
    assert identity(config)["ca/ca.crt"] == authority
    assert "DNS:bench.local" in localca._alternative_names(config / "dashboard.crt")
    assert "bench.local" in (config / "dns" / localname.SHARED_DNS_FILE).read_text()
    assert [command[-1] for command in commands if command[0] == "systemctl"] == [
        "jittersim-mdns.service", "jittersim-dashboard.service"]


@pytest.mark.parametrize("uncertain_launch", [False, True])
def test_unknown_certificate_command_owner_does_not_save_or_restart(tmp_path, uncertain_launch):
    config, root = tmp_path / "config", tmp_path / "updates"
    localca.prepare(config, host_name="ignored.local", neutral_name="netlab.local", ethernet_ip="192.168.1.20")
    localname.set_local_name("netlab.local", config)
    before = identity(config)
    error = (UncertainLaunch("launch ownership is unknown") if uncertain_launch else
             updates.CommandNotStopped("openssl", object()))

    def unknown(command, timeout, **kwargs):
        assert command[0] == "openssl"
        raise error

    # The manager's Linux tests cover its durable process fence. This case
    # isolates the CLI's propagation of the same error without a real process.
    with patch.object(updates, "retain_command", create=True), pytest.raises(type(error)) as raised:
        run_cli(config, root, unknown)
    assert raised.value is error
    assert identity(config) == before


@pytest.mark.parametrize("uncertain_launch", [False, True])
def test_unknown_restart_owner_stops_before_the_next_restart(tmp_path, uncertain_launch):
    config, root = tmp_path / "config", tmp_path / "updates"
    localca.prepare(config, host_name="ignored.local", neutral_name="netlab.local", ethernet_ip="192.168.1.20")
    localname.set_local_name("netlab.local", config)
    restarted = []
    error = (UncertainLaunch("launch ownership is unknown") if uncertain_launch else
             updates.CommandNotStopped("restart", object()))

    def unknown(command, timeout, **kwargs):
        if command[0] == "openssl":
            return updates.capture_command(command, timeout, **kwargs)
        restarted.append(command[-1])
        raise error

    with patch.object(updates, "retain_command", create=True), pytest.raises(type(error)) as raised:
        run_cli(config, root, unknown)
    assert raised.value is error
    assert restarted == ["jittersim-mdns.service"]


def test_queued_or_malformed_retained_update_blocks_rename_before_crypto(tmp_path):
    config, root = tmp_path / "config", tmp_path / "updates"
    config.mkdir()
    localname.set_local_name("netlab.local", config)
    root.mkdir(mode=0o700)
    (root / "request.json").write_text("malformed retained operation")
    assert run_cli(config, root, lambda *a, **k: pytest.fail("ran a command"), operation=None) == 1
    assert localname.read_local_name(config / "trust.env") == "netlab.local"

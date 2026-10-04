"""`jittersim update` on the Pi: the exact commands the passwordless rule allows."""

import shlex
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim import cli, updates
from jittersim.command import main as jittersim
from tests.release_support import have_ssh_keygen, make_key, signed_release, write_archive
from tests.release_support import release_files


def rule_commands():
    """The commands of the sudoers rule as the argument lists sudo would pass."""
    rule = [line for line in updates.SUDOERS_TEXT.splitlines() if line and line[0] != "#"][0]
    commands = [shlex.split(text) for text in rule.split("NOPASSWD:", 1)[1].split(",")]
    assert all(command[0] == "/usr/local/bin/jittersim" for command in commands)
    return [command[1:] for command in commands]


def test_the_rule_allows_exactly_staging_the_incoming_archive_and_a_waiting_install():
    assert rule_commands() == [
        ["update", "stage", "/home/jittersim/incoming/jittersim-release.tar.gz"],
        ["update", "install", "--yes", "--wait"],
    ]


def test_the_staging_command_of_the_rule_verifies_the_archive_and_its_signature():
    staged = []
    with patch.object(updates, "stage_release",
                      side_effect=lambda *a, **k: staged.append((a, k)) or {
                          "version": "abc1234", "signer_label": "owner@mac"}), \
            patch.object(updates, "running_as_root", return_value=True):
        assert jittersim(rule_commands()[0]) == 0
    (arguments, options), = staged
    assert arguments == (Path(updates.INCOMING_ARCHIVE),)
    # No other signature, checksum or version comes from the command line.
    assert options["signature"] is None
    assert options["sha256"] is None and options["version"] is None


@pytest.fixture
def pi(tmp_path, monkeypatch):
    if not have_ssh_keygen():
        pytest.skip("ssh-keygen signs the releases")
    root, code, config = tmp_path / "updates", tmp_path / "code", tmp_path / "config"
    for folder in (code, config):
        folder.mkdir()
        folder.chmod(0o755)
    private, public = make_key(tmp_path / "keys")
    updates.trust_key(public, config=config, root=root)
    monkeypatch.setattr(updates, "UPDATE_ROOT", root)
    monkeypatch.setattr(updates, "CODE_ROOT", code)
    monkeypatch.setattr(updates, "CONFIG_ROOT", config)
    monkeypatch.setattr(updates, "running_as_root", lambda: True)
    return tmp_path, private, root


def test_the_staging_command_alone_gives_nothing_without_a_trusted_signature(pi, capsys):
    base, private, root = pi
    unsigned = write_archive(base / "unsigned.tar.gz", release_files())
    assert jittersim(["update", "stage", str(unsigned)]) == 1
    stranger, _ = make_key(base / "stranger", comment="stranger")
    foreign = signed_release(base, stranger, name="foreign.tar.gz")
    assert jittersim(["update", "stage", str(foreign)]) == 1
    assert not (root / "staged").exists()
    assert "Nothing was changed." in capsys.readouterr().err
    signed = signed_release(base, private, name="signed.tar.gz", version="abc1234")
    assert jittersim(["update", "stage", str(signed)]) == 0
    assert "Staged release abc1234 signed by owner@mac" in capsys.readouterr().out


def helper_status(**values):
    status = {"installed": "old1234", "available": "new5678", "phase": "ready",
              "message": "Release new5678 signed by owner@mac verified and ready to install.",
              "rollback_available": False, "installable": True, "signed_by": "owner@mac",
              "reason": None, "trusted_keys": 1}
    status.update(values)
    return status


def test_install_with_wait_queues_the_staged_release_and_joins_the_update_service(capsys):
    with patch("jittersim.cli.HelperClient") as helper, \
            patch.object(cli.os, "geteuid", return_value=0), \
            patch("jittersim.cli.subprocess.run") as run, \
            patch("jittersim.updates.update_status",
                  return_value=helper_status(installed="new5678", phase="complete",
                                             message="Release installed.")):
        helper.return_value.release_status.return_value = helper_status()
        assert jittersim(rule_commands()[1]) == 0
    helper.return_value.install_release.assert_called_once_with(version="new5678")
    assert run.call_args[0][0] == ["systemctl", "start", "jittersim-update.service"]
    output = capsys.readouterr().out
    assert "Installed: new5678" in output and "Release installed." in output


def test_a_failed_update_makes_the_waiting_install_fail(capsys):
    with patch("jittersim.cli.HelperClient") as helper, \
            patch.object(cli.os, "geteuid", return_value=0), \
            patch("jittersim.cli.subprocess.run"), \
            patch("jittersim.updates.update_status",
                  return_value=helper_status(phase="rolled-back",
                                             message="Update failed. Previous installation "
                                             "restored and checked.")):
        helper.return_value.release_status.return_value = helper_status()
        assert jittersim(["update", "install", "--yes", "--wait"]) == 1
    assert "Previous installation restored" in capsys.readouterr().out


def test_waiting_needs_root_and_queues_nothing_without_it(capsys):
    with patch("jittersim.cli.HelperClient") as helper, \
            patch.object(cli.os, "geteuid", return_value=1000):
        helper.return_value.release_status.return_value = helper_status()
        assert jittersim(["update", "install", "--yes", "--wait"]) == 1
    helper.return_value.install_release.assert_not_called()
    assert "needs root" in capsys.readouterr().err


def test_a_staged_release_that_cannot_be_installed_is_not_queued(capsys):
    with patch("jittersim.cli.HelperClient") as helper:
        helper.return_value.release_status.return_value = helper_status(
            installable=False, signed_by=None,
            reason="This release was staged without a signature. Stage a signed release.")
        assert jittersim(["update", "install", "--yes"]) == 1
    helper.return_value.install_release.assert_not_called()
    assert "staged without a signature" in capsys.readouterr().err


def test_check_names_the_signer_and_whether_the_release_can_be_installed(capsys):
    with patch("jittersim.cli.HelperClient") as helper:
        helper.return_value.release_status.return_value = helper_status()
        assert jittersim(["update", "check"]) == 0
    output = capsys.readouterr().out.splitlines()
    assert "Installed: old1234" in output
    assert "Staged: new5678" in output
    assert "Signed by: owner@mac" in output
    assert "Ready to install: yes" in output
    assert "Trusted signing keys: 1" in output
    assert "Update phase: ready" in output


def test_help_lists_the_signing_commands(capsys):
    with pytest.raises(SystemExit):
        jittersim(["update", "--help"])
    output = capsys.readouterr().out
    for name in ("stage", "trust-key", "untrust-key", "list-keys",
                 "enable-passwordless-staging", "disable-passwordless-staging"):
        assert name in output

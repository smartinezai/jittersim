"""One-step updates: `sudo jittersim update` on the Pi, `jittersim remote update check|now`
from a Mac, and one version label for health, the CLI and Updates."""

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim import cli, release_info, remote_cli, updates
from jittersim.api import workflows
from jittersim.command import main as jittersim


def status(**values):
    value = {"installed": "0.2.0", "available": "0.3.0", "phase": "ready", "installable": True,
             "signed_by": "owner@mac", "reason": None, "rollback_available": True,
             "message": "Release 0.3.0 signed by owner@mac verified and ready to install."}
    value.update(values)
    return value


@pytest.fixture
def pi(tmp_path, monkeypatch):
    root = tmp_path / "updates"
    (root / "staged" / "deploy").mkdir(parents=True)
    monkeypatch.setattr(updates, "UPDATE_ROOT", root)
    monkeypatch.setattr(updates, "CONFIG_ROOT", tmp_path / "config")
    monkeypatch.setattr(updates, "CODE_ROOT", tmp_path / "code")
    monkeypatch.setattr(cli.os, "geteuid", lambda: 0)
    return root


def run_now(capsys, arguments=("update",), *, token=True, final="complete", initial=None,
            online="Release 0.3.0 (v0.3.0) is staged and ready to install."):
    online_calls = []

    def check(*args, **kwargs):
        online_calls.append(kwargs)
        return {"result": "staged", "message": online}

    with patch("jittersim.cli.HelperClient") as helper, \
            patch.object(updates, "token_is_set", return_value=token), \
            patch.object(updates, "perform_online_check", side_effect=check), \
            patch.object(cli.subprocess, "run") as start, \
            patch.object(updates, "update_status", return_value={
                "phase": final, "request_id": "a" * 32, "installed": "0.3.0",
                "message": "Release installed." if final == "complete"
                else "Update failed. Previous installation restored and checked."}):
        helper.return_value.release_status.return_value = initial or status()
        helper.return_value.install_release.return_value = {"request_id": "a" * 32}
        code = jittersim(list(arguments))
    captured = capsys.readouterr()
    start.assert_not_called()
    return code, captured.out, captured.err, helper.return_value, online_calls


def test_bare_update_checks_online_shows_notes_installs_and_waits(pi, capsys):
    (pi / "staged/deploy/RELEASE-NOTES.md").write_text(
        "## v0.3.0 - 2026-10-09\n\n* Faster \x1b[2Jcheck by @owner\n")
    code, out, err, helper, online = run_now(capsys, ("update", "now", "--yes"))
    assert code == 0, err
    assert len(online) == 1 and online[0]["auto_install"] is False
    assert "Installed: 0.2.0" in out and "New release: 0.3.0, signed by owner@mac" in out
    assert "  ## v0.3.0 - 2026-10-09" in out and "\x1b" not in out
    assert "restored automatically" in out and "Done. The Pi runs 0.3.0." in out
    helper.install_release.assert_called_once_with(version="0.3.0")


def test_bare_update_is_the_one_step_command(pi, capsys):
    with patch.object(cli, "_confirm", return_value=False) as confirm:
        code, out, _, helper, online = run_now(capsys, ("update",))
    assert code == 0 and online and confirm.called
    assert "Cancelled. Nothing was installed." in out
    helper.install_release.assert_not_called()


def test_a_failed_health_check_reports_the_automatic_restore(pi, capsys):
    code, out, err, _, _ = run_now(capsys, ("update", "now", "--yes"), final="rolled-back")
    assert code == 1
    assert "Previous installation restored" in out and "was restored" in err


def test_up_to_date_installs_nothing(pi, capsys):
    code, out, _, helper, _ = run_now(capsys, ("update", "now", "--yes"),
                                      initial=status(available=None))
    assert code == 0 and "Up to date. Installed: 0.2.0" in out
    helper.install_release.assert_not_called()


def test_a_release_that_cannot_be_installed_is_not_queued(pi, capsys):
    code, _, err, helper, _ = run_now(capsys, ("update", "now", "--yes"), initial=status(
        installable=False, reason="The key that signed the staged release is no longer trusted."))
    assert code == 1 and "no longer trusted" in err
    helper.install_release.assert_not_called()


def test_offline_and_missing_token_never_ask_github(pi, capsys):
    code, out, _, _, online = run_now(capsys, ("update", "now", "--yes", "--offline"))
    assert code == 0 and not online and "--offline" in out
    code, out, _, _, online = run_now(capsys, ("update", "now", "--yes"), token=False)
    assert code == 0 and not online and "set-token" in out


def test_an_unfinished_update_must_be_retried_first(pi, capsys):
    code, _, err, helper, _ = run_now(capsys, ("update", "now", "--yes"), initial=status(
        phase="failed", retry={"id": "b" * 64}))
    assert code == 1 and "update retry" in err
    helper.install_release.assert_not_called()


def test_one_step_update_needs_root(capsys, monkeypatch):
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)
    with patch("jittersim.cli.HelperClient") as helper:
        assert jittersim(["update"]) == 1
    helper.assert_not_called()
    assert "sudo jittersim update" in capsys.readouterr().err


def test_update_help_still_lists_every_command(capsys):
    with pytest.raises(SystemExit):
        jittersim(["update", "--help"])
    output = capsys.readouterr().out
    for name in ("now", "check", "install", "rollback", "stage", "trust-key", "set-token"):
        assert name in output


@pytest.mark.parametrize("raw,label", [
    ({"format": 1, "commit": "a" * 40, "version": "0.3.0", "sequence": 5}, "0.3.0"),
    ({"format": 1, "commit": "a" * 40, "version": "0.3.1.dev+abc1234", "sequence": 5}, "0.3.1.dev+abc1234"),
    ({"version": "../../etc"}, None),
    ("not json", None),
])
def test_installed_label_comes_from_the_signed_manifest(tmp_path, raw, label):
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/RELEASE").write_text(raw if isinstance(raw, str) else json.dumps(raw))
    assert release_info.installed_label(tmp_path) == label


def test_health_cli_and_updates_show_the_same_label(tmp_path, monkeypatch, capsys):
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/RELEASE").write_text(json.dumps(
        {"format": 1, "commit": "a" * 40, "version": "0.3.1.dev+abc1234", "sequence": 5}))
    monkeypatch.setattr(release_info, "CODE_ROOT", tmp_path)
    assert workflows.machine_health()["version"] == "0.3.1.dev+abc1234"
    assert jittersim(["version"]) == 0
    assert capsys.readouterr().out.strip() == "JitterSim 0.3.1.dev+abc1234"
    monkeypatch.setattr(release_info, "CODE_ROOT", tmp_path / "missing")
    assert workflows.machine_health()["version"] == release_info.package_label()


def test_long_notes_are_cut_not_refused(tmp_path):
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/RELEASE-NOTES.md").write_text("## v1.0.0\n" + "x" * 40000)
    notes = release_info.release_notes(tmp_path)
    assert notes.startswith("## v1.0.0") and len(notes.encode()) <= release_info.MAX_NOTES


# --- From the Mac: jittersim remote update check | now


have_tools = shutil.which("ssh-keygen") and shutil.which("openssl")


@pytest.fixture
def lab(tmp_path, monkeypatch):
    if not have_tools:
        pytest.skip("ssh-keygen and openssl")
    from tests.release_support import make_key, signed_release
    from tests.unit.test_deploy_over_https import TOKEN, Pi, make_certificate

    private, public = make_key(tmp_path / "keys")
    certificate, key = make_certificate(tmp_path, "dashboard")
    pi = Pi(tmp_path, certificate, key, public)
    builds = tmp_path / "builds"
    builds.mkdir()
    published = signed_release(builds, private, sequence=300, version="0.3.0",
                               name="jittersim-release.tar.gz")
    original = pi.server.helper_client

    class OnlineHelper:
        """The Pi's helper with an online check that 'downloads' the published release."""

        fail_install = False

        def __getattr__(self, name):
            return getattr(original, name)

        def online_check(self, *, force=False):
            updates.stage_release(published, root=pi.root, code=pi.code, config=pi.config)
            return {"job": "0123456789abcdef", "state": "running", "message": "Checking GitHub."}

        def online_status(self, job=None):
            return {"job": job, "state": "done", "result": "staged",
                    "message": "Release 0.3.0 (v0.3.0) is staged and ready to install."}

        def install_release(self, *, version, rollback=False):
            if not self.fail_install:
                return original.install_release(version=version, rollback=rollback)
            updates.request_update(version, root=pi.root, code=pi.code, config=pi.config,
                                   run=lambda *a: None)
            (pi.root / "request.json").unlink()
            updates.atomic_json(pi.root / "status.json", {
                "phase": "rolled-back", "message": "Previous installation restored and health check passed."})
            return {"ok": True, **original.release_status()}

    helper = OnlineHelper()
    pi.server.helper_client = helper
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("JITTERSIM_URL", pi.url)
    monkeypatch.setenv("JITTERSIM_CA_CERT", str(certificate))
    monkeypatch.setenv("JITTERSIM_ADMIN_TOKEN", TOKEN)
    yield pi, helper, TOKEN
    pi.stop()


def remote(capsys, *arguments):
    with patch.object(remote_cli.time, "sleep"):
        code = cli.main(["remote", "update", *arguments])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_remote_check_asks_the_pi_to_look_on_github(lab, capsys):
    pi, _, token = lab
    code, out, err = remote(capsys, "check")
    assert code == 0, err
    assert "staged and ready to install" in out and "Staged release: 0.3.0" in out
    assert pi.installs == [] and token not in out + err


def test_remote_now_checks_installs_and_waits(lab, capsys):
    pi, _, _ = lab
    code, out, err = remote(capsys, "now", "--yes")
    assert code == 0, err
    assert "New release: 0.3.0" in out and "The Pi now runs release 0.3.0" in out
    assert pi.installs == ["0.3.0"]


def test_remote_now_reports_the_restore_after_a_failed_install(lab, capsys):
    _, helper, _ = lab
    helper.fail_install = True
    code, _, err = remote(capsys, "now", "--yes", "--wait", "60")
    assert code == 1 and "restored the previous installation" in err


def test_remote_now_without_confirmation_installs_nothing(lab, capsys, monkeypatch):
    pi, _, _ = lab
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    code, out, _ = remote(capsys, "now")
    assert code == 0 and "Cancelled" in out and pi.installs == []

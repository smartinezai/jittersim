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


REQUEST = "a" * 32


def phases(*steps, version="0.3.0", before="0.2.0", request=REQUEST):
    """Statuses the update service publishes one after another, as apply_update does. The
    new release record appears at 'checking', before the health check decides."""
    out = []
    for phase in steps:
        installed = version if phase in ("checking", "complete", "rolling-back") else before
        if phase == "rolled-back":
            installed = before
        out.append({"phase": phase, "request_id": request, "request_kind": "install",
                    "request_version": version, "installed": installed,
                    "message": "Update " + phase + "."})
    return out


def run_now(capsys, arguments=("update", "now", "--yes"), *, token=True, statuses=None,
            initial=None, online=None):
    online_calls = []

    def check(*args, **kwargs):
        online_calls.append(kwargs)
        return online or {"result": "staged",
                          "message": "Release 0.3.0 (v0.3.0) is staged and ready to install."}

    sequence = list(statuses if statuses is not None else phases(
        "queued", "installing", "checking", "complete"))
    final = sequence[-1] if sequence else {}

    def read_status(*args, **kwargs):
        return sequence.pop(0) if len(sequence) > 1 else final

    with patch("jittersim.cli.HelperClient") as helper, \
            patch.object(updates, "token_is_set", return_value=token), \
            patch.object(updates, "perform_online_check", side_effect=check), \
            patch.object(cli.subprocess, "run") as start, \
            patch.object(cli.time, "sleep"), \
            patch.object(updates, "update_status", side_effect=read_status):
        helper.return_value.release_status.return_value = initial or status()
        helper.return_value.install_release.return_value = {"request_id": REQUEST}
        code = jittersim(list(arguments))
    captured = capsys.readouterr()
    start.assert_not_called()
    return code, captured.out, captured.err, helper.return_value, online_calls


def test_update_checks_online_shows_notes_installs_and_waits_for_complete(pi, capsys):
    (pi / "staged/deploy/RELEASE-NOTES.md").write_text(
        "## v0.3.0 - 2026-10-09\n\n* Faster \x1b[2Jcheck by @owner\n")
    code, out, err, helper, online = run_now(capsys)
    assert code == 0, err
    assert len(online) == 1 and online[0]["auto_install"] is False
    assert "Installed: 0.2.0" in out and "New release: 0.3.0, signed by owner@mac" in out
    assert "  ## v0.3.0 - 2026-10-09" in out and "\x1b" not in out
    assert "Done. The Pi runs 0.3.0 and passed its health check." in out
    helper.install_release.assert_called_once_with(version="0.3.0")


def test_new_label_during_the_health_check_is_not_success(pi, capsys):
    # The release record says 0.3.0 at 'checking', then the health check fails and restores.
    code, out, err, _, _ = run_now(capsys, statuses=phases(
        "queued", "installing", "checking", "rolling-back", "rolled-back"))
    assert code == 1 and "Done." not in out
    assert "confirmed that the previous installation (0.2.0) was restored" in err


def test_a_failed_recovery_is_never_called_a_restore(pi, capsys):
    code, out, err, _, _ = run_now(capsys, statuses=phases("queued", "installing", "checking", "failed"))
    assert code == 1 and "restored" not in err and "Recovery may still be running" in err


@pytest.mark.parametrize("arguments", [("update", "--yes"), ("update", "--offline", "--yes")])
def test_options_after_bare_update_mean_update_now(pi, capsys, arguments):
    code, out, err, helper, online = run_now(capsys, arguments)
    assert code == 0, err
    assert bool(online) == ("--offline" not in arguments)
    helper.install_release.assert_called_once()


def test_bare_update_asks_before_installing(pi, capsys):
    with patch.object(cli, "_confirm", return_value=False) as confirm:
        code, out, _, helper, online = run_now(capsys, ("update",))
    assert code == 0 and online and confirm.called
    assert "Cancelled. Nothing was installed." in out
    helper.install_release.assert_not_called()


def test_a_failed_online_check_is_a_failure_not_up_to_date(pi, capsys):
    code, out, err, helper, _ = run_now(capsys, initial=status(available=None), online={
        "result": "failed", "message": "GitHub refused the token. It may have expired"})
    assert code == 1 and "No newer release" not in out and "Up to date" not in out
    assert "refused the token" in err and "--offline" in err
    helper.install_release.assert_not_called()


def test_up_to_date_installs_nothing(pi, capsys):
    code, out, _, helper, _ = run_now(capsys, initial=status(available=None))
    assert code == 0 and "No newer release. Installed: 0.2.0" in out
    helper.install_release.assert_not_called()


def test_an_update_in_progress_is_followed_not_restarted(pi, capsys):
    running = phases("installing")[0]
    code, out, err, helper, online = run_now(capsys, initial={**status(), **running},
                                             statuses=phases("checking", "complete"))
    assert code == 0, err
    assert "already running" in out and not online
    helper.install_release.assert_not_called()


def test_a_release_that_just_failed_is_not_retried_under_yes(pi, capsys):
    failed = phases("rolled-back")[0]
    code, _, err, helper, _ = run_now(capsys, ("update", "now", "--yes", "--offline"),
                                      initial={**status(), **failed, "installed": "0.2.0"})
    assert code == 1 and "failed on this Pi before" in err
    helper.install_release.assert_not_called()


def test_a_release_that_cannot_be_installed_is_not_queued(pi, capsys):
    code, _, err, helper, _ = run_now(capsys, initial=status(
        installable=False, reason="The key that signed the staged release is no longer trusted."))
    assert code == 1 and "no longer trusted" in err
    helper.install_release.assert_not_called()


def test_offline_and_missing_token_never_ask_github(pi, capsys):
    code, out, _, _, online = run_now(capsys, ("update", "now", "--yes", "--offline"))
    assert code == 0 and not online and "--offline" in out
    code, out, _, _, online = run_now(capsys, token=False)
    assert code == 0 and not online and "set-token" in out


def test_an_unfinished_update_must_be_retried_first(pi, capsys):
    code, _, err, helper, _ = run_now(capsys, initial=status(phase="failed", retry={"id": "b" * 64}))
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
        """The Pi's helper with an online check that 'downloads' the published release. With
        auto_install it queues the install itself, as Install automatically does."""

        check_result = "staged"
        auto_install = False

        def __getattr__(self, name):
            return getattr(original, name)

        def online_check(self, *, force=False):
            if self.check_result == "staged":
                updates.stage_release(published, root=pi.root, code=pi.code, config=pi.config)
            return {"job": "0123456789abcdef", "state": "running", "message": "Checking GitHub."}

        def online_status(self, job=None):
            if self.check_result == "failed":
                return {"job": job, "state": "done", "result": "failed",
                        "message": "GitHub refused the token. It may have expired"}
            queued = False
            if self.auto_install and not pi.installs:
                original.install_release(version="0.3.0")
                queued = True
            return {"job": job, "state": "done", "result": "staged", "install_queued": queued,
                    "message": "Release 0.3.0 (v0.3.0) is staged and ready to install."}

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


def test_remote_now_succeeds_only_after_complete(lab, capsys):
    pi, _, _ = lab
    code, out, err = remote(capsys, "now", "--yes")
    assert code == 0, err
    assert "Pi: checking" in out and out.index("Pi: checking") < out.index("Pi: complete")
    assert "The Pi now runs release 0.3.0 and passed its health check" in out
    assert pi.installs == ["0.3.0"]


def test_remote_now_new_label_then_rollback_is_a_failure(lab, capsys):
    pi, _, _ = lab
    pi.outcome = "rolled-back"
    code, out, err = remote(capsys, "now", "--yes", "--wait", "120")
    assert code == 1 and "now runs" not in out
    assert "confirmed that the previous installation" in err


def test_remote_now_failed_recovery_is_not_called_a_restore(lab, capsys):
    pi, _, _ = lab
    pi.outcome = "failed"
    code, _, err = remote(capsys, "now", "--yes", "--wait", "120")
    assert code == 1 and "recovery is not confirmed" in err and "restored" not in err


def test_remote_check_failure_is_an_error_and_installs_nothing(lab, capsys):
    pi, helper, _ = lab
    helper.check_result = "failed"
    for action in ("check", "now"):
        code, out, err = remote(capsys, action, "--yes") if action == "now" else remote(capsys, action)
        assert code == 1 and "refused the token" in err and "No newer release" not in out
    assert pi.installs == []


def test_remote_now_follows_an_install_the_pi_already_started(lab, capsys, monkeypatch):
    pi, helper, _ = lab
    helper.auto_install = True
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("must not ask"))
    code, out, err = remote(capsys, "now")
    assert code == 0, err
    assert "already started installing" in out and pi.installs == ["0.3.0"]


def test_remote_now_json_prints_one_json_answer(lab, capsys):
    code, out, err = remote(capsys, "now", "--yes", "--json")
    assert code == 0, err
    assert json.loads(out) == {"result": "installed", "installed": "0.3.0",
                               "request_id": json.loads(out)["request_id"]}
    assert remote(capsys, "now", "--json")[0] == 2


def test_remote_now_without_confirmation_installs_nothing(lab, capsys, monkeypatch):
    pi, _, _ = lab
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    code, out, _ = remote(capsys, "now")
    assert code == 0 and "Cancelled" in out and pi.installs == []

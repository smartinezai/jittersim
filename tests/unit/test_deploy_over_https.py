"""deploy-over-https.sh and the way deploy-from-mac.sh picks it, against a real TLS dashboard.

Real curl talks to the real ControlServer. The helper stand-in runs the real stage_upload so a
bad signature is refused by the root update manager's own code. ssh, scp, security and ifconfig
are fakes on PATH: nothing reaches a Pi, a Keychain or a network.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim import release_uploads, updates
from jittersim.api import server as api
from jittersim.config.storage import ConfigRepository
from jittersim.networking.helper import HelperClientError
from tests.release_support import have_ssh_keygen, make_key

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "Tok3n-never-printed_ABC"
pytestmark = pytest.mark.skipif(
    not (have_ssh_keygen() and shutil.which("openssl") and shutil.which("curl")),
    reason="ssh-keygen, openssl and curl are required")

SHIM = '''#!/bin/sh
printf '%s\\n' "$(basename "$0") $*" >> "$FAKE_LOG"
case $(basename "$0") in
    curl) exec "$REAL_CURL" "$@" ;;
    ssh) exit "${FAKE_SSH_EXIT:-255}" ;;
    scp) exit 1 ;;
    sleep) exit 0 ;;
    ifconfig) printf "en0: flags=8863<UP>\\n\\tinet %s netmask 0xffffff00\\n" "${FAKE_MAC_ADDRESS:-192.168.0.5}" ;;
    route) printf 'gateway: 192.168.0.1\\n' ;;
    security)
        case $1 in
            find-generic-password) printf '%s\\n' "${FAKE_KEYCHAIN_TOKEN:-}"; [ -n "${FAKE_KEYCHAIN_TOKEN:-}" ] ;;
            *) exit 0 ;;
        esac ;;
esac
'''


class Pi:
    """The dashboard, its helper stand-in and the folders the root manager uses."""

    def __init__(self, base, certificate, key, public):
        self.root, self.code, self.config = base / "updates", base / "code", base / "config"
        self.spool = base / "spool"
        self.upload_lock = base / "release-upload.lock"
        release_uploads.prepare_lock(self.upload_lock)
        for folder in (self.code, self.config):
            folder.mkdir()
        updates.trust_key(public, config=self.config, root=self.root)
        self.installs = []
        outer = self

        class Helper:
            def ping(self):
                return {"ok": True}

            def release_status(self):
                return {"ok": True, **updates.update_status(
                    root=outer.root, code=outer.code, config=outer.config)}

            def stage_uploaded_release(self, *, upload_id=None):
                try:
                    record = updates.stage_upload(upload_id, spool=outer.spool,
                                                  lock=outer.upload_lock, root=outer.root,
                                                  code=outer.code, config=outer.config)
                except updates.UpdateError as error:
                    raise HelperClientError(str(error), reason="release-refused") from error
                return {"ok": True, "staged_version": record["version"], **self.release_status()}

            def install_release(self, *, version, rollback=False):
                updates.request_update(version, root=outer.root, code=outer.code,
                                       config=outer.config, run=lambda *a: None)
                outer.installs.append(version)
                # The update service installs it: the Pi now reports the new release.
                updates.atomic_json(outer.code / ".jittersim-release.json", {"version": version})
                return {"ok": True, **self.release_status()}

        ready = threading.Event()

        def serve():
            self.repository = ConfigRepository(":memory:")
            self.server = api.ControlServer(
                ("127.0.0.1", 0), self.repository, admin_token=TOKEN,
                tls_certfile=certificate, tls_keyfile=key)
            self.server.release_spool = self.spool
            self.server.release_upload_lock = self.upload_lock
            self.server.helper_client = Helper()
            ready.set()
            self.server.serve_forever()
            self.server.server_close()
            self.repository.close()

        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        assert ready.wait(10)
        self.url = f"https://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)


def make_certificate(directory, name):
    certificate, key = directory / f"{name}.crt", directory / f"{name}.key"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes", "-days", "1",
         "-keyout", str(key), "-out", str(certificate), "-subj", "/CN=127.0.0.1",
         "-addext", "subjectAltName=IP:127.0.0.1"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return certificate, key


@pytest.fixture
def lab(tmp_path):
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    for name in ("curl", "ssh", "scp", "sleep", "ifconfig", "route", "security"):
        shim = bin_directory / name
        shim.write_text(SHIM)
        shim.chmod(0o755)
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "deploy", repo / "deploy")
    (repo / "src" / "jittersim" / "api").mkdir(parents=True)
    (repo / "src" / "jittersim" / "api" / "server.py").write_text("x = 1\n")
    (repo / "src" / "jittersim" / "updates.py").write_text("y = 1\n")
    for name in ("pyproject.toml", "README.md"):
        (repo / name).write_text(name)
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": f"{bin_directory}:{os.environ['PATH']}", "HOME": str(home),
           "TMPDIR": str(tmp_path), "FAKE_LOG": str(tmp_path / "calls.log"),
           "REAL_CURL": shutil.which("curl"), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for command in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(repo), *command], check=True, env=env)
    short = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, check=True).stdout.strip()
    private, public = make_key(tmp_path / "keys")
    certificate, key = make_certificate(tmp_path, "dashboard")
    stranger, _ = make_certificate(tmp_path, "stranger")
    pi = Pi(tmp_path, certificate, key, public)
    yield {"pi": pi, "env": env, "repo": repo, "short": short, "private": private,
           "certificate": certificate, "stranger": stranger, "tmp": tmp_path,
           "log": tmp_path / "calls.log"}
    pi.stop()


def https(lab, *arguments, env=None, script="deploy-over-https.sh"):
    values = {**lab["env"], "JITTERSIM_TOKEN": TOKEN, "JITTERSIM_INSTALL_WAIT_SECONDS": "20",
              **(env or {})}
    values = {name: value for name, value in values.items() if value is not None}
    return subprocess.run(
        ["sh", str(lab["repo"] / "deploy" / "raspberry-pi" / script), *arguments],
        capture_output=True, text=True, env=values, timeout=180, cwd=lab["repo"])


def base_arguments(lab, certificate=None):
    return ["--ref", "HEAD", "--url", lab["pi"].url, "--ca-cert",
            str(certificate or lab["certificate"]), "--signing-key", str(lab["private"])]


def logged(lab):
    return lab["log"].read_text() if lab["log"].exists() else ""


def test_one_command_uploads_installs_and_waits_for_the_new_release(lab):
    result = https(lab, *base_arguments(lab))
    assert result.returncode == 0, result.stdout + result.stderr
    short = lab["short"]
    assert f"The Pi checked the signature and staged release {short}" in result.stdout
    assert f"The Pi now runs release {short}" in result.stdout
    assert lab["pi"].installs == [short]
    # The token never shows in output or in any command line.
    assert TOKEN not in result.stdout + result.stderr + logged(lab)
    assert not list(lab["tmp"].glob("jittersim-https.*")), "the work folder is removed"


def test_explicit_stage_only_stages_and_says_how_to_install(lab):
    result = https(lab, *base_arguments(lab), "--stage-only")
    assert result.returncode == 0, result.stderr
    assert "staged release" in result.stdout and "Install it with" in result.stdout
    assert lab["pi"].installs == []


def test_a_release_the_pi_already_runs_is_not_uploaded_again(lab):
    updates.atomic_json(lab["pi"].code / ".jittersim-release.json", {"version": lab["short"]})
    result = https(lab, *base_arguments(lab), "--install")
    assert result.returncode == 0
    assert "already runs release" in result.stdout
    assert lab["pi"].installs == []


def test_a_wrong_token_is_refused_in_plain_words(lab):
    result = https(lab, *base_arguments(lab), env={"JITTERSIM_TOKEN": "not-the-token"})
    assert result.returncode == 1
    assert "refused the admin token" in result.stderr
    assert "not-the-token" not in result.stdout + result.stderr


def test_without_a_token_it_says_how_to_store_one(lab):
    result = https(lab, *base_arguments(lab), env={"JITTERSIM_TOKEN": None})
    assert result.returncode == 1
    assert "no admin token" in result.stderr and "--save-token" in result.stderr


def test_the_token_comes_from_the_keychain(lab):
    result = https(lab, *base_arguments(lab), env={"JITTERSIM_TOKEN": None,
                                                    "FAKE_KEYCHAIN_TOKEN": TOKEN})
    assert result.returncode == 0, result.stderr
    assert "find-generic-password -s jittersim-admin-token -w" in logged(lab)
    assert TOKEN not in result.stdout + result.stderr + logged(lab)


def test_save_token_uses_the_keychain_prompt_and_never_the_command_line(lab):
    result = https(lab, "--save-token")
    assert result.returncode == 0, result.stderr
    call = [line for line in logged(lab).splitlines() if "add-generic-password" in line][0]
    assert call.endswith("-s jittersim-admin-token -w")
    assert "Saved the admin token" in result.stdout


def test_a_certificate_that_is_not_the_pis_stops_before_the_token_is_sent(lab):
    result = https(lab, *base_arguments(lab, lab["stranger"]))
    assert result.returncode == 1
    assert "certificate does not match" in result.stderr
    assert lab["pi"].installs == []


def test_a_device_that_is_not_allowed_on_the_test_wifi_is_told_what_to_do(lab):
    with patch.object(lab["pi"].server, "dashboard_access", return_value={
            "ok": True, "network": "test", "allowed": False, "can_register": True,
            "mac_address": None}):
        result = https(lab, *base_arguments(lab))
    assert result.returncode == 1
    assert "Allow this device" in result.stderr


def test_a_release_signed_by_a_stranger_is_refused_by_the_pi(lab):
    other, _ = make_key(lab["tmp"] / "other")
    arguments = base_arguments(lab)
    arguments[arguments.index("--signing-key") + 1] = str(other)
    result = https(lab, *arguments)
    assert result.returncode == 1
    assert "signature is not valid" in result.stderr


def test_an_unreachable_dashboard_gets_a_plain_message(lab):
    arguments = base_arguments(lab)
    arguments[arguments.index("--url") + 1] = "https://127.0.0.1:1"
    result = https(lab, *arguments)
    assert result.returncode == 1
    assert "could not reach the dashboard" in result.stderr


def test_the_certificate_is_required(lab):
    arguments = base_arguments(lab)
    del arguments[arguments.index("--ca-cert"):arguments.index("--ca-cert") + 2]
    assert https(lab, *arguments).returncode == 2


def test_dry_run_runs_nothing(lab):
    result = https(lab, *base_arguments(lab), "--install", "--dry-run")
    assert result.returncode == 0
    assert "Dry run" in result.stdout and "curl" not in logged(lab)
    assert lab["pi"].installs == [] and not (lab["pi"].root / "staged").exists()


# -- deploy-from-mac.sh picks the way ---------------------------------------------------------


def mac(lab, *arguments, **env):
    key = lab["tmp"] / "ssh_key"
    key.write_text("not a key")
    return https(lab, "--key", str(key), *arguments, env=env, script="deploy-from-mac.sh")


def tools(lab):
    return [line.split()[0] for line in logged(lab).splitlines() if line.strip()]


def test_on_the_test_wifi_it_never_uses_ssh(lab):
    result = mac(lab, *base_arguments(lab)[2:], "--install", FAKE_MAC_ADDRESS="192.168.50.23")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SSH is blocked on purpose" in result.stdout
    assert f"The Pi now runs release {lab['short']}" in result.stdout
    assert "ssh" not in tools(lab) and "scp" not in tools(lab)


def test_the_https_option_skips_ssh(lab):
    result = mac(lab, "--https", *base_arguments(lab)[2:])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ssh" not in tools(lab)


def test_when_ssh_does_not_answer_it_falls_back_to_https(lab):
    result = mac(lab, *base_arguments(lab)[2:], FAKE_SSH_EXIT="255")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SSH to the Pi did not answer. Using the HTTPS upload instead." in result.stdout
    assert "ssh" in tools(lab)
    assert "staged release" in result.stdout


@pytest.mark.parametrize("explicit_https", [True, False])
def test_wrapper_stage_only_never_posts_an_install_on_either_https_route(lab, explicit_https):
    arguments = ["--https"] if explicit_https else []
    result = mac(lab, *arguments, *base_arguments(lab)[2:], "--stage-only",
                 FAKE_SSH_EXIT="255")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (lab["pi"].root / "staged" / ".jittersim-release.json").is_file()
    assert lab["pi"].installs == []
    assert "--request POST" in logged(lab), "the signed upload must have run"
    assert "/api/updates/install" not in logged(lab)
    assert ("ssh" in tools(lab)) is not explicit_https


def test_ssh_only_never_switches(lab):
    result = mac(lab, "--ssh", *base_arguments(lab)[2:], FAKE_SSH_EXIT="255")
    assert result.returncode == 1
    assert "curl" not in tools(lab)


def test_without_a_ssh_key_it_uses_https(lab):
    result = https(lab, "--key", str(lab["tmp"] / "missing"), *base_arguments(lab)[2:],
                   script="deploy-from-mac.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no SSH key" in result.stdout.replace("There is no SSH key", "no SSH key")


def test_the_token_is_not_handed_to_the_build_or_any_other_child_process(lab):
    # build-release.sh, git and ssh-keygen need no token. A token exported in the shell must
    # not reach them through the environment.
    folder = lab["repo"] / "deploy" / "raspberry-pi"
    (folder / "build-release-real.sh").write_text((folder / "build-release.sh").read_text())
    (folder / "build-release.sh").write_text(
        '#!/bin/sh\nenv >> "$FAKE_ENV_LOG"\nexec sh "$(dirname -- "$0")/build-release-real.sh" "$@"\n')
    seen = lab["tmp"] / "child-env.log"
    result = https(lab, *base_arguments(lab), env={
        "FAKE_ENV_LOG": str(seen), "JITTERSIM_ADMIN_TOKEN": TOKEN + "-other"})
    assert result.returncode == 0, result.stdout + result.stderr
    children = seen.read_text()
    assert "PATH=" in children, "the build stand-in must have run"
    assert TOKEN not in children
    assert "JITTERSIM_TOKEN" not in children and "JITTERSIM_ADMIN_TOKEN" not in children


def test_a_token_with_a_line_break_is_refused_before_curl_is_started(lab):
    result = https(lab, *base_arguments(lab), env={"JITTERSIM_TOKEN": TOKEN + "\nurl=http://x"})
    assert result.returncode == 1
    assert "cannot be right" in result.stderr
    assert "curl " not in logged(lab)
    assert TOKEN not in result.stdout + result.stderr

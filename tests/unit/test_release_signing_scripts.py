"""The Mac side of signed releases: make-release-key.sh, sign-release.sh, build-release.sh and
the signed path of deploy-from-mac.sh. Every key is a throwaway key in the test's own home
folder and fake ssh and scp record every call: nothing reaches a Pi."""

import io
import json
import os
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from jittersim import updates
from tests.release_support import fingerprint, have_ssh_keygen, make_key
from tests.unit.test_deploy_from_mac import (
    BATCH_OPTIONS,
    HOST,
    PROBE,
    SHELLS,
    TERMINAL_OPTIONS,
    _in_a_checkout,
    deploy,
    make_fakes,
    printed_sha,
    short_head,
    tools,
)

ROOT = Path(__file__).resolve().parents[2]
PI = ROOT / "deploy" / "raspberry-pi"
SIGN = PI / "sign-release.sh"
MAKE = PI / "make-release-key.sh"
BUILD = PI / "build-release.sh"
INCOMING = "incoming/jittersim-release.tar.gz"
INCOMING_PATH = "/home/jittersim/" + INCOMING
CLEANUP = f"rm -f {INCOMING} {INCOMING}.sig"
SIGNED_PI = "signed-updates trusted-key"
PASSWORDLESS_PI = SIGNED_PI + " passwordless"

pytestmark = pytest.mark.skipif(not have_ssh_keygen(), reason="ssh-keygen makes and uses keys")
needs_a_checkout = pytest.mark.skipif(not _in_a_checkout(),
                                      reason="the scripts build from a git checkout")


def run(script, *arguments, home, env=None, cwd=None, shell="/bin/sh", path=None):
    environment = {
        "PATH": (f"{path}:" if path else "") + os.environ["PATH"],
        "HOME": str(home),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        **(env or {}),
    }
    return subprocess.run([shell, str(script), *arguments], capture_output=True, text=True,
                          env=environment, cwd=str(cwd or ROOT), stdin=subprocess.DEVNULL,
                          timeout=120)


@pytest.fixture
def pi_fakes(tmp_path):
    return make_fakes(tmp_path)


@pytest.fixture
def home(tmp_path):
    folder = tmp_path / "home"
    folder.mkdir()
    return folder


def release_key(home):
    private, public = make_key(home / ".ssh", name="jittersim_release_ed25519",
                               comment="mac-test")
    return private, public


def trusting_pi(tmp_path, public):
    """A stand-in Pi that trusts PUBLIC: (root, config)."""
    root, config = tmp_path / "pi-updates", tmp_path / "pi-config"
    config.mkdir(mode=0o755)
    updates.trust_key(public, config=config, root=root)
    return root, config


def stage_on(tmp_path, archive, public, **options):
    root, config = trusting_pi(tmp_path, public)
    return updates.stage_release(archive, root=root, code=tmp_path / "pi-code", config=config,
                                 **options)


# sign-release.sh


def a_file(tmp_path, name="release.tar.gz", content=b"release bytes"):
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_sign_release_signs_with_the_default_key_so_the_pi_accepts_it(tmp_path, home):
    _, public = release_key(home)
    archive = a_file(tmp_path)
    result = run(SIGN, str(archive), home=home)
    assert result.returncode == 0, result.stderr
    assert fingerprint(public) in result.stdout
    signature = Path(str(archive) + ".sig")
    assert signature.read_text().startswith("-----BEGIN SSH SIGNATURE-----")
    # The Pi's own check accepts exactly these bytes and nothing else.
    root, config = trusting_pi(tmp_path, public)
    signers = updates.trusted_keys(config)
    work = tmp_path / "work"
    work.mkdir()
    assert updates.verify_signature(archive, signature, signers, work).label == "mac-test"
    archive.write_bytes(b"other bytes")
    with pytest.raises(updates.UpdateError):
        updates.verify_signature(archive, signature, signers, work)


def test_an_old_signature_is_replaced_without_a_question(tmp_path, home):
    release_key(home)
    archive = a_file(tmp_path)
    Path(str(archive) + ".sig").write_text("stale signature\n")
    result = run(SIGN, str(archive), home=home)
    assert result.returncode == 0, result.stderr
    assert "stale" not in Path(str(archive) + ".sig").read_text()


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o606, 0o620])
def test_a_key_others_may_read_or_change_is_refused(tmp_path, home, mode):
    private, _ = release_key(home)
    private.chmod(mode)
    archive = a_file(tmp_path)
    result = run(SIGN, str(archive), home=home)
    assert result.returncode == 2
    assert "chmod 600" in result.stderr
    assert not Path(str(archive) + ".sig").exists()


def test_a_missing_key_points_to_make_release_key(tmp_path, home):
    archive = a_file(tmp_path)
    result = run(SIGN, str(archive), home=home)
    assert result.returncode == 2
    assert "make-release-key.sh" in result.stderr
    private, _ = release_key(home)
    Path(str(private) + ".pub").unlink()
    assert run(SIGN, str(archive), home=home).returncode == 2
    assert not Path(str(archive) + ".sig").exists()


def test_sign_release_refuses_odd_arguments(tmp_path, home):
    release_key(home)
    archive = a_file(tmp_path)
    for arguments in ((), (str(archive), "extra"), ("--key",), ("--key", "-oX", str(archive)),
                      ("--unknown", str(archive)), (str(tmp_path / "missing.tar.gz"),)):
        result = run(SIGN, *arguments, home=home)
        assert result.returncode == 2, arguments
    assert not Path(str(archive) + ".sig").exists()
    assert run(SIGN, "--help", home=home).returncode == 0


def test_another_key_can_be_named(tmp_path, home):
    private, public = make_key(tmp_path / "elsewhere", name="other", comment="other-key")
    archive = a_file(tmp_path)
    result = run(SIGN, "--key", str(private), str(archive), home=home)
    assert result.returncode == 0, result.stderr
    assert fingerprint(public) in result.stdout


# make-release-key.sh


def printed_command(output):
    lines = [line.strip() for line in output.splitlines()]
    return next(line for line in lines if line.startswith("echo '"))


def run_on_a_stand_in_pi(tmp_path, command, monkeypatch):
    """Run the printed Pi command's trust-key step with the real update manager."""
    words = shlex.split(command)
    assert words[0] == "echo" and words[2:6] == ["|", "sudo", "jittersim", "update"]
    root, config = tmp_path / "pi-updates", tmp_path / "pi-config"
    config.mkdir(mode=0o755)
    sudoers = tmp_path / "sudoers.d" / "jittersim-update"
    sudoers.parent.mkdir()
    for name, value in (("UPDATE_ROOT", root), ("CONFIG_ROOT", config), ("CODE_ROOT", tmp_path),
                        ("SUDOERS_PATH", sudoers), ("VISUDO", None)):
        monkeypatch.setattr(updates, name, value)
    monkeypatch.setattr(updates, "running_as_root", lambda: True)
    monkeypatch.setattr(updates.sys, "stdin", io.TextIOWrapper(io.BytesIO(
        (words[1] + "\n").encode())))
    assert updates.main(words[6:], prog="jittersim update") == 0
    return config, sudoers


def test_make_release_key_creates_a_private_ed25519_key_and_prints_the_pi_command(
        tmp_path, home, monkeypatch):
    result = run(MAKE, "--label", "desk-mac", home=home)
    assert result.returncode == 0, result.stderr
    private = home / ".ssh" / "jittersim_release_ed25519"
    public = Path(str(private) + ".pub").read_text()
    assert private.stat().st_mode & 0o777 == 0o600
    assert (home / ".ssh").stat().st_mode & 0o777 == 0o700
    assert public.startswith("ssh-ed25519 ") and public.split()[2] == "desk-mac"
    command = printed_command(result.stdout)
    assert command == (f"echo '{' '.join(public.split()[:2])} desk-mac' | sudo jittersim update "
                       "trust-key --label desk-mac --enable-passwordless-staging -")
    assert "ssh -t -i ~/.ssh/jittersim_ed25519 jittersim@192.168.0.17" in result.stdout
    assert "PRIVATE KEY" not in result.stdout + result.stderr
    config, sudoers = run_on_a_stand_in_pi(tmp_path, command, monkeypatch)
    assert [(key.label, key.fingerprint) for key in updates.trusted_keys(config)] == [
        ("desk-mac", fingerprint(public))]
    assert sudoers.read_text() == updates.SUDOERS_TEXT


def test_an_existing_key_is_never_replaced(home):
    private, public = release_key(home)
    before = private.read_bytes()
    result = run(MAKE, home=home)
    assert result.returncode == 0, result.stderr
    assert "already exists" in result.stdout
    assert private.read_bytes() == before
    # The comment of the existing key is its label.
    assert f"' | sudo jittersim update trust-key --label mac-test " in result.stdout
    assert " ".join(public.split()[:2]) in result.stdout


def test_the_command_without_passwordless_staging(home):
    result = run(MAKE, "--no-passwordless", "--label", "desk", home=home)
    assert result.returncode == 0, result.stderr
    command = printed_command(result.stdout)
    assert "--enable-passwordless-staging" not in command
    assert command.endswith("trust-key --label desk -")


@pytest.mark.parametrize("label", ["two words", "-dash", "x" * 65, "quo'te", "é", "a;b", ""])
def test_unsafe_labels_are_refused_before_a_key_is_made(home, label):
    result = run(MAKE, "--label", label, home=home)
    assert result.returncode == 2
    assert not (home / ".ssh").exists()


def test_a_passphrase_is_left_to_ssh_keygen(tmp_path, home):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "keygen.log"
    real = shutil.which("ssh-keygen")
    (fake_bin / "ssh-keygen").write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        'case "$*" in *" -t ed25519 "*) set -- "$@" -N ""; esac\n'
        f'exec "{real}" "$@"\n'
    )
    (fake_bin / "ssh-keygen").chmod(0o755)
    result = run(MAKE, "--passphrase", "--label", "desk", home=home, path=fake_bin)
    assert result.returncode == 0, result.stderr
    first = log.read_text().splitlines()[0]
    assert "-t ed25519" in first and "-N" not in first
    assert "ssh-add" in result.stdout


# build-release.sh


@pytest.fixture
def checkout(tmp_path):
    """A small checkout with the release scripts and one commit."""
    folder = tmp_path / "checkout"
    (folder / "deploy" / "raspberry-pi").mkdir(parents=True)
    (folder / "src" / "jittersim").mkdir(parents=True)
    for script in (BUILD, SIGN):
        shutil.copy(script, folder / "deploy" / "raspberry-pi")
    (folder / "pyproject.toml").write_text("[project]\nname = 'jittersim'\n")
    (folder / "README.md").write_text("release\n")
    (folder / "src" / "jittersim" / "__init__.py").write_text("")
    for arguments in (("init", "-q"), ("add", "-A"), ("commit", "-q", "-m", "first")):
        subprocess.run(["git", "-C", str(folder), "-c", "user.name=Test",
                        "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                        *arguments], check=True, capture_output=True)
    return folder


def git(folder, *arguments):
    return subprocess.run(["git", "-C", str(folder), *arguments], capture_output=True, text=True,
                          check=True).stdout.strip()


def test_build_release_writes_the_signed_manifest_and_prints_the_checksum_last(
        tmp_path, home, checkout):
    _, public = release_key(home)
    output = tmp_path / "out.tar.gz"
    result = run(checkout / "deploy/raspberry-pi/build-release.sh", "HEAD", str(output),
                 str(home / ".ssh/jittersim_release_ed25519"), home=home, cwd=checkout,
                 env={"JITTERSIM_RELEASE_SEQUENCE": "1790000000"})
    assert result.returncode == 0, result.stderr
    last = result.stdout.strip().splitlines()[-1].split()[0]
    assert last == updates.hashlib.sha256(output.read_bytes()).hexdigest()
    with tarfile.open(output) as bundle:
        manifest = json.loads(bundle.extractfile("deploy/RELEASE").read())
        tops = {Path(name).parts[0] for name in bundle.getnames()}
    assert manifest == {"format": 1, "commit": git(checkout, "rev-parse", "HEAD"),
                        "version": git(checkout, "rev-parse", "--short", "HEAD"),
                        "sequence": 1790000000}
    # Managers from before signed releases accept only these names.
    assert tops <= {"src", "deploy", "README.md", "pyproject.toml"}
    signature = Path(str(output) + ".sig")
    assert signature.exists()
    root, config = trusting_pi(tmp_path, public)
    work = tmp_path / "work"
    work.mkdir()
    updates.verify_signature(output, signature, updates.trusted_keys(config), work)


def test_build_release_without_a_key_signs_nothing_and_uses_the_build_time(
        tmp_path, home, checkout):
    output = tmp_path / "out.tar.gz"
    result = run(checkout / "deploy/raspberry-pi/build-release.sh", "HEAD", str(output),
                 home=home, cwd=checkout)
    assert result.returncode == 0, result.stderr
    assert not Path(str(output) + ".sig").exists()
    with tarfile.open(output) as bundle:
        manifest = json.loads(bundle.extractfile("deploy/RELEASE").read())
    assert manifest["sequence"] > 1_700_000_000


@pytest.mark.parametrize("sequence", ["abc", "-5", "0", "012", "1.5", "1 2"])
def test_build_release_refuses_an_odd_sequence(tmp_path, home, checkout, sequence):
    output = tmp_path / "out.tar.gz"
    result = run(checkout / "deploy/raspberry-pi/build-release.sh", "HEAD", str(output),
                 home=home, cwd=checkout, env={"JITTERSIM_RELEASE_SEQUENCE": sequence})
    assert result.returncode != 0
    assert not output.exists()


def test_build_release_with_a_loose_key_fails(tmp_path, home, checkout):
    private, _ = release_key(home)
    private.chmod(0o644)
    output = tmp_path / "out.tar.gz"
    result = run(checkout / "deploy/raspberry-pi/build-release.sh", "HEAD", str(output),
                 str(private), home=home, cwd=checkout)
    assert result.returncode != 0
    assert not Path(str(output) + ".sig").exists()


# deploy-from-mac.sh on a Pi with signed releases


def signed_deploy(pi_fakes, *arguments, markers=PASSWORDLESS_PI, key=True, **environment):
    if key and not (pi_fakes["home"] / ".ssh" / "jittersim_release_ed25519").exists():
        release_key(pi_fakes["home"])
    return deploy(pi_fakes, *arguments, FAKE_MARKERS=markers, **environment)


def public_key(pi_fakes):
    return (pi_fakes["home"] / ".ssh" / "jittersim_release_ed25519.pub").read_text()


def assert_copies(pi_fakes, calls, short):
    copy_archive, copy_signature = (call["argv"] for call in calls[1:3])
    assert copy_archive[:-2] == [*BATCH_OPTIONS, "-i", str(pi_fakes["key"]), "--"]
    assert copy_archive[-1] == f"{HOST}:{INCOMING}"
    assert Path(copy_archive[-2]).name == f"jittersim-release-{short}.tar.gz"
    assert copy_signature[-1] == f"{HOST}:{INCOMING}.sig"
    assert copy_signature[-2] == copy_archive[-2] + ".sig"


@needs_a_checkout
def test_passwordless_staging_runs_the_exact_allowed_command_without_a_terminal(pi_fakes, tmp_path):
    short = short_head()
    result, calls = signed_deploy(pi_fakes)
    assert result.returncode == 0, result.stderr
    assert tools(calls) == ["ssh", "scp", "scp", "ssh"]
    assert calls[0]["argv"][-1] == PROBE
    assert_copies(pi_fakes, calls, short)
    stage = calls[3]["argv"]
    # BatchMode and no terminal: sudo -n never asks and fails if the rule is gone.
    assert stage == [*BATCH_OPTIONS, "-i", str(pi_fakes["key"]), "--", HOST,
                     f"sudo -n /usr/local/bin/jittersim update stage {INCOMING_PATH} && {CLEANUP}"]
    assert "Passwordless staging is on" in result.stdout
    assert ("'sudo -n /usr/local/bin/jittersim update install --yes --wait'"
            in result.stdout)
    # The copies are the signed archive the Pi accepts.
    archive = pi_fakes["copies"] / f"jittersim-release-{short}.tar.gz"
    signature = pi_fakes["copies"] / f"jittersim-release-{short}.tar.gz.sig"
    assert signature.exists()
    staged = stage_on(tmp_path, archive, public_key(pi_fakes), signature=signature,
                      sha256=printed_sha(result.stdout))
    assert staged["version"] == short and staged["signer_label"] == "mac-test"


@needs_a_checkout
def test_passwordless_install_waits_for_the_update_service_and_checks_the_release(pi_fakes):
    short = short_head()
    result, calls = signed_deploy(pi_fakes, "--install", FAKE_INSTALLED=short)
    assert result.returncode == 0, result.stderr
    assert tools(calls) == ["ssh", "scp", "scp", "ssh", "ssh"]
    assert calls[3]["argv"][-1] == (
        f"sudo -n /usr/local/bin/jittersim update stage {INCOMING_PATH} && {CLEANUP} && "
        "sudo -n /usr/local/bin/jittersim update install --yes --wait")
    assert calls[3]["argv"][:2] == ["-o", "BatchMode=yes"]
    assert calls[4]["argv"][-1] == "jittersim update check"
    assert f"The Pi now runs release {short}." in result.stdout


@needs_a_checkout
def test_without_passwordless_staging_the_pi_asks_for_its_sudo_password(pi_fakes):
    result, calls = signed_deploy(pi_fakes, "--install", markers=SIGNED_PI,
                                  FAKE_INSTALLED=short_head())
    assert result.returncode == 0, result.stderr
    assert tools(calls) == ["ssh", "scp", "scp", "ssh", "ssh"]
    assert calls[3]["argv"] == [*TERMINAL_OPTIONS, "-i", str(pi_fakes["key"]), "--", HOST,
                                f"sudo /usr/local/bin/jittersim update stage {INCOMING} && "
                                f"{CLEANUP} && sudo /usr/local/bin/jittersim update install "
                                "--yes --wait"]
    assert "Passwordless staging is on" not in result.stdout


@needs_a_checkout
def test_a_signed_pi_without_a_local_signing_key_stops_before_copying(pi_fakes):
    result, calls = signed_deploy(pi_fakes, key=False)
    assert result.returncode == 1
    assert tools(calls) == ["ssh"]
    assert "installs only signed releases" in result.stderr
    assert "make-release-key.sh" in result.stderr


@needs_a_checkout
def test_a_signed_pi_without_a_trusted_key_stops_before_copying(pi_fakes):
    result, calls = signed_deploy(pi_fakes, markers="signed-updates")
    assert result.returncode == 1
    assert tools(calls) == ["ssh"]
    assert "trusts no release signing key" in result.stderr


@needs_a_checkout
def test_a_loose_signing_key_stops_everything_before_the_pi_is_contacted(pi_fakes):
    private, _ = release_key(pi_fakes["home"])
    private.chmod(0o640)
    result, calls = signed_deploy(pi_fakes)
    assert result.returncode == 1
    assert calls == []
    assert "chmod 600" in result.stderr
    assert "building or signing the release archive failed" in result.stderr


@needs_a_checkout
def test_a_status_message_cannot_pretend_to_be_a_marker(pi_fakes):
    result, calls = signed_deploy(
        pi_fakes, markers="signed-updates trusted-key",
        FAKE_PROBE_EXTRA="Update state: passwordless staging was mentioned here passwordless")
    assert result.returncode == 0, result.stderr
    assert calls[3]["argv"][0] == "-t"


@needs_a_checkout
def test_a_failed_passwordless_stage_is_reported_and_nothing_else_runs(pi_fakes):
    result, calls = signed_deploy(pi_fakes, "--install", FAKE_TERMINAL_EXIT="1")
    assert result.returncode == 1
    assert tools(calls) == ["ssh", "scp", "scp", "ssh"]
    assert "staging or installing on the Pi failed" in result.stderr


@needs_a_checkout
def test_a_pi_from_before_signed_releases_gets_the_old_staging_command(pi_fakes):
    short = short_head()
    result, calls = signed_deploy(pi_fakes, markers="")
    assert result.returncode == 0, result.stderr
    assert tools(calls) == ["ssh", "scp", "ssh"]
    assert calls[1]["argv"][-1] == f"{HOST}:jittersim-release-{short}.tar.gz"
    assert calls[2]["argv"][-1].startswith(
        f"sudo /usr/bin/python3 -I /usr/local/lib/jittersim-update.py stage "
        f"jittersim-release-{short}.tar.gz --version {short} --sha256 ")


@needs_a_checkout
def test_a_dry_run_with_a_signing_key_shows_the_signed_path_and_runs_nothing(pi_fakes):
    result, calls = signed_deploy(pi_fakes, "--dry-run", "--install")
    assert result.returncode == 0, result.stderr
    assert calls == []
    printed = [line for line in result.stdout.splitlines() if line.startswith("+ ")]
    assert printed[0].endswith("/.ssh/jittersim_release_ed25519")
    assert printed[2].endswith(f"{HOST}:{INCOMING}")
    assert printed[3].endswith(f"{HOST}:{INCOMING}.sig")
    assert printed[4].startswith("+ ssh -t ")
    assert "update install --yes --wait" in printed[4]
    assert list(pi_fakes["tmp"].glob("jittersim-deploy.*")) == []


@needs_a_checkout
@pytest.mark.parametrize("shell", SHELLS)
def test_the_signed_path_runs_under_each_shell(pi_fakes, shell):
    result, calls = signed_deploy(pi_fakes, "--install", shell=shell, FAKE_INSTALLED=short_head())
    assert result.returncode == 0, result.stderr
    assert tools(calls) == ["ssh", "scp", "scp", "ssh", "ssh"]


def test_the_scripts_never_print_or_store_a_private_key():
    for script in (SIGN, MAKE, BUILD, PI / "deploy-from-mac.sh"):
        source = script.read_text()
        for forbidden in ("cat \"$key\"", "cat $key", "BEGIN OPENSSH PRIVATE KEY", "sshpass",
                          "sudo -S", "StrictHostKeyChecking"):
            assert forbidden not in source, (script.name, forbidden)

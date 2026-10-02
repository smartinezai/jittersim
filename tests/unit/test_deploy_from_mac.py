"""deploy-from-mac.sh with fake ssh and scp that record every call: nothing reaches a Pi."""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

from jittersim import updates

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "raspberry-pi" / "deploy-from-mac.sh"
HOST = "jittersim@192.168.0.17"
MANAGER = "/usr/local/lib/jittersim-update.py"
SHELLS = [shell for shell in ("/bin/sh", "/bin/bash", shutil.which("dash")) if shell and
          Path(shell).exists()]

FAKE = r'''#!/usr/bin/env python3
import json, os, shutil, sys
tool = os.path.basename(sys.argv[0])
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"tool": tool, "argv": sys.argv[1:]}) + "\n")
if tool == "scp":
    destination = os.environ.get("FAKE_COPIES")
    if destination:
        shutil.copy(sys.argv[-2], destination)
    sys.exit(int(os.environ.get("FAKE_SCP_EXIT", "0")))
remote = sys.argv[-1]
if remote.startswith("test -f "):
    sys.exit(int(os.environ.get("FAKE_PROBE_EXIT", "0")))
if remote == "jittersim update check":
    print("Installed: " + os.environ.get("FAKE_INSTALLED", ""))
    print("Staged: none")
    sys.exit(0)
if "sha256sum -c" in remote:
    sys.exit(int(os.environ.get("FAKE_UNPACK_EXIT", "0")))
sys.exit(int(os.environ.get("FAKE_TERMINAL_EXIT", "0")))
'''


@pytest.fixture
def fakes(tmp_path):
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    for name in ("ssh", "scp"):
        path = bin_directory / name
        path.write_text(FAKE)
        path.chmod(0o755)
    key = tmp_path / "keys dir" / "it's_ed25519"
    key.parent.mkdir()
    key.write_text("not a real key")
    copies = tmp_path / "copies"
    copies.mkdir()
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    return {"bin": bin_directory, "key": key, "log": tmp_path / "calls.log", "copies": copies,
            "tmp": temporary}


def deploy(fakes, *arguments, shell="/bin/sh", **environment):
    env = {
        "PATH": f"{fakes['bin']}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", "/tmp"),
        "TMPDIR": str(fakes["tmp"]),
        "FAKE_LOG": str(fakes["log"]),
        "FAKE_COPIES": str(fakes["copies"]),
        **environment,
    }
    result = subprocess.run(
        [shell, str(SCRIPT), "--key", str(fakes["key"]), *arguments],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120,
    )
    calls = []
    if fakes["log"].exists():
        calls = [json.loads(line) for line in fakes["log"].read_text().splitlines()]
    return result, calls


def short_head():
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def printed_sha(output):
    return next(line.split(": ", 1)[1] for line in output.splitlines()
                if line.startswith("SHA-256: "))


def ssh_options(argv, *, batch):
    assert argv.count("-o") >= 2
    assert ("BatchMode=yes" in argv) is batch
    assert "ConnectTimeout=10" in argv
    assert not any("StrictHostKeyChecking" in item for item in argv)
    assert argv[argv.index("-i") + 1].endswith("it's_ed25519")
    assert argv[argv.index("--") + 1] == HOST


def test_dry_run_prints_every_command_and_runs_none(fakes):
    result, calls = deploy(fakes, "--dry-run", "--install")
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert list(fakes["tmp"].iterdir()) == []
    printed = [line for line in result.stdout.splitlines() if line.startswith("+ ")]
    assert printed[0].startswith("+ sh deploy/raspberry-pi/build-release.sh ")
    assert printed[1].endswith("'test -f /usr/local/lib/jittersim-update.py'")
    assert printed[2].startswith("+ scp -o BatchMode=yes -o ConnectTimeout=10")
    assert printed[3].startswith("+ ssh -t -o ConnectTimeout=10")
    assert " stage " in printed[3] and "&& sudo jittersim update install --yes" in printed[3]
    assert printed[4].endswith(" 'jittersim update check'")
    assert len(printed) == 5


def test_stage_only_copies_the_verified_archive_and_prints_the_install_command(fakes, tmp_path):
    short = short_head()
    result, calls = deploy(fakes)
    assert result.returncode == 0, result.stderr
    assert [call["tool"] for call in calls] == ["ssh", "scp", "ssh"]
    probe, copy, stage = (call["argv"] for call in calls)
    ssh_options(probe, batch=True)
    assert probe[-1] == f"test -f {MANAGER}"
    assert "BatchMode=yes" in copy and "ConnectTimeout=10" in copy
    assert not any("StrictHostKeyChecking" in item for item in copy)
    assert copy[-1] == f"{HOST}:jittersim-release-{short}.tar.gz"
    assert copy[-2].endswith(f"jittersim-release-{short}.tar.gz")
    ssh_options(stage, batch=False)
    assert stage[0] == "-t"
    sha = printed_sha(result.stdout)
    assert stage[-1] == (
        f"sudo /usr/bin/python3 -I {MANAGER} stage jittersim-release-{short}.tar.gz "
        f"--version {short} --sha256 {sha}"
    )
    assert "update install" not in stage[-1]
    assert f"{HOST} 'sudo jittersim update install'" in result.stdout
    assert "Install staged release" in result.stdout
    # the temporary build folder is gone and the copied archive is the one that was verified
    assert list(fakes["tmp"].iterdir()) == []
    archive = fakes["copies"] / f"jittersim-release-{short}.tar.gz"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == sha
    # main's update flow accepts it exactly as the Pi's root-only staging step does
    root = tmp_path / "updates"
    root.mkdir(mode=0o700)
    updates.stage_release(archive, sha, short, root=root)
    assert json.loads((root / "staged" / ".jittersim-release.json").read_text()) == {
        "version": short, "sha256": sha}
    assert (root / "staged" / "src" / "jittersim" / "updates.py").is_file()


def test_the_archive_is_the_one_build_release_makes(fakes):
    result, _ = deploy(fakes)
    archive = next(fakes["copies"].iterdir())
    with tarfile.open(archive) as bundle:
        tops = {Path(name).parts[0] for name in bundle.getnames()}
    assert tops == {"pyproject.toml", "README.md", "src", "deploy"}
    assert "Commit: " in result.stdout


def test_install_stages_installs_waits_and_compares_the_running_release(fakes):
    short = short_head()
    result, calls = deploy(fakes, "--install", FAKE_INSTALLED=short)
    assert result.returncode == 0, result.stderr
    assert [call["tool"] for call in calls] == ["ssh", "scp", "ssh", "ssh"]
    terminal = calls[2]["argv"]
    assert terminal[0] == "-t"
    assert terminal[-1].endswith(
        " && sudo jittersim update install --yes && sudo systemctl start jittersim-update.service"
    )
    check = calls[3]["argv"]
    ssh_options(check, batch=True)
    assert check[-1] == "jittersim update check"
    assert f"The Pi now runs {short}." in result.stdout
    assert "git rev-parse --short HEAD" in result.stdout


def test_install_reports_a_different_running_release(fakes):
    result, _ = deploy(fakes, "--install", FAKE_INSTALLED="abc1234")
    assert result.returncode == 1
    assert "the Pi reports 'abc1234'" in result.stderr


def test_a_pi_without_the_update_service_needs_the_installer_once(fakes):
    result, calls = deploy(fakes, FAKE_PROBE_EXIT="1")
    assert result.returncode == 1
    assert "Run this once with --installer" in result.stderr
    assert [call["tool"] for call in calls] == ["ssh"]


def test_an_unreachable_pi_stops_before_copying(fakes):
    result, calls = deploy(fakes, FAKE_PROBE_EXIT="255")
    assert result.returncode == 1
    assert "could not reach" in result.stderr
    assert len(calls) == 1


def test_a_failed_sudo_step_is_reported(fakes):
    result, _ = deploy(fakes, FAKE_TERMINAL_EXIT="1")
    assert result.returncode == 1
    assert "staging on the Pi failed" in result.stderr


def test_installer_mode_checks_unpacks_and_labels_the_copy(fakes, tmp_path):
    short = short_head()
    result, calls = deploy(fakes, "--installer")
    assert result.returncode == 0, result.stderr
    assert [call["tool"] for call in calls] == ["scp", "ssh"]
    unpack = calls[1]["argv"]
    ssh_options(unpack, batch=True)
    sha = printed_sha(result.stdout)
    assert f" {sha} " in unpack[-1] and "sha256sum -c -" in unpack[-1]
    assert f"./jittersim-release-{short}/deploy/raspberry-pi/install.sh" in result.stdout
    if shutil.which("sha256sum") is None:
        return
    # the remote command does what it says in a stand-in home folder
    home = tmp_path / "pi-home"
    home.mkdir()
    shutil.copy(fakes["copies"] / f"jittersim-release-{short}.tar.gz", home)
    subprocess.run(["sh", "-c", unpack[-1]], cwd=home, check=True, capture_output=True)
    release = home / f"jittersim-release-{short}"
    assert (release / "deploy" / "raspberry-pi" / "install.sh").stat().st_mode & stat.S_IXUSR
    assert json.loads((release / ".jittersim-release.json").read_text()) == {
        "version": short, "sha256": sha}
    # a changed copy is refused before anything is unpacked
    shutil.rmtree(release)
    with open(home / f"jittersim-release-{short}.tar.gz", "ab") as archive:
        archive.write(b"changed")
    changed = subprocess.run(["sh", "-c", unpack[-1]], cwd=home, capture_output=True)
    assert changed.returncode != 0
    assert not release.exists()


def test_installer_mode_with_install_runs_the_installer_in_a_terminal(fakes):
    short = short_head()
    result, calls = deploy(fakes, "--installer", "--install", FAKE_INSTALLED=short)
    assert result.returncode == 0, result.stderr
    assert [call["tool"] for call in calls] == ["scp", "ssh", "ssh", "ssh"]
    terminal = calls[2]["argv"]
    ssh_options(terminal, batch=False)
    assert terminal[-1] == f"sudo ./jittersim-release-{short}/deploy/raspberry-pi/install.sh"
    assert f"The Pi now runs {short}." in result.stdout


@pytest.mark.parametrize(
    "host",
    ["-oProxyCommand=touch /tmp/x", "jittersim@pi;rm -rf ~", "jitter sim@pi", "user@$(id)",
     "a@b@c", "@pi", "user@", "user@-pi", "", "user@pi`id`", "user@pi|x", "user@pi:22",
     "user@pi/x", "user@pi\nx"],
)
def test_unsafe_hosts_are_refused_before_anything_runs(fakes, host):
    result, calls = deploy(fakes, "--host", host)
    assert result.returncode == 2
    assert calls == []
    assert list(fakes["copies"].iterdir()) == []


def test_a_missing_key_or_an_option_like_key_is_refused(fakes, tmp_path):
    result, calls = deploy(fakes, "--key", str(tmp_path / "missing"))
    assert result.returncode == 2 and calls == []
    assert "does not exist" in result.stderr
    result, calls = deploy(fakes, "--key", "-oProxyCommand=x")
    assert result.returncode == 2 and calls == []


@pytest.mark.parametrize("ref", ["-x", "--output=/tmp/x", "no-such-ref-anywhere"])
def test_unsafe_or_unknown_refs_are_refused(fakes, ref):
    result, calls = deploy(fakes, "--ref", ref)
    assert result.returncode == 2
    assert calls == []


def test_unknown_options_are_usage_errors(fakes):
    result, calls = deploy(fakes, "--password", "secret")
    assert result.returncode == 2
    assert calls == []


@pytest.mark.parametrize("shell", SHELLS)
def test_it_runs_under_each_available_shell(fakes, shell):
    result, calls = deploy(fakes, "--dry-run", shell=shell)
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert "/keys dir/it'\\''s_ed25519'" in result.stdout


def test_help_names_the_defaults(fakes):
    result, _ = deploy(fakes, "--help")
    assert result.returncode == 0
    assert HOST in result.stdout and "~/.ssh/jittersim_ed25519" in result.stdout


def test_the_script_never_handles_a_password():
    source = SCRIPT.read_text()
    for forbidden in ("sudo -S", "read -s", "sshpass", "SSH_ASKPASS", "StrictHostKeyChecking=",
                      "python3 -m jittersim", "accept-new"):
        assert forbidden not in source
    assert os.access(SCRIPT, os.X_OK)

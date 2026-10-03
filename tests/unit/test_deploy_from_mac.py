"""deploy-from-mac.sh with fake ssh, scp and sleep that record every call: nothing reaches a Pi."""

import hashlib
import json
import os
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

from jittersim import updates
from tests.release_support import have_ssh_keygen, make_key, sign

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "raspberry-pi" / "deploy-from-mac.sh"
BUILD = ROOT / "deploy" / "raspberry-pi" / "build-release.sh"
HOST = "jittersim@192.168.0.17"
MANAGER = "/usr/local/lib/jittersim-update.py"
SHELLS = [shell for shell in ("/bin/sh", "/bin/bash", shutil.which("dash")) if shell and
          Path(shell).exists()]
CONNECTION = ["-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15", "-o",
              "ServerAliveCountMax=3", "-o", "IdentitiesOnly=yes"]
BATCH_OPTIONS = ["-o", "BatchMode=yes", *CONNECTION]
TERMINAL_OPTIONS = ["-t", *CONNECTION]
PROBE = (f"if test -f {MANAGER}; then echo update-service; fi; "
         f"if grep -q '^SIGNED_RELEASES = True' {MANAGER} 2>/dev/null; then echo signed-updates; fi; "
         "if grep -q '^jittersim-release ' /etc/jittersim/release-signers 2>/dev/null; "
         "then echo trusted-key; fi; "
         "if sudo -n -l /usr/local/bin/jittersim update stage "
         "/home/jittersim/incoming/jittersim-release.tar.gz >/dev/null 2>&1; "
         "then echo passwordless; fi; mkdir -p -m 700 incoming 2>/dev/null; "
         "jittersim update check 2>/dev/null || true")
WAIT = "echo 'Waiting for the update service to install and check the release.'"
NETWORK_HINT = "Deploying needs a connection to the Pi's router network"

FAKE = r'''#!/usr/bin/env python3
import json, os, shutil, sys, time
tool = os.path.basename(sys.argv[0])
if tool == "sleep":
    seconds = float(sys.argv[1])
    if seconds != 3:
        # a watchdog of the script: it sleeps for real unless the test speeds it up
        time.sleep(seconds * float(os.environ.get("FAKE_SLEEP_SCALE", "1")))
        sys.exit(0)
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"tool": tool, "argv": sys.argv[1:]}) + "\n")
if tool == "sleep":
    sys.exit(0)
if tool == "scp":
    if os.environ.get("FAKE_HANG") == "scp":
        time.sleep(60)
    destination = os.environ.get("FAKE_COPIES")
    if destination:
        shutil.copy(sys.argv[-2], destination)
    sys.exit(int(os.environ.get("FAKE_SCP_EXIT", "0")))
remote = sys.argv[-1]
if remote.startswith("if test -f "):
    if os.environ.get("FAKE_PID_FILE"):
        open(os.environ["FAKE_PID_FILE"], "w").write(str(os.getpid()))
    if os.environ.get("FAKE_HANG") == "probe":
        time.sleep(60)
    if os.environ.get("FAKE_PROBE_EXIT", "0") != "0":
        sys.exit(int(os.environ["FAKE_PROBE_EXIT"]))
    if not os.environ.get("FAKE_NO_SERVICE"):
        print("update-service")
    for marker in ("signed-updates", "trusted-key", "passwordless"):
        if marker in os.environ.get("FAKE_MARKERS", "").split():
            print(marker)
    if os.environ.get("FAKE_PROBE_EXTRA"):
        print(os.environ["FAKE_PROBE_EXTRA"])
    if os.environ.get("FAKE_RUNNING"):
        print("Installed: " + os.environ["FAKE_RUNNING"])
        print("Staged: none")
    sys.exit(0)
if remote == "jittersim update check":
    counter = os.environ["FAKE_LOG"] + ".checks"
    seen = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(seen + 1))
    if seen < int(os.environ.get("FAKE_CHECK_FAILS", "0")):
        print("Release status unavailable: helper not answering", file=sys.stderr)
        sys.exit(1)
    print("Installed: " + os.environ.get("FAKE_INSTALLED", ""))
    print("Staged: none")
    sys.exit(0)
if "sha256sum -c" in remote:
    sys.exit(int(os.environ.get("FAKE_UNPACK_EXIT", "0")))
sys.exit(int(os.environ.get("FAKE_TERMINAL_EXIT", "0")))
'''


# ifconfig and route as the Mac prints them. They log nothing: the tests count ssh and scp calls.
# The default is a Mac on the home network.
NETWORK_FAKE = r'''#!/bin/sh
case "$(basename "$0")" in
    ifconfig)
        printf 'lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384\n\tinet 127.0.0.1 netmask 0xff000000\n'
        printf 'en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n'
        printf '\tinet %s netmask 0xffffff00 broadcast 255.255.255.255\n' "${FAKE_ADDRESS:-192.168.0.23}"
        ;;
    route)
        printf '   route to: default\n destination: default\n     gateway: %s\n  interface: en0\n' "${FAKE_GATEWAY:-192.168.0.1}"
        ;;
esac
'''


@pytest.fixture
def fakes(tmp_path):
    return make_fakes(tmp_path)


def make_fakes(tmp_path):
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    for name in ("ssh", "scp", "sleep"):
        path = bin_directory / name
        path.write_text(FAKE)
        path.chmod(0o755)
    for name in ("ifconfig", "route"):
        path = bin_directory / name
        path.write_text(NETWORK_FAKE)
        path.chmod(0o755)
    key = tmp_path / "keys dir" / "it's_ed25519"
    key.parent.mkdir()
    key.write_text("not a real key")
    copies = tmp_path / "copies"
    copies.mkdir()
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    # A home folder of its own: the tests never read a real release signing key.
    home = tmp_path / "home"
    home.mkdir()
    return {"bin": bin_directory, "key": key, "log": tmp_path / "calls.log", "copies": copies,
            "tmp": temporary, "home": home}


def deploy(fakes, *arguments, shell="/bin/sh", script=SCRIPT, **environment):
    env = {
        "PATH": f"{fakes['bin']}:{os.environ['PATH']}",
        "HOME": str(fakes["home"]),
        "TMPDIR": str(fakes["tmp"]),
        "FAKE_LOG": str(fakes["log"]),
        "FAKE_COPIES": str(fakes["copies"]),
        **environment,
    }
    result = subprocess.run(
        [shell, str(script), "--key", str(fakes["key"]), *arguments],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120,
    )
    calls = []
    if fakes["log"].exists():
        calls = [json.loads(line) for line in fakes["log"].read_text().splitlines()]
    return result, calls


def tools(calls):
    return [call["tool"] for call in calls]


def leftovers(fakes):
    """The script's own temporary folders. Other tools (Apple's git) may use TMPDIR too."""

    return list(fakes["tmp"].glob("jittersim-deploy.*"))


def short_head(repository=ROOT, ref="HEAD"):
    return subprocess.run(["git", "-C", str(repository), "rev-parse", "--short", ref],
                          capture_output=True, text=True, check=True).stdout.strip()


def version_head(repository=ROOT, ref="HEAD"):
    return subprocess.run(["sh", str(repository / "deploy/raspberry-pi/build-release.sh"),
                           "--version", ref], cwd=repository, check=True,
                          capture_output=True, text=True).stdout.strip()


def printed_sha(output):
    return next(line.split(": ", 1)[1] for line in output.splitlines()
                if line.startswith("SHA-256: "))


def batch_call(argv, fakes, remote):
    """An ssh call that may never ask anything and uses the user's known_hosts as it is."""

    assert argv == [*BATCH_OPTIONS, "-i", str(fakes["key"]), "--", HOST, remote]


def terminal_call(argv, fakes, remote):
    """The one ssh call that opens a terminal so sudo asks on the Pi: never in batch mode."""

    assert argv == [*TERMINAL_OPTIONS, "-i", str(fakes["key"]), "--", HOST, remote]


def stage_command(short, sha, version=None):
    version = version or version_head()
    return (f"sudo /usr/bin/python3 -I {MANAGER} stage jittersim-release-{short}.tar.gz "
            f"--version {version} --sha256 {sha} && rm -f jittersim-release-{short}.tar.gz")


def test_dry_run_prints_every_command_and_runs_none(fakes):
    result, calls = deploy(fakes, "--dry-run", "--install")
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert leftovers(fakes) == []
    assert list(fakes["copies"].iterdir()) == []
    printed = [line for line in result.stdout.splitlines() if line.startswith("+ ")]
    assert printed[0].startswith("+ sh deploy/raspberry-pi/build-release.sh ")
    assert printed[1].startswith("+ ssh -o BatchMode=yes -o ConnectTimeout=8")
    assert shlex.split(printed[1][2:])[-1] == PROBE
    assert printed[2].startswith("+ scp -o BatchMode=yes -o ConnectTimeout=8")
    assert printed[3].startswith("+ ssh -t -o ConnectTimeout=8 -o ServerAliveInterval=15")
    assert " stage " in printed[3] and "&& sudo jittersim update install --yes" in printed[3]
    assert "sudo systemctl start jittersim-update.service" in printed[3]
    assert printed[4].endswith(" 'jittersim update check'")
    assert len(printed) == 5
    assert f"Connecting to {HOST} with the key " in result.stdout
    assert result.stdout.rstrip().endswith("Dry run: nothing was built, copied or run on the Pi.")


@pytest.mark.parametrize("extra", [(), ("--use-installer",), ("--use-installer", "--install")])
def test_every_dry_run_runs_nothing(fakes, extra):
    result, calls = deploy(fakes, "--dry-run", *extra, FAKE_PROBE_EXIT="255")
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert leftovers(fakes) == []
    assert "Dry run: nothing was built" in result.stdout


def test_stage_only_copies_the_verified_archive_and_prints_the_install_command(fakes, tmp_path):
    short = short_head()
    result, calls = deploy(fakes, "--stage-only")
    assert result.returncode == 0, result.stderr
    assert tools(calls) == ["ssh", "scp", "ssh"]
    probe, copy, stage = (call["argv"] for call in calls)
    batch_call(probe, fakes, PROBE)
    archive_name = f"jittersim-release-{short}.tar.gz"
    assert copy[:-2] == [*BATCH_OPTIONS, "-i", str(fakes["key"]), "--"]
    assert copy[-1] == f"{HOST}:{archive_name}"
    assert Path(copy[-2]).name == archive_name and Path(copy[-2]).is_absolute()
    sha = printed_sha(result.stdout)
    terminal_call(stage, fakes, stage_command(short, sha))
    assert f"{HOST} 'sudo jittersim update install'" in result.stdout
    assert "Install staged release" in result.stdout
    # the temporary build folder is gone and the copied archive is the one that was checked
    assert leftovers(fakes) == []
    archive = fakes["copies"] / archive_name
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == sha
    # An update manager from before signed releases accepts only these top-level names.
    with tarfile.open(archive) as bundle:
        assert {Path(name).parts[0] for name in bundle.getnames()} <= {
            "src", "deploy", "README.md", "pyproject.toml"}
    # the update flow accepts it exactly as the Pi's root-only staging step does once it is
    # signed by a trusted key
    if not have_ssh_keygen():
        pytest.skip("ssh-keygen signs the release")
    root, config = tmp_path / "updates", tmp_path / "config"
    config.mkdir(mode=0o755)
    private, public = make_key(tmp_path / "throwaway")
    updates.trust_key(public, config=config, root=root)
    sign(archive, private)
    updates.stage_release(archive, root=root, code=tmp_path / "code", config=config, sha256=sha,
                          version=version_head())
    record = json.loads((root / "staged" / ".jittersim-release.json").read_text())
    assert record["version"] == version_head() and record["sha256"] == sha
    assert record["commit"].startswith(short) and record["sequence"] > 1_700_000_000
    assert (root / "staged" / "src" / "jittersim" / "updates.py").is_file()


def test_the_archive_is_the_one_build_release_makes(fakes):
    result, _ = deploy(fakes, "--stage-only")
    assert result.returncode == 0, result.stderr
    archive = next(fakes["copies"].iterdir())
    with tarfile.open(archive) as bundle:
        tops = {Path(name).parts[0] for name in bundle.getnames()}
    assert tops == {"pyproject.toml", "README.md", "src", "deploy"}
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                            text=True, check=True).stdout.strip()
    assert result.stdout.splitlines()[0] == f"Commit: {commit} ({short_head()})"


def test_install_stages_installs_waits_and_compares_the_running_release(fakes):
    short = short_head()
    result, calls = deploy(fakes, FAKE_INSTALLED=version_head())
    assert result.returncode == 0, result.stderr
    assert tools(calls) == ["ssh", "scp", "ssh", "ssh"]
    sha = printed_sha(result.stdout)
    terminal_call(calls[2]["argv"], fakes, (
        stage_command(short, sha) + " && sudo jittersim update install --yes && " + WAIT
        + " && sudo systemctl start jittersim-update.service"
    ))
    batch_call(calls[3]["argv"], fakes, "jittersim update check")
    assert f"The Pi now runs release {version_head()}." in result.stdout
    assert "git rev-parse --short HEAD" in result.stdout
    # the command is shown once and its answer is not mixed into the trace
    assert result.stdout.count("'jittersim update check'") == 1
    assert "Installed:" not in result.stdout


@pytest.mark.parametrize("extra", [(), ("--install",)])
def test_a_pi_that_already_runs_the_release_needs_nothing(fakes, extra):
    short = short_head()
    result, calls = deploy(fakes, *extra, FAKE_RUNNING=version_head())
    assert result.returncode == 0, result.stderr
    assert tools(calls) == ["ssh"]
    assert f"The Pi already runs release {version_head()}. Nothing to stage or install." in result.stdout


def test_the_installer_runs_again_for_the_same_release(fakes):
    short = short_head()
    result, calls = deploy(fakes, "--use-installer", "--stage-only", FAKE_RUNNING=version_head())
    assert result.returncode == 0, result.stderr
    assert tools(calls) == ["ssh", "scp", "ssh"]
    assert f"The Pi already runs release {version_head()}. The installer runs again." in result.stdout


def test_install_reports_a_different_running_release(fakes):
    result, _ = deploy(fakes, "--install", FAKE_INSTALLED="abc1234")
    assert result.returncode == 1
    assert "the Pi reports release 'abc1234'" in result.stderr
    assert "journalctl -u jittersim-update.service" in result.stderr
    assert "The Pi now runs" not in result.stdout


def test_install_waits_for_the_helper_to_answer_again(fakes):
    short = short_head()
    result, calls = deploy(fakes, "--install", FAKE_INSTALLED=version_head(), FAKE_CHECK_FAILS="3")
    assert result.returncode == 0, result.stderr
    assert tools(calls) == ["ssh", "scp", "ssh", "ssh", "sleep", "ssh", "sleep", "ssh", "sleep",
                            "ssh"]
    assert f"The Pi now runs release {version_head()}." in result.stdout


def test_install_gives_up_when_the_release_cannot_be_read_and_says_why(fakes):
    result, calls = deploy(fakes, "--install", FAKE_INSTALLED=version_head(),
                           FAKE_CHECK_FAILS="99")
    assert result.returncode == 1
    # the probe and the sudo step, then ten checks with a pause between two of them
    assert tools(calls) == ["ssh", "scp", "ssh"] + ["ssh", "sleep"] * 9 + ["ssh"]
    assert all(call["argv"] == ["3"] for call in calls if call["tool"] == "sleep")
    assert "could not read the installed release" in result.stderr
    assert "Release status unavailable: helper not answering" in result.stderr


def test_a_failed_install_on_the_pi_stops_before_the_check(fakes):
    result, calls = deploy(fakes, "--install", FAKE_TERMINAL_EXIT="1")
    assert result.returncode == 1
    assert tools(calls) == ["ssh", "scp", "ssh"]
    assert "staging or installing on the Pi failed" in result.stderr
    assert "jittersim update check" in result.stderr


def test_a_pi_without_the_update_service_needs_the_installer_once(fakes):
    result, calls = deploy(fakes, FAKE_NO_SERVICE="1")
    assert result.returncode == 1
    assert "Run this once with --use-installer" in result.stderr
    assert tools(calls) == ["ssh"]


@pytest.mark.parametrize("extra", [(), ("--use-installer",)])
def test_an_unreachable_pi_stops_before_copying_and_says_which_network(fakes, extra):
    result, calls = deploy(fakes, "--ssh", *extra, FAKE_PROBE_EXIT="255")
    assert result.returncode == 1
    assert f"could not reach {HOST} over SSH (exit 255)" in result.stderr
    assert NETWORK_HINT in result.stderr and "test Wi-Fi" in result.stderr
    assert "known_hosts" in result.stderr
    assert f"Connecting to {HOST} with the key {fakes['key']}." in result.stdout
    assert tools(calls) == ["ssh"]
    assert list(fakes["copies"].iterdir()) == []


@pytest.mark.parametrize("hang, words", [
    ("probe", "Checking the Pi did not finish within 60 seconds."),
    ("scp", "Copying the archive did not finish within 600 seconds."),
])
def test_a_call_that_hangs_is_stopped_at_its_deadline(fakes, hang, words):
    started = time.monotonic()
    result, _ = deploy(fakes, "--ssh", FAKE_HANG=hang, FAKE_SLEEP_SCALE="0.002")
    assert result.returncode == 1
    assert words in result.stderr and NETWORK_HINT in result.stderr
    assert time.monotonic() - started < 30
    assert leftovers(fakes) == []


@pytest.mark.parametrize("shell", SHELLS)
def test_ctrl_c_also_stops_a_running_call(fakes, tmp_path, shell):
    """Background calls ignore Ctrl-C: the script stops them itself."""

    pid_file = tmp_path / "ssh.pid"
    env = {"PATH": f"{fakes['bin']}:{os.environ['PATH']}", "HOME": str(fakes["home"]),
           "TMPDIR": str(fakes["tmp"]), "FAKE_LOG": str(fakes["log"]),
           "FAKE_HANG": "probe", "FAKE_PID_FILE": str(pid_file)}
    process = subprocess.Popen([shell, str(SCRIPT), "--key", str(fakes["key"])], env=env,
                               cwd=str(ROOT), stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.monotonic() + 30
        while not pid_file.exists() or not pid_file.read_text():
            assert time.monotonic() < deadline, "the probe never started"
            time.sleep(0.05)
        os.killpg(process.pid, signal.SIGINT)
        assert process.wait(timeout=15) != 0
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            assert time.monotonic() < deadline, "the hanging ssh call kept running"
            time.sleep(0.05)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
    assert leftovers(fakes) == []


def test_a_failed_copy_stops_before_staging(fakes):
    result, calls = deploy(fakes, FAKE_SCP_EXIT="1")
    assert result.returncode == 1
    assert tools(calls) == ["ssh", "scp"]
    assert "copying the archive to the Pi failed (exit 1)" in result.stderr


def test_a_failed_sudo_step_is_reported(fakes):
    result, _ = deploy(fakes, "--stage-only", FAKE_TERMINAL_EXIT="1")
    assert result.returncode == 1
    assert "staging on the Pi failed" in result.stderr
    assert "Install it with" not in result.stdout


def unpack_on_a_stand_in_pi(fakes, home, short, command):
    if shutil.which("sha256sum") is None:
        pytest.skip("sha256sum is needed to run the Pi side of the command")
    home.mkdir(exist_ok=True)
    shutil.copy(fakes["copies"] / f"jittersim-release-{short}.tar.gz", home)
    return subprocess.run(["sh", "-c", command], cwd=home, capture_output=True)


def test_installer_mode_checks_unpacks_and_labels_the_copy(fakes, tmp_path):
    short = short_head()
    result, calls = deploy(fakes, "--use-installer", "--stage-only")
    assert result.returncode == 0, result.stderr
    assert tools(calls) == ["ssh", "scp", "ssh"]
    batch_call(calls[0]["argv"], fakes, PROBE)
    unpack = calls[2]["argv"]
    sha = printed_sha(result.stdout)
    batch_call(unpack, fakes, unpack[-1])
    assert unpack[-1].startswith(f"printf '%s  %s\\n' {sha} jittersim-release-{short}.tar.gz"
                                 " | sha256sum -c - && ")
    assert f"'sudo ./jittersim-release-{short}/deploy/raspberry-pi/install.sh'" in result.stdout
    # the remote command does what it says in a stand-in home folder
    home = tmp_path / "pi-home"
    assert unpack_on_a_stand_in_pi(fakes, home, short, unpack[-1]).returncode == 0
    release = home / f"jittersim-release-{short}"
    assert (release / "deploy" / "raspberry-pi" / "install.sh").stat().st_mode & stat.S_IXUSR
    with tarfile.open(fakes["copies"] / f"jittersim-release-{short}.tar.gz") as bundle:
        manifest = json.load(bundle.extractfile("deploy/RELEASE"))
    assert json.loads((release / ".jittersim-release.json").read_text()) == {
        "version": version_head(), "sha256": sha,
        "commit": manifest["commit"], "sequence": manifest["sequence"]}
    assert not (home / f"jittersim-release-{short}.tar.gz").exists()
    # a changed copy is refused before anything is unpacked
    shutil.rmtree(release)
    with open(fakes["copies"] / f"jittersim-release-{short}.tar.gz", "ab") as archive:
        archive.write(b"changed")
    assert unpack_on_a_stand_in_pi(fakes, home, short, unpack[-1]).returncode != 0
    assert not release.exists()


def test_the_installer_label_reaches_the_installed_folder(fakes, tmp_path):
    """The label written next to the unpacked source is what replace-source.py installs."""

    short = short_head()
    result, calls = deploy(fakes, "--use-installer", "--stage-only")
    assert result.returncode == 0, result.stderr
    home = tmp_path / "pi-home"
    assert unpack_on_a_stand_in_pi(fakes, home, short, calls[2]["argv"][-1]).returncode == 0
    installed = tmp_path / "opt-jittersim"
    installed.mkdir()
    release = home / f"jittersim-release-{short}"
    subprocess.run([sys.executable, str(release / "deploy/raspberry-pi/replace-source.py"),
                    str(release), str(installed)], check=True, capture_output=True)
    assert updates.update_status(root=tmp_path / "none", code=installed)["installed"] == version_head()
    assert json.loads((installed / ".jittersim-release.json").read_text())["sha256"] == (
        printed_sha(result.stdout))


def test_installer_mode_installs_by_default_in_a_terminal(fakes):
    short = short_head()
    result, calls = deploy(fakes, "--use-installer", FAKE_INSTALLED=version_head())
    assert result.returncode == 0, result.stderr
    assert tools(calls) == ["ssh", "scp", "ssh", "ssh", "ssh"]
    terminal_call(calls[3]["argv"], fakes,
                  f"sudo ./jittersim-release-{short}/deploy/raspberry-pi/install.sh")
    batch_call(calls[4]["argv"], fakes, "jittersim update check")
    assert f"The Pi now runs release {version_head()}." in result.stdout


def test_installer_mode_works_on_a_pi_without_the_update_service(fakes):
    result, calls = deploy(fakes, "--use-installer", "--stage-only", FAKE_NO_SERVICE="1")
    assert result.returncode == 0, result.stderr
    assert tools(calls) == ["ssh", "scp", "ssh"]


def test_installer_mode_reports_a_failed_check_of_the_copy(fakes):
    result, calls = deploy(fakes, "--use-installer", "--install", FAKE_UNPACK_EXIT="1")
    assert result.returncode == 1
    assert tools(calls) == ["ssh", "scp", "ssh"]
    assert "checking or unpacking the archive on the Pi failed" in result.stderr


@pytest.fixture
def repository(tmp_path):
    """A small checkout with this script, build-release.sh and two commits."""

    checkout = tmp_path / "checkout"
    (checkout / "deploy" / "raspberry-pi").mkdir(parents=True)
    (checkout / "src" / "jittersim").mkdir(parents=True)
    shutil.copy(SCRIPT, checkout / "deploy" / "raspberry-pi")
    shutil.copy(BUILD, checkout / "deploy" / "raspberry-pi")
    shutil.copy(BUILD.with_name("check-release-manifest.sh"), checkout / "deploy/raspberry-pi")
    (checkout / "pyproject.toml").write_text('[project]\nname = "jittersim"\nversion = "0.2.0"\n')
    (checkout / "src" / "jittersim" / "__init__.py").write_text('__version__ = "0.2.0"\n')

    def git(*arguments):
        subprocess.run(["git", "-C", str(checkout), "-c", "user.name=Test",
                        "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                        *arguments], check=True, capture_output=True)

    git("init", "-q")
    (checkout / "README.md").write_text("first release\n")
    git("add", "-A")
    git("commit", "-q", "-m", "first")
    (checkout / "README.md").write_text("second release\n")
    git("commit", "-q", "-am", "second")
    return checkout


def archived_readme(archive):
    with tarfile.open(archive) as bundle:
        return bundle.extractfile("README.md").read().decode()


def test_build_of_another_commit_is_refused_before_connection(fakes, repository):
    build = repository / "deploy/raspberry-pi/build-release.sh"
    real = build.with_name("build-release-real.sh")
    shutil.copy(build, real)
    build.write_text('#!/bin/sh\nif [ "$1" = --version ]; then\n'
                     'exec sh "$(dirname -- "$0")/build-release-real.sh" "$@"\nfi\n'
                     'exec sh "$(dirname -- "$0")/build-release-real.sh" HEAD~1 "$2"\n')
    result, calls = deploy(fakes, "--stage-only",
                           script=repository / "deploy/raspberry-pi/deploy-from-mac.sh")
    assert result.returncode != 0
    assert "manifest" in result.stderr
    assert calls == []


def test_the_archive_is_built_from_the_commit_and_leaves_local_edits_out(fakes, repository):
    (repository / "README.md").write_text("an uncommitted edit\n")
    script = repository / "deploy" / "raspberry-pi" / "deploy-from-mac.sh"
    result, calls = deploy(fakes, "--stage-only", script=script)
    assert result.returncode == 0, result.stderr
    assert "Uncommitted changes in this checkout are not part of the archive." in result.stdout
    short = short_head(repository)
    archive = fakes["copies"] / f"jittersim-release-{short}.tar.gz"
    assert archived_readme(archive) == "second release\n"
    assert calls[1]["argv"][-1] == f"{HOST}:jittersim-release-{short}.tar.gz"


def test_an_older_ref_builds_that_commit(fakes, repository):
    script = repository / "deploy" / "raspberry-pi" / "deploy-from-mac.sh"
    result, calls = deploy(fakes, "--ref", "HEAD~1", "--stage-only", script=script)
    assert result.returncode == 0, result.stderr
    assert "Uncommitted changes" not in result.stdout
    short = short_head(repository, "HEAD~1")
    assert archived_readme(fakes["copies"] / f"jittersim-release-{short}.tar.gz") == (
        "first release\n")
    assert f"--version {version_head(repository, 'HEAD~1')} " in calls[2]["argv"][-1]


def test_a_checksum_that_does_not_match_the_archive_stops_everything(fakes, repository):
    build = repository / "deploy" / "raspberry-pi" / "build-release.sh"
    zeros = "0" * 64
    build.write_text(build.read_text()
                     .replace('sha256sum "$archive"', f'echo "{zeros}  $archive"')
                     .replace('shasum -a 256 "$archive"', f'echo "{zeros}  $archive"'))
    script = repository / "deploy" / "raspberry-pi" / "deploy-from-mac.sh"
    result, calls = deploy(fakes, script=script)
    assert result.returncode == 1
    assert "build-release.sh printed the checksum" in result.stderr
    assert calls == []


UNSAFE_HOSTS = [
    "-oProxyCommand=touch /tmp/x", "jittersim@pi;rm -rf ~", "jitter sim@pi", "user@$(id)",
    "a@b@c", "@pi", "user@", "user@-pi", "", "user@pi`id`", "user@pi|x", "user@pi:22",
    "user@pi/x", "user@pi\nx", "user@pi&x", "user@pi>x", "user@pi'x", 'user@pi"x',
    "user@pi*", "user@pi\tx", "jittersim@pí", "jïttersim@pi", "user@Ａpi",
]


@pytest.mark.parametrize("host", UNSAFE_HOSTS)
def test_unsafe_hosts_are_refused_before_anything_runs(fakes, host):
    result, calls = deploy(fakes, "--host", host)
    assert result.returncode == 2
    assert calls == []
    assert list(fakes["copies"].iterdir()) == []
    assert "refusing host" in result.stderr


@pytest.mark.parametrize("shell", SHELLS)
def test_letters_outside_ascii_are_refused_in_a_utf8_locale(fakes, shell):
    for host in ("jittersim@pí", "user@Ａpi", "ßuser@pi"):
        result, calls = deploy(fakes, "--host", host, shell=shell, LANG="en_US.UTF-8",
                               LC_ALL="en_US.UTF-8")
        assert result.returncode == 2, (shell, host)
        assert calls == []


@pytest.mark.parametrize("host", ["pi.local", "jittersim@pi.local", "pi_user@192.168.0.17"])
def test_plain_hosts_are_accepted(fakes, host):
    result, calls = deploy(fakes, "--dry-run", "--host", host)
    assert result.returncode == 0, result.stderr
    assert f" -- {host} " in result.stdout


def test_a_missing_key_or_an_option_like_key_is_refused(fakes, tmp_path):
    result, calls = deploy(fakes, "--ssh", "--key", str(tmp_path / "missing"))
    assert result.returncode == 2 and calls == []
    assert "does not exist" in result.stderr
    result, calls = deploy(fakes, "--key", "-oProxyCommand=x")
    assert result.returncode == 2 and calls == []
    result, calls = deploy(fakes, "--key", str(tmp_path))
    assert result.returncode == 2 and calls == []


@pytest.mark.parametrize("ref", ["-x", "--output=/tmp/x", ""])
def test_unsafe_refs_are_refused(fakes, ref):
    result, calls = deploy(fakes, "--ref", ref)
    assert result.returncode == 2
    assert calls == []
    assert "refusing ref" in result.stderr


def test_an_unknown_ref_is_refused(fakes):
    result, calls = deploy(fakes, "--ref", "no-such-ref-anywhere")
    assert result.returncode == 2
    assert calls == []
    assert "unknown ref" in result.stderr


@pytest.mark.parametrize("arguments", [("--password", "secret"), ("--host",), ("extra",)])
def test_unknown_options_are_usage_errors(fakes, arguments):
    result, calls = deploy(fakes, *arguments)
    assert result.returncode == 2
    assert calls == []
    assert "secret" not in result.stdout + result.stderr


@pytest.mark.parametrize("shell", SHELLS)
def test_it_runs_under_each_available_shell(fakes, shell):
    result, calls = deploy(fakes, "--dry-run", shell=shell)
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert "/keys dir/it'\\''s_ed25519'" in result.stdout
    result, calls = deploy(fakes, "--install", shell=shell, FAKE_INSTALLED=version_head())
    assert result.returncode == 0, result.stderr
    assert tools(calls) == ["ssh", "scp", "ssh", "ssh"]
    result, _ = deploy(fakes, "--ssh", shell=shell, FAKE_HANG="probe", FAKE_SLEEP_SCALE="0.002")
    assert "did not finish within 60 seconds" in result.stderr


def test_help_names_the_defaults_where_it_runs_and_the_network(fakes):
    result, _ = deploy(fakes, "--help")
    assert result.returncode == 0
    assert HOST in result.stdout and "~/.ssh/jittersim_ed25519" in result.stdout
    assert "Runs on your Mac" in result.stdout and "known_hosts" in result.stdout
    assert "router network" in result.stdout and "test Wi-Fi" in result.stdout


def test_the_script_never_handles_a_password_or_trusts_new_host_keys():
    source = SCRIPT.read_text()
    for forbidden in ("sudo -S", "read -s", "sshpass", "SSH_ASKPASS", "StrictHostKeyChecking",
                      "python3 -m jittersim", "accept-new", "UserKnownHostsFile"):
        assert forbidden not in source
    assert os.access(SCRIPT, os.X_OK)


def _in_a_checkout():
    if shutil.which("git") is None:
        return False
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
                          capture_output=True).returncode == 0


# These refuse their input before the script calls git so they also run where the tests run
# from a copy without git (the Pi release image). Every other test needs git and a checkout.
WITHOUT_GIT = {
    "test_unsafe_hosts_are_refused_before_anything_runs",
    "test_letters_outside_ascii_are_refused_in_a_utf8_locale",
    "test_a_missing_key_or_an_option_like_key_is_refused",
    "test_unsafe_refs_are_refused",
    "test_unknown_options_are_usage_errors",
    "test_help_names_the_defaults_where_it_runs_and_the_network",
    "test_the_script_never_handles_a_password_or_trusts_new_host_keys",
    "test_a_mac_on_the_test_wifi_stops_before_anything_is_built_or_sent",
    "test_a_pi_address_on_the_test_wifi_stops_before_ssh",
    "test_the_retry_command_quotes_odd_arguments",
}


# The Mac on the Pi's test Wi-Fi


TEST_WIFI_WORDS = "SSH from the test Wi-Fi is blocked on purpose; join the home network or use Ethernet"


@pytest.mark.parametrize("environment", [
    {"FAKE_ADDRESS": "192.168.50.23"},
    {"FAKE_GATEWAY": "192.168.50.1"},
], ids=["address", "gateway"])
@pytest.mark.parametrize("arguments", [[], ["--install"], ["--dry-run"]])
def test_a_mac_on_the_test_wifi_stops_before_anything_is_built_or_sent(fakes, arguments,
                                                                      environment):
    # Only with --ssh: without it the script deploys over HTTPS instead (tested below).
    result, calls = deploy(fakes, "--ssh", *arguments, **environment)
    assert result.returncode != 0
    assert TEST_WIFI_WORDS in result.stderr
    # The exact command to retry once the Mac is on the home network.
    retry = result.stderr.rstrip().splitlines()[-1].strip()
    assert retry.startswith("deploy/raspberry-pi/deploy-from-mac.sh --key ")
    assert retry.endswith(" --ssh" + "".join(f" {a}" for a in arguments))
    assert calls == []
    assert "Commit:" not in result.stdout
    assert list(fakes["tmp"].iterdir()) == []


def test_a_pi_address_on_the_test_wifi_stops_before_ssh(fakes):
    result, calls = deploy(fakes, "--ssh", "--host", "jittersim@192.168.50.1")
    assert result.returncode != 0
    assert TEST_WIFI_WORDS in result.stderr
    assert "--host jittersim@192.168.50.1" in result.stderr
    assert calls == []


@pytest.mark.parametrize("environment", [
    {"FAKE_ADDRESS": "192.168.50.23"},
    {"FAKE_GATEWAY": "192.168.50.1"},
], ids=["address", "gateway"])
def test_a_mac_on_the_test_wifi_deploys_over_https_instead(fakes, environment):
    result, calls = deploy(fakes, "--dry-run", **environment)
    assert "On the Pi Wi-Fi: SSH is blocked on purpose there, so this deploys over HTTPS instead." \
        in result.stdout
    assert TEST_WIFI_WORDS not in result.stderr
    assert calls == []


def test_a_pi_address_on_the_test_wifi_deploys_over_https_instead(fakes):
    result, calls = deploy(fakes, "--dry-run", "--host", "jittersim@192.168.50.1")
    assert "deploys over HTTPS instead" in result.stdout
    assert calls == []


def test_the_retry_command_quotes_odd_arguments(fakes):
    result, _ = deploy(fakes, "--ssh", "--ref", "my branch", FAKE_ADDRESS="192.168.50.9")
    assert "--ref 'my branch'" in result.stderr


def test_a_similar_address_is_not_the_test_wifi(fakes):
    for address in ("192.168.5.23", "10.192.168.50", "192.168.150.4"):
        result, calls = deploy(fakes, "--dry-run", FAKE_ADDRESS=address, FAKE_GATEWAY="192.168.5.1")
        assert result.returncode == 0, (address, result.stderr)
        assert TEST_WIFI_WORDS not in result.stderr


def test_the_network_check_can_be_skipped_on_purpose(fakes):
    result, calls = deploy(fakes, "--skip-network-check", "--dry-run",
                           FAKE_ADDRESS="192.168.50.23")
    assert result.returncode == 0, result.stderr
    assert TEST_WIFI_WORDS not in result.stderr


def test_every_connection_has_a_short_timeout_and_a_plain_message(fakes):
    result, calls = deploy(fakes, "--ssh", FAKE_PROBE_EXIT="255")
    assert result.returncode != 0
    assert NETWORK_HINT in result.stderr
    assert calls and all("ConnectTimeout=8" in call["argv"] for call in calls
                         if call["tool"] == "ssh")


# Applied last so it also covers the tests defined above this line.
needs_a_checkout = pytest.mark.skipif(
    not _in_a_checkout(), reason="the deploy script builds from a git checkout")
for _name, _test in list(globals().items()):
    if _name.startswith("test_") and _name not in WITHOUT_GIT:
        globals()[_name] = needs_a_checkout(_test)

"""Run only in an allocated offline Linux container with real NetworkManager."""

import hashlib
import json
import os
import pathlib
import runpy
import shutil
import signal
import subprocess
import sys
import time

if (os.environ.get("JITTERSIM_OFFLINE_SECRET_PROOF") != "1"
        or not pathlib.Path("/.dockerenv").exists()
        or {p.name for p in pathlib.Path("/sys/class/net").iterdir()} != {"lo"}):
    raise SystemExit("Use an allocated disposable Docker fixture with --network none")

ROOT = pathlib.Path("/proof/source/deploy/raspberry-pi")
AP = ROOT / "create-ap-profile.py"
LOCK = ROOT / "install-lock.py"
lock = runpy.run_path(str(LOCK))
records = pathlib.Path("/tmp/nmcli-private-proof.jsonl")
wrapper_directory = pathlib.Path("/tmp/private-proof-bin")
wrapper_directory.mkdir()
wrapper = wrapper_directory / "nmcli"
wrapper.write_text('''#!/usr/local/bin/python
import hashlib, json, os, pathlib, shutil, signal, subprocess, sys
args = sys.argv[1:]
wanted = os.environ.get("PROOF_PASSWORD_SHA256", "")
private = lambda text: hashlib.sha256(text.encode()).hexdigest() == wanted
try:
    os.fstat(9)
    inherited_lock = True
except OSError:
    inherited_lock = False
record = {"secret_argument": any(private(a) for a in args),
          "secret_environment": any(private(a) for a in os.environ.values()),
          "password_variable": any(name in os.environ for name in
              ("JITTERSIM_AP_PASSWORD", "ap_password", "jittersim_initial_ap_secret")),
          "inherited_lock": inherited_lock,
          "operation": next((a for a in args if a in
              ("add", "edit", "modify", "show", "delete", "up", "down")), "other")}
with open("/tmp/nmcli-private-proof.jsonl", "a") as log:
    log.write(json.dumps(record) + "\\n")
directory = pathlib.Path("/etc/NetworkManager/system-connections")
backup = pathlib.Path("/etc/NetworkManager/proof-profile-backup")
if os.environ.get("PROOF_FAIL_SAVE") == "1" and "edit" in args:
    shutil.copytree(directory, backup)
    shutil.rmtree(directory)
    directory.write_text("force a real persistent-save failure")
if os.environ.get("PROOF_FAIL_SAVE") == "1" and "delete" in args and backup.exists():
    directory.unlink()
    shutil.copytree(backup, directory)
    shutil.rmtree(backup)
if os.environ.get("PROOF_FAIL_CLEANUP") == "1" and "delete" in args:
    raise SystemExit(7)
if os.environ.get("PROOF_CRASH_AFTER_ADD") == "1" and "add" in args:
    result = subprocess.run(["/usr/bin/nmcli", *args], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=3)
    if result.returncode == 0:
        os.kill(os.getppid(), signal.SIGKILL)
    raise SystemExit(result.returncode)
os.execv("/usr/bin/nmcli", ["nmcli", *args])
''')
wrapper.chmod(0o700)


def nmcli(arguments):
    return subprocess.run(["/usr/bin/nmcli", *arguments], capture_output=True,
                          text=True, timeout=5, check=True).stdout.removesuffix("\n")


def invoke(action, password=b"fixture-password", **flags):
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("JITTERSIM_AP_PASSWORD", "JITTERSIM_INSTALL_LOCK_FD")}
    environment.update(PATH=str(wrapper_directory) + ":" + os.environ["PATH"],
                       PROOF_PASSWORD_SHA256=hashlib.sha256(password).hexdigest(),
                       PYTHONPATH="/nonexistent/broken-installed-package")
    environment.update(flags)
    return subprocess.run([sys.executable, "-I", str(AP), action, "--interface", "wlan-proof",
                           "--ssid", "TestNet"], input=password + b"\n",
                          capture_output=True, timeout=20, env=environment, pass_fds=(9,))


pathlib.Path("/run/dbus").mkdir(parents=True, exist_ok=True)
pathlib.Path("/etc/NetworkManager/conf.d").mkdir(parents=True, exist_ok=True)
pathlib.Path("/etc/NetworkManager/conf.d/99-private-proof.conf").write_text(
    "[main]\nplugins=keyfile\ndns=none\n[device-proof]\nmatch-device=*\nmanaged=0\n")
bus = subprocess.Popen(["dbus-daemon", "--system", "--nofork"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
log = pathlib.Path("/tmp/installer-networkmanager.log").open("w")
daemon = None


def start_daemon():
    process = subprocess.Popen(["NetworkManager", "--no-daemon"], stdout=log, stderr=log)
    for _ in range(50):
        try:
            nmcli(["-g", "VERSION", "general"])
            return process
        except subprocess.CalledProcessError:
            time.sleep(0.1)
    raise RuntimeError("private daemon did not start")


try:
    daemon = start_daemon()
    print(nmcli(["--version"]))
    print("Python " + sys.version.split()[0])
    descriptor = lock["acquire_lock"]()
    if descriptor != 9:
        os.dup2(descriptor, 9, inheritable=True)
        os.close(descriptor)
    anchor = pathlib.Path("/var/lib/jittersim-updates/lock")
    inode = anchor.stat().st_ino
    spoof_code = ("import os,runpy; m=runpy.run_path(" + repr(str(LOCK)) + "); "
                  "d=os.open('/var/lib/jittersim-updates/lock',os.O_RDWR);os.dup2(d,9); "
                  "m['verify_lock']()")
    spoof = subprocess.run([sys.executable, "-I", "-c", spoof_code], capture_output=True, timeout=5)
    assert spoof.returncode != 0
    prefix = pathlib.Path("/proof/source/deploy/raspberry-pi/install.sh").read_text()
    prefix = prefix[:prefix.index("\nif ! getent")]
    installer = pathlib.Path("/tmp/locked-installer.sh")
    installer.write_text(prefix + "\nprintf reached > /tmp/installer-write-marker\n")
    busy = subprocess.run(["sh", str(installer), "/proof/source"], capture_output=True, timeout=5)
    assert busy.returncode != 0 and not pathlib.Path("/tmp/installer-write-marker").exists()
    print("PASS spoofed FD9 and real early installer busy state refuse before mutation")
    nmcli(["connection", "add", "type", "ethernet", "con-name", "unrelated-fixture",
           "ifname", "unused-proof", "autoconnect", "no"])
    unrelated = nmcli(["-g", "connection.uuid", "connection", "show", "id", "unrelated-fixture"])
    passwords = (b"first-fixture-password", b' leading " # \\ $ ` ; trailing ', b"x" * 63)
    for password in passwords:
        result = invoke("create", password)
        assert result.returncode == 0, "production create failed (output withheld)"
        identifier = nmcli(["-g", "connection.uuid", "connection", "show", "id", "jittersim-ap"])
        assert nmcli(["--escape", "no", "--show-secrets", "-g", "802-11-wireless-security.psk",
                      "connection", "show", "uuid", identifier]).encode() == password
        assert nmcli(["-g", "connection.stable-id", "connection", "show", "uuid", identifier]) == ""
        profiles = list(pathlib.Path("/etc/NetworkManager/system-connections").glob("*"))
        owned = [path for path in profiles if ("uuid=" + identifier).encode() in path.read_bytes()]
        assert len(owned) == 1
        info = owned[0].stat()
        assert info.st_uid == 0 and info.st_gid == 0 and info.st_mode & 0o777 == 0o600
        daemon.terminate()
        daemon.wait(timeout=5)
        daemon = start_daemon()
        assert nmcli(["--escape", "no", "--show-secrets", "-g", "802-11-wireless-security.psk",
                      "connection", "show", "uuid", identifier]).encode() == password
        assert invoke("probe", password).stdout == b"present\n"
        assert nmcli(["-g", "connection.uuid", "connection", "show", "id", "jittersim-ap"]) == identifier
        nmcli(["connection", "delete", "uuid", identifier])
    print("PASS standalone production creates literal persisted PSKs and preserves existing profiles")
    failed = invoke("create", PROOF_FAIL_SAVE="1")
    assert failed.returncode != 0
    assert invoke("probe").stdout == b"absent\n"
    assert nmcli(["-g", "connection.uuid", "connection", "show", "id", "unrelated-fixture"]) == unrelated
    print("PASS real persistent-save failure deletes only newly owned UUID")
    cleanup_failed = invoke("create", PROOF_FAIL_SAVE="1", PROOF_FAIL_CLEANUP="1")
    assert cleanup_failed.returncode != 0
    assert b"fixture-password" not in cleanup_failed.stdout + cleanup_failed.stderr
    identifier = nmcli(["-g", "connection.uuid", "connection", "show", "id", "jittersim-ap"])
    assert nmcli(["--escape", "no", "-g", "connection.stable-id", "connection", "show", "uuid", identifier]) == "jittersim-initial-ap-pending:" + identifier
    assert nmcli(["-g", "connection.autoconnect,802-11-wireless.mode,802-11-wireless-security.key-mgmt",
                  "connection", "show", "uuid", identifier]) == "no\nap\nsae"
    assert invoke("probe").returncode != 0
    assert nmcli(["-g", "connection.uuid", "connection", "show", "id", "unrelated-fixture"]) == unrelated
    nmcli(["connection", "delete", "uuid", identifier])
    print("PASS failed cleanup retains only secured inactive marked UUID and generic failure")
    interrupted = invoke("create", PROOF_CRASH_AFTER_ADD="1")
    assert interrupted.returncode == -signal.SIGKILL
    identifier = nmcli(["-g", "connection.uuid", "connection", "show", "id", "jittersim-ap"])
    marker = nmcli(["--escape", "no", "-g", "connection.stable-id", "connection", "show", "uuid", identifier])
    assert marker == "jittersim-initial-ap-pending:" + identifier
    assert nmcli(["-g", "connection.autoconnect,802-11-wireless.mode,802-11-wireless-security.key-mgmt",
                  "connection", "show", "uuid", identifier]) == "no\nap\nsae"
    before = len(records.read_text().splitlines())
    assert invoke("probe").returncode != 0
    assert invoke("create").returncode != 0
    later = [json.loads(line) for line in records.read_text().splitlines()[before:]]
    assert all(record["operation"] == "show" for record in later)
    daemon.terminate()
    daemon.wait(timeout=5)
    daemon = start_daemon()
    assert invoke("probe").returncode != 0
    print("PASS SIGKILL leaves durable secured receipt and retry refuses without writes")
    nmcli(["connection", "delete", "uuid", identifier])
    all_records = [json.loads(line) for line in records.read_text().splitlines()]
    assert all(not any(record[key] for key in ("secret_argument", "secret_environment",
                   "password_variable", "inherited_lock")) for record in all_records)
    assert all(record["operation"] not in ("up", "down") for record in all_records)
    assert not pathlib.Path("/root/.nmcli-history").exists()
    assert not pathlib.Path("/root/.cache/nmcli-history").exists()
    log.flush()
    daemon_text = pathlib.Path("/tmp/installer-networkmanager.log").read_bytes()
    assert all(password not in daemon_text for password in passwords)
    print("PASS no PSK argv/environment/history/log exposure and no FD9 passed to nmcli")
    child = subprocess.Popen([sys.executable, "-I", "-c",
                              "import os,sys;os.execv(sys.executable,[sys.executable,'-I','-c',"
                              "'import sys;print(\"ready\",flush=True);sys.stdin.buffer.read()'])"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, pass_fds=(9,))
    assert child.stdout.readline() == b"ready\n"
    os.close(9)
    try:
        lock["acquire_lock"]()
    except BlockingIOError:
        pass
    else:
        raise AssertionError("exec child lost the inherited lock")
    child.communicate(timeout=5)
    descriptor = lock["acquire_lock"]()
    os.close(descriptor)
    assert anchor.stat().st_ino == inode
    ready = subprocess.run(["sh", str(installer), "/proof/source"], capture_output=True, timeout=5)
    assert ready.returncode == 0 and pathlib.Path("/tmp/installer-write-marker").exists()
    print("PASS Linux lock survives exec until child completes and stable anchor remains")
finally:
    if daemon is not None:
        daemon.terminate()
        daemon.wait(timeout=5)
    bus.terminate()
    bus.wait(timeout=5)
    log.close()

#!/usr/bin/env python3
"""Prepare exact packages and publish only an approved stable release."""

import argparse
import contextlib
import fcntl
import hashlib
import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import selectors
import subprocess
import sys
import tarfile
import tempfile
import time

REPOSITORY = "smartinezai/jittersim"
ASSET = "jittersim-release.tar.gz"
POLICY = "deploy/raspberry-pi/release-policy.json"
LOCKS = "deploy/raspberry-pi/release-locks"
REGISTRY = "docs/releases/registry.json"
MAX_JSON = 2 * 1024 * 1024
MAX_ARCHIVE = 25 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
GATES = {"physical", "upgrade", "recovery", "saved_settings", "management_access", "safety_state"}


class ReleaseError(Exception):
    pass


class NotReady(ReleaseError):
    pass


def checked(condition, message):
    if not condition:
        raise ReleaseError(message)


def version(value):
    checked(isinstance(value, str) and VERSION.fullmatch(value), "Use a MAJOR.MINOR.PATCH version.")
    return tuple(map(int, value.split(".")))


def read_bytes(path, limit):
    before = Path(path).lstat()
    checked(stat.S_ISREG(before.st_mode), "A release input is not a regular file.")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        checked(stat.S_ISREG(opened.st_mode) and (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                "A release input changed its file identity.")
        checked(opened.st_size <= limit, "A release input exceeds its size limit.")
        value = stream.read(min(limit + 1, opened.st_size + 1))
        after = os.fstat(stream.fileno())
        checked((opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "A release input changed while being read.")
    checked(len(value) <= limit, "A release input exceeds its size limit.")
    return value


def digest(path, limit=MAX_ARCHIVE):
    return hashlib.sha256(read_bytes(path, limit)).hexdigest()


def loads(raw):
    checked(len(raw) <= MAX_JSON, "The release record is too large.")
    try:
        value = json.loads(raw)
        checked(isinstance(value, (dict, list)), "The release record has an invalid shape.")
        return value
    except (ValueError, UnicodeError):
        raise ReleaseError("The release record is not valid JSON.") from None


def read_json(path):
    return loads(read_bytes(path, MAX_JSON))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    checked(len(raw) <= MAX_JSON, "The release record is too large.")
    fd, temporary = tempfile.mkstemp(prefix=".release-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# The guardian stays waitable until the controller confirms its group has ended.
# Command descendants cannot keep or forge the controller's status/lease pipes.
GUARDIAN = """
import os, signal, subprocess, sys
status, lease = int(sys.argv[1]), int(sys.argv[2])
try:
    result = subprocess.Popen(sys.argv[3:], close_fds=True).wait()
    os.write(status, (str(result) + '\\n').encode())
    os.close(status)
    if not os.read(lease, 1):
        os.killpg(os.getpgrp(), signal.SIGKILL)
except BaseException:
    os.killpg(os.getpgrp(), signal.SIGKILL)
"""
UNCONFIRMED_COMMANDS = []


def group_running(group, deadline, *, exclude_leader=False):
    """Inspect live group members while its guardian PID is still owned."""
    if Path("/proc").is_dir():
        rows = []
        with os.scandir("/proc") as entries:
            count = 0
            for entry in entries:
                checked(time.monotonic() < deadline, "Command group inspection exceeded its deadline.")
                if not entry.name.isdigit():
                    continue
                count += 1
                checked(count <= 65536, "The process table exceeds the inspection limit.")
                try:
                    with open(Path(entry.path) / "stat", "rb") as stream:
                        fields = stream.read(4096).rsplit(b")", 1)[1].split()
                    rows.append((int(entry.name), int(fields[2]), fields[0].decode("ascii")))
                except FileNotFoundError:
                    continue
                except (ValueError, IndexError, UnicodeError):
                    raise ReleaseError("Command group completion could not be verified.") from None
    else:
        # ps is a fixed native inspection command. Its file output stays bounded.
        with tempfile.TemporaryFile() as output:
            probe = subprocess.Popen(["/bin/ps", "-axo", "pid=,pgid=,stat="], stdout=output,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                while probe.poll() is None:
                    checked(time.monotonic() < deadline and os.fstat(output.fileno()).st_size <= MAX_JSON,
                            "Command group inspection exceeded its time or output limit.")
                    time.sleep(0.005)
                checked(probe.returncode == 0 and os.fstat(output.fileno()).st_size <= MAX_JSON,
                        "Command group completion could not be verified.")
                output.seek(0)
                lines = output.read(MAX_JSON + 1).splitlines()
                checked(len(lines) <= 65536, "The process table exceeds the inspection limit.")
                rows = []
                for line in lines:
                    fields = line.split()
                    checked(len(fields) == 3, "Command group completion could not be verified.")
                    rows.append((int(fields[0]), int(fields[1]), fields[2].decode("ascii")))
            finally:
                if probe.returncode is None:
                    os.killpg(probe.pid, signal.SIGKILL)
                    probe.wait(timeout=1)
    return any(pgid == group and not state.startswith(("Z", "X"))
               and not (exclude_leader and pid == group) for pid, pgid, state in rows)


def command(arguments, *, cwd=None, input_bytes=None, limit=MAX_JSON, timeout=60, env=None):
    """Bound output and retain group ownership through completion or cancellation."""
    checked(type(limit) is int and 0 < limit <= MAX_ARCHIVE, "Invalid command output allowance.")
    checked(isinstance(timeout, (int, float)) and 0 < timeout <= 180,
            "Invalid command deadline.")
    checked(input_bytes is None or isinstance(input_bytes, bytes) and len(input_bytes) <= MAX_ARCHIVE,
            "Command input exceeds its allowance.")
    with tempfile.TemporaryFile() as source:
        if input_bytes is not None:
            source.write(input_bytes)
            source.seek(0)
        status_read, status_write = os.pipe()
        lease_read, lease_write = os.pipe()
        try:
            process = subprocess.Popen([sys.executable, "-I", "-c", GUARDIAN,
                                        str(status_write), str(lease_read), *arguments],
                                       cwd=cwd, env=env, stdin=source, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, pass_fds=(status_write, lease_read),
                                       start_new_session=True, bufsize=0)
        except BaseException:
            for descriptor in (status_read, status_write, lease_read, lease_write):
                os.close(descriptor)
            raise
        os.close(status_write)
        os.close(lease_read)
        deadline = time.monotonic() + timeout
        work_deadline = deadline - min(1, timeout / 3)
        answer, status, total = bytearray(), bytearray(), 0
        result, released = None, False
        confirmed = False
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                selector.register(status_read, selectors.EVENT_READ, "status")
                while selector.get_map():
                    remaining = work_deadline - time.monotonic()
                    checked(remaining > 0, "A release command exceeded its time limit.")
                    for key, _ in selector.select(timeout=min(remaining, 0.02)):
                        block = os.read(key.fd, 65536)
                        if not block:
                            selector.unregister(key.fileobj)
                            if key.data == "status":
                                checked(status.endswith(b"\n") and len(status) <= 32,
                                        "The command result could not be verified.")
                                result = int(status)
                            continue
                        if key.data == "status":
                            status.extend(block)
                            checked(len(status) <= 32, "The command result exceeds its allowance.")
                        else:
                            total += len(block)
                            checked(total <= limit, "A release command exceeded its output limit.")
                            if key.data == "stdout":
                                answer.extend(block)
                    if result is not None:
                        checked(result == 0, "A release command failed. No stable release was approved.")
                        if not released and not group_running(process.pid, work_deadline, exclude_leader=True):
                            os.write(lease_write, b"1")
                            released = True
            checked(released and result == 0, "The complete command result could not be verified.")
            while group_running(process.pid, deadline):
                checked(time.monotonic() < deadline, "A release command exceeded its time limit.")
                time.sleep(0.005)
            confirmed = True
            process.wait(timeout=max(0, deadline - time.monotonic()))
            checked(process.returncode == 0, "The command guardian failed.")
            return bytes(answer)
        finally:
            try:
                if not confirmed:
                    # The guardian has not been reaped so its group number cannot be reused.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        while group_running(process.pid, deadline):
                            checked(time.monotonic() < deadline, "The command group did not stop.")
                            time.sleep(0.005)
                        process.wait(timeout=max(0, deadline - time.monotonic()))
                    except (ReleaseError, OSError, subprocess.TimeoutExpired):
                        UNCONFIRMED_COMMANDS.append(process)
                        raise ReleaseError("Command group termination is unconfirmed. Inspect before retrying.") from None
            finally:
                for descriptor in (status_read, lease_write):
                    os.close(descriptor)
                process.stdout.close()
                process.stderr.close()

class Git:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def call(self, *arguments):
        return command(["git", *arguments], cwd=self.root).decode().strip()

    def commit(self, ref):
        checked(isinstance(ref, str) and not ref.startswith("-"), "Invalid source reference.")
        value = self.call("rev-parse", "--verify", ref + "^{commit}")
        checked(SHA.fullmatch(value), "Invalid source commit.")
        return value

    def on_main(self, commit):
        answer = GitHub().api(f"repos/{REPOSITORY}/compare/{commit}...main?per_page=1")
        checked(answer.get("status") in {"ahead", "identical"}
                and answer.get("merge_base_commit", {}).get("sha") == commit,
                "The selected source is not in current main history.")

    def source(self, commit, name):
        checked(SHA.fullmatch(commit), "Invalid source commit.")
        return command(["git", "show", commit + ":" + name], cwd=self.root)


OFFICIAL_PUSH_URLS = {prefix + REPOSITORY + suffix
                      for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/")
                      for suffix in ("", ".git")}


def verified_push_destination(git):
    # get-url expands both insteadOf and pushInsteadOf and lists every push URL.
    destinations = git.call("remote", "get-url", "--push", "--all", "origin").splitlines()
    checked(len(destinations) == 1 and destinations[0] in OFFICIAL_PUSH_URLS,
            "The effective push destination is not the single official repository.")
    return destinations[0]


class GitHub:
    def call(self, *arguments):
        return command(["gh", *arguments])

    def api(self, path):
        checked(path.startswith("repos/" + REPOSITORY + "/") or path == "user", "Unexpected GitHub request.")
        return loads(self.call("api", path))


def policy(git, commit):
    result = loads(git.source(commit, POLICY))
    checked(result.get("format") == 1 and result.get("repository") == REPOSITORY,
            "The source has no accepted release policy.")
    workflows = result.get("workflows")
    checked(isinstance(workflows, dict) and 1 <= len(workflows) <= 8,
            "The required workflow list is invalid.")
    for path, jobs in workflows.items():
        checked(path.startswith(".github/workflows/") and path.endswith(".yml")
                and isinstance(jobs, list) and 1 <= len(jobs) <= 32
                and all(isinstance(job, str) and 0 < len(job) <= 100 for job in jobs)
                and len(set(jobs)) == len(jobs), "The required job list is invalid.")
    return result


def verify_ci(git, commit, github):
    expected = policy(git, commit)["workflows"]
    answer = github.api(f"repos/{REPOSITORY}/actions/runs?head_sha={commit}&per_page=100")
    runs = answer.get("workflow_runs", [])
    checked(isinstance(runs, list) and len(runs) <= 100 and all(isinstance(run, dict) for run in runs),
            "Invalid workflow response.")
    receipts = []
    for path, job_names in expected.items():
        matches = [run for run in runs if run.get("path", "").split("@")[0] == path
                   and run.get("head_sha") == commit and run.get("event") == "push"
                   and run.get("head_branch") == "main"
                   and run.get("repository", {}).get("full_name") == REPOSITORY
                   and run.get("head_repository", {}).get("full_name") == REPOSITORY]
        if not matches:
            raise NotReady("Required main checks have not completed for this commit.")
        run = max(matches, key=lambda row: (row["id"], row.get("run_attempt", 1)))
        checked(type(run.get("id")) is int and 0 < run["id"] < 2 ** 53
                and type(run.get("run_attempt", 1)) is int and 0 < run.get("run_attempt", 1) <= 1000,
                "The workflow attempt is invalid.")
        if run.get("status") != "completed":
            raise NotReady("Required main checks are still running.")
        checked(run.get("conclusion") == "success", "A required main workflow failed.")
        jobs = github.api(f"repos/{REPOSITORY}/actions/runs/{run['id']}/attempts/{run.get('run_attempt', 1)}/jobs?per_page=100")
        rows = jobs.get("jobs", [])
        checked(isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
                and jobs.get("total_count") == len(rows) and len(rows) <= 100,
                "The complete job list could not be verified.")
        checked(len(rows) == len(job_names) and {row.get("name") for row in rows} == set(job_names),
                "Required jobs are missing or the workflow inventory changed.")
        checked(all(row.get("conclusion") == "success" and row.get("steps")
                    and all(step.get("conclusion") == "success" for step in row["steps"])
                    for row in rows), "A required job or step did not pass.")
        receipts.append({"path": path, "run_id": run["id"], "attempt": run.get("run_attempt", 1),
                         "commit": commit, "jobs": sorted(job_names)})
    return receipts


@contextlib.contextmanager
def registry_lock(git):
    path = git.root / REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / ".registry.lock", "a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReleaseError("Another release operation is running.") from None
        yield path


def registry(path):
    value = read_json(path)
    checked(value.get("format") == 1 and set(value) == {"format", "sequence_floor", "reservations"},
            "Invalid release registry.")
    floor = value["sequence_floor"]
    checked(floor is None or type(floor) is int and 0 <= floor < 2 ** 53, "Invalid release-order floor.")
    rows = value["reservations"]
    checked(isinstance(rows, list) and len(rows) <= 10000, "Invalid release reservations.")
    names, orders = set(), set()
    for row in rows:
        checked(isinstance(row, dict), "Invalid release reservation.")
        version(row.get("version"))
        checked(type(row.get("sequence")) is int and 0 < row["sequence"] < 2 ** 53
                and floor is not None and row["sequence"] > floor
                and row["version"] not in names and row["sequence"] not in orders
                and row.get("state") in {"reserved", "retired", "published"}, "Invalid release reservation.")
        names.add(row["version"])
        orders.add(row["sequence"])
    return value


def initialize(git, floor):
    checked(type(floor) is int and 0 <= floor < 2 ** 53 - 1, "Invalid inspected release-order floor.")
    with registry_lock(git) as path:
        value = registry(path)
        checked(value["sequence_floor"] is None and not value["reservations"], "The registry is already initialized.")
        value["sequence_floor"] = floor
        write_json(path, value)


def reserve(git, number):
    version(number)
    with registry_lock(git) as path:
        value = registry(path)
        checked(value["sequence_floor"] is not None, "Inspect supported installations and initialize their release-order floor first.")
        checked(len(value["reservations"]) < 10000, "The release registry needs archival before another reservation.")
        checked(all(version(number) > version(row["version"]) for row in value["reservations"]),
                "The version has been used or is not newer than all reserved versions.")
        sequence = max([value["sequence_floor"], *[row["sequence"] for row in value["reservations"]]]) + 1
        checked(sequence < 2 ** 53, "Release order is exhausted.")
        row = {"version": number, "sequence": sequence, "state": "reserved"}
        value["reservations"].append(row)
        write_json(path, value)
        return row


def retire(git, number):
    version(number)
    with registry_lock(git) as path:
        value = registry(path)
        matches = [row for row in value["reservations"] if row["version"] == number]
        checked(len(matches) == 1 and matches[0]["state"] in {"reserved", "retired"},
                "Only an unpublished reservation can be retired.")
        matches[0]["state"] = "retired"
        write_json(path, value)


MAX_EXPANDED = 256 * 1024 * 1024
MAX_EXTENSION = 16 * 1024
MAX_EXTENSION_TOTAL = 64 * 1024
PAX_FIELDS = {b"path", b"linkpath", b"mtime", b"atime", b"ctime", b"uid", b"gid",
              b"uname", b"gname", b"comment", b"hdrcharset"}


def check_pax(payload):
    offset, count = 0, 0
    while offset < len(payload):
        count += 1
        checked(count <= 128, "Too many archive extension fields.")
        end = payload.find(b" ", offset, offset + 8)
        checked(end > offset and payload[offset:end].isdigit(), "Invalid archive extension field.")
        length = int(payload[offset:end])
        checked(length > end - offset + 3 and offset + length <= len(payload),
                "Invalid archive extension field.")
        record = payload[end + 1:offset + length]
        key, separator, value = record[:-1].partition(b"=")
        checked(record.endswith(b"\n") and separator and key in PAX_FIELDS and len(value) <= 4096,
                "Unsupported or oversized archive extension field.")
        offset += length


@contextlib.contextmanager
def bounded_tar(compressed):
    """Scan physical headers before tarfile can read or apply extension metadata."""
    checked(isinstance(compressed, bytes) and len(compressed) <= MAX_ARCHIVE,
            "The archive exceeds the Pi download limit.")
    deadline = time.monotonic() + 30
    total, headers, extension_total, chain = 0, 0, 0, 0
    with tempfile.TemporaryFile() as plain:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
                def read(size):
                    nonlocal total
                    checked(time.monotonic() < deadline, "Archive inspection exceeded its deadline.")
                    block = stream.read(size)
                    total += len(block)
                    checked(total <= MAX_EXPANDED, "The raw archive exceeds its expanded limit.")
                    plain.write(block)
                    return block

                while True:
                    header = read(512)
                    checked(len(header) == 512, "The archive has a truncated header.")
                    if header == b"\0" * 512:
                        checked(chain == 0 and read(512) == b"\0" * 512, "The archive has an invalid ending.")
                        while True:
                            padding = read(65536)
                            if not padding:
                                break
                            checked(not padding.strip(b"\0"), "The archive has unexpected trailing data.")
                        break
                    headers += 1
                    checked(headers <= 5000, "The raw archive has too many headers.")
                    member = tarfile.TarInfo.frombuf(header, encoding="utf-8", errors="strict")
                    checked(0 <= member.size <= MAX_EXPANDED - total,
                            "The raw archive entry exceeds its expanded limit.")
                    extended = member.type in {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME}
                    checked(extended or member.type in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE},
                            "The archive contains unsupported physical entries.")
                    if extended:
                        chain += 1
                        extension_total += member.size
                        checked(chain <= 2 and member.size <= MAX_EXTENSION
                                and extension_total <= MAX_EXTENSION_TOTAL,
                                "The archive extension exceeds its limit.")
                        payload = read(member.size)
                        checked(len(payload) == member.size, "The archive extension is truncated.")
                        if member.type in {tarfile.XHDTYPE, tarfile.XGLTYPE}:
                            check_pax(payload)
                        else:
                            checked(member.size <= 4096, "The archive name exceeds its limit.")
                    else:
                        chain = 0
                        checked(member.type != tarfile.DIRTYPE or member.size == 0,
                                "An archive directory has unexpected content.")
                        remaining = member.size
                        while remaining:
                            block = read(min(65536, remaining))
                            checked(block, "The archive entry is truncated.")
                            remaining -= len(block)
                    padding = (-member.size) % 512
                    checked(len(read(padding)) == padding, "The archive padding is truncated.")
            plain.seek(0)
            yield plain
        except (gzip.BadGzipFile, EOFError, tarfile.HeaderError, UnicodeError):
            raise ReleaseError("The release archive is invalid or exceeds its limits.") from None

def archive_record(path, *, archive_bytes=None):
    compressed = read_bytes(path, MAX_ARCHIVE) if archive_bytes is None else archive_bytes
    selected, names, total = {}, set(), 0
    with bounded_tar(compressed) as plain, tarfile.open(fileobj=plain, mode="r|") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            total += member.size
            checked(member.isfile() or member.isdir(), "The archive contains an unsafe entry.")
            checked(not name.is_absolute() and ".." not in name.parts and str(name) not in names
                    and name.parts and name.parts[0] in {"src", "deploy", "pyproject.toml", "README.md"}
                    and len(names) < 5000 and total <= 256 * 1024 * 1024,
                    "The archive contains unexpected entries or exceeds its limits.")
            names.add(str(name))
            if str(name) in {"deploy/RELEASE", POLICY, LOCKS + "/runtime.txt", LOCKS + "/runtime-py39.txt", LOCKS + "/build.txt",
                             "pyproject.toml", "src/jittersim/__init__.py"}:
                checked(member.isfile() and member.size <= 128 * 1024, "Invalid release metadata.")
                selected[str(name)] = archive.extractfile(member).read(128 * 1024 + 1)
    checked("deploy/RELEASE" in selected and POLICY in selected
            and all(LOCKS + "/" + lock in selected for lock in ("runtime.txt", "runtime-py39.txt", "build.txt")),
            "The archive is missing release policy or dependency locks.")
    manifest = loads(selected["deploy/RELEASE"])
    checked(set(manifest) == {"format", "commit", "version", "sequence"} and manifest["format"] == 1
            and isinstance(manifest["commit"], str) and SHA.fullmatch(manifest["commit"])
            and type(manifest["sequence"]) is int and 0 < manifest["sequence"] < 2 ** 53,
            "Invalid signed manifest.")
    number = manifest["version"]
    version(number)
    for name, pattern in (("pyproject.toml", r'^version = "([^"]+)"$'),
                          ("src/jittersim/__init__.py", r'^__version__ = "([^"]+)"$')):
        found = re.search(pattern, selected.get(name, b"").decode(), re.MULTILINE)
        checked(found and found.group(1) == number, "Package versions disagree with the signed manifest.")
    manifest["locks_sha256"] = hashlib.sha256(b"\0".join(selected[LOCKS + "/" + name]
                                               for name in ("runtime.txt", "runtime-py39.txt", "build.txt"))).hexdigest()
    return manifest


def verify_signature(path, signature, trusted_key, namespace="jittersim-release", *, payload=None, signature_bytes=None):
    key = read_bytes(trusted_key, 4096).decode().strip().split()
    checked(len(key) >= 2 and key[0] == "ssh-ed25519", "Use a trusted Ed25519 public key.")
    raw = read_bytes(path, MAX_ARCHIVE) if payload is None else payload
    signature_raw = read_bytes(signature, 16384) if signature_bytes is None else signature_bytes
    checked(isinstance(raw, bytes) and len(raw) <= MAX_ARCHIVE
            and isinstance(signature_raw, bytes) and len(signature_raw) <= 16384, "Signature input exceeds its allowance.")
    with tempfile.TemporaryDirectory(prefix="jittersim-verify-") as folder:
        allowed = Path(folder) / "signers"
        allowed.write_text(f'release namespaces="{namespace}" {key[0]} {key[1]}\n')
        retained_signature = Path(folder) / "signature"
        retained_signature.write_bytes(signature_raw)
        command(["ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I", "release", "-n", namespace,
                 "-s", str(retained_signature)], input_bytes=raw)


def candidate(git, ref, output, github):
    commit = git.commit(ref)
    git.on_main(commit)
    receipts = verify_ci(git, commit, github)
    output = Path(output)
    checked(not output.exists(), "Candidate output already exists.")
    output.mkdir(mode=0o700)
    archive = output / "development.tar.gz"
    package = re.search(r'^version = "([^"]+)"$', git.source(commit, "pyproject.toml").decode(), re.MULTILINE)
    checked(package, "The candidate package has no version.")
    version(package.group(1))
    label = package.group(1) + ".dev+" + commit[:7]
    manifest = json.dumps({"format": 1, "commit": commit, "version": label, "sequence": 1})
    command(["git", "archive", "--format=tar.gz", "--add-virtual-file=deploy/RELEASE:" + manifest,
             "--output=" + str(archive.resolve()), commit, "pyproject.toml", "README.md", "src", "deploy"], cwd=git.root)
    write_json(output / "candidate.json", {"format": 1, "channel": "development", "commit": commit,
                                          "version": label, "archive_sha256": digest(archive), "checks": receipts})


def prepare(git, ref, number, output, key, github):
    version(number)
    commit = git.commit(ref)
    git.on_main(commit)
    receipts = verify_ci(git, commit, github)
    checked(not git.call("status", "--porcelain", "--untracked-files=no"), "Commit release preparation changes before building.")
    with registry_lock(git) as path:
        value = registry(path)
        rows = [row for row in value["reservations"] if row["version"] == number and row["state"] == "reserved"]
        checked(len(rows) == 1, "Reserve this release version first.")
        row = rows[0]
        checked(loads(git.source(commit, REGISTRY)) == value, "The selected commit does not contain the current reservations.")
        tag = "v" + number
        existing = git.call("tag", "--list", tag)
        checked(not existing or git.commit(tag) == commit, "The version tag names another commit.")
        if not existing:
            git.call("tag", "-a", tag, commit, "-m", "JitterSim " + number)
        output = Path(output).resolve()
        checked(not output.exists(), "Retain the existing candidate. Do not rebuild it.")
        output.mkdir(mode=0o700)
        archive = output / ASSET
        environment = {name: value for name, value in os.environ.items()
                       if name not in {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"}}
        environment["JITTERSIM_RELEASE_SEQUENCE"] = str(row["sequence"])
        # Build in an isolated committed checkout so its scripts match the selected source.
        with tempfile.TemporaryDirectory(prefix="jittersim-build-") as folder:
            checkout = Path(folder) / "source"
            command(["git", "clone", "--no-hardlinks", "--quiet", str(git.root), str(checkout)])
            command(["git", "checkout", "--quiet", "--detach", commit], cwd=checkout)
            command(["sh", "deploy/raspberry-pi/build-release.sh", tag, str(archive), str(key)],
                    cwd=checkout, env=environment, timeout=180)
        archive_raw = read_bytes(archive, MAX_ARCHIVE)
        verify_signature(archive, str(archive) + ".sig", str(key) + ".pub", payload=archive_raw)
        record = archive_record(archive, archive_bytes=archive_raw)
        checked(record["commit"] == commit and record["version"] == number and record["sequence"] == row["sequence"],
                "The built archive differs from its reservation.")
        record.update(format=1, archive_sha256=digest(archive), signature_sha256=digest(str(archive) + ".sig", 16384), checks=receipts)
        write_json(output / "release.json", record)
        evidence = {"format": 1, "commit": commit, "archive_sha256": record["archive_sha256"],
                    "locks_sha256": record["locks_sha256"], "targets": [], "release_notes": "",
                    "gates": {name: {"passed": False, "evidence": ""} for name in sorted(GATES)}}
        write_json(output / "acceptance.json", evidence)
        return record


def bundle(folder, trusted_key):
    folder = Path(folder)
    record = read_json(folder / "release.json")
    raw = read_bytes(folder / ASSET, MAX_ARCHIVE)
    signature = read_bytes(folder / (ASSET + ".sig"), 16384)
    checked(record.get("archive_sha256") == hashlib.sha256(raw).hexdigest()
            and record.get("signature_sha256") == hashlib.sha256(signature).hexdigest(),
            "The retained package changed.")
    verify_signature(folder / ASSET, folder / (ASSET + ".sig"), trusted_key,
                     payload=raw, signature_bytes=signature)
    manifest = archive_record(folder / ASSET, archive_bytes=raw)
    checked(all(record.get(name) == value for name, value in manifest.items()), "The release record differs from the archive.")
    return record


def acceptance(value, record):
    checked(isinstance(value, dict) and value.get("format") == 1 and all(value.get(name) == record.get(name)
            for name in ("commit", "archive_sha256", "locks_sha256")), "Acceptance belongs to another package.")
    release_notes = value.get("release_notes")
    checked(isinstance(release_notes, str) and 1 <= len(release_notes.strip()) <= 16000,
            "Record release changes, supported systems and upgrade instructions before approval.")
    targets = value.get("targets")
    checked(isinstance(targets, list) and 1 <= len(targets) <= 16
            and all(isinstance(target, str) and 0 < len(target) <= 200 for target in targets),
            "Record concrete tested targets before approval.")
    gates = value.get("gates")
    checked(isinstance(gates, dict) and set(gates) == GATES, "Acceptance gates are missing.")
    for gate in gates.values():
        checked(isinstance(gate, dict) and gate.get("passed") is True
                and isinstance(gate.get("evidence"), str) and 0 < len(gate["evidence"]) <= 2000,
                "A required physical acceptance or recovery gate has not passed.")


def approve(git, folder, evidence, key, confirm, github):
    checked(not (Path(folder) / "approval.json").exists(), "Approval already exists. Retain the original signed record.")
    record = bundle(folder, str(key) + ".pub")
    checked(confirm == record["version"], "Confirm the exact version being approved.")
    git.on_main(record["commit"])
    receipts = verify_ci(git, record["commit"], github)
    value = read_json(evidence)
    acceptance(value, record)
    who = github.api("user").get("login")
    checked(who == REPOSITORY.split("/")[0], "The approving GitHub account must be the release owner.")
    approval = {"format": 1, "release": record, "acceptance": value, "checks": receipts,
                "approved_by": who, "approved_at": int(time.time())}
    path = Path(folder) / "approval.json"
    write_json(path, approval)
    command(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "jittersim-stable-approval", str(path)])
    verify_signature(path, str(path) + ".sig", str(key) + ".pub", "jittersim-stable-approval")
    return approval


def approved(git, folder, trusted_key, github):
    record = bundle(folder, trusted_key)
    path = Path(folder) / "approval.json"
    raw = read_bytes(path, MAX_JSON)
    signature = read_bytes(str(path) + ".sig", 16384)
    verify_signature(path, str(path) + ".sig", trusted_key, "jittersim-stable-approval",
                     payload=raw, signature_bytes=signature)
    approval = loads(raw)
    checked(approval.get("format") == 1 and approval.get("release") == record
            and approval.get("approved_by") == REPOSITORY.split("/")[0],
            "Approval does not name this exact release.")
    acceptance(approval.get("acceptance", {}), record)
    git.on_main(record["commit"])
    verify_ci(git, record["commit"], github)
    return record, approval


def remote_tag(github, tag):
    row = github.api(f"repos/{REPOSITORY}/git/ref/tags/{tag}")
    obj = row.get("object", {})
    if obj.get("type") == "tag":
        checked(SHA.fullmatch(obj.get("sha", "")), "The remote annotated tag is invalid.")
        obj = github.api(f"repos/{REPOSITORY}/git/tags/{obj['sha']}").get("object", {})
    checked(obj.get("type") == "commit" and SHA.fullmatch(obj.get("sha", "")), "The remote tag target is invalid.")
    return obj["sha"]


def notes(record, approval):
    return (f"JitterSim {record['version']}\n\n"
            + approval["acceptance"]["release_notes"] + "\n\n" +
            f"Source: {record['commit']}\nRelease order: {record['sequence']}\n"
            f"Archive SHA-256: {record['archive_sha256']}\nSignature SHA-256: {record['signature_sha256']}\n"
            f"Dependency locks SHA-256: {record['locks_sha256']}\n\n"
            "Tested targets:\n" + "\n".join("- " + target for target in approval["acceptance"]["targets"])
            + f"\n\nStable approval: {approval['approved_by']}\n"
            "Installation requires owner approval. Use only the tested upgrade and recovery paths.\n")


def publish(git, folder, trusted_key, github, confirm, dry_run=False):
    folder = Path(folder).resolve()
    with registry_lock(git) as registry_path:
        # Snapshot files before checking them so later edits cannot change an upload.
        with tempfile.TemporaryDirectory(prefix="jittersim-publish-") as temporary:
            snapshot = Path(temporary)
            for name, limit in ((ASSET, MAX_ARCHIVE), (ASSET + ".sig", 16384), ("release.json", MAX_JSON),
                                ("approval.json", MAX_JSON), ("approval.json.sig", 16384)):
                (snapshot / name).write_bytes(read_bytes(folder / name, limit))
            record, approval = approved(git, snapshot, trusted_key, github)
            checked(confirm == record["version"], "Confirm the exact version being published.")
            value = registry(registry_path)
            reservations = [row for row in value["reservations"] if row["version"] == record["version"]]
            checked(len(reservations) == 1 and reservations[0]["sequence"] == record["sequence"]
                    and reservations[0]["state"] in {"reserved", "published"}, "The reservation is missing or retired.")
            tag = "v" + record["version"]
            checked(git.commit(tag) == record["commit"], "The local release tag changed.")
            verified_push_destination(git)
            releases = github.api(f"repos/{REPOSITORY}/releases?per_page=100")
            checked(isinstance(releases, list) and len(releases) < 100, "Review older release history before publishing.")
            same = [release for release in releases if release.get("tag_name") == tag]
            newer = [release for release in releases if not release.get("draft") and not release.get("prerelease")
                     and re.fullmatch(r"v\d+\.\d+\.\d+", release.get("tag_name", ""))
                     and version(release["tag_name"][1:]) > version(record["version"])]
            checked(not newer, "A newer stable release already exists. Do not replace latest.")
            checked(len(same) <= 1, "Conflicting releases name this tag.")
            if dry_run:
                return {"version": record["version"], "commit": record["commit"], "state": "verified-dry-run"}
            if not same:
                git.call("push", "--no-follow-tags", "--no-mirror", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
                checked(remote_tag(github, tag) == record["commit"], "The pushed release tag differs from the approved commit.")
                note_file = snapshot / "notes.md"
                note_file.write_text(notes(record, approval))
                github.call("release", "create", tag, "--repo", REPOSITORY, "--verify-tag", "--draft",
                            "--title", "JitterSim " + record["version"], "--notes-file", str(note_file))
                same = [github.api(f"repos/{REPOSITORY}/releases/tags/{tag}")]
            release = same[0]
            checked(remote_tag(github, tag) == record["commit"], "The remote tag changed.")
            assets = release.get("assets", [])
            checked(len(assets) <= 2 and len({asset.get("name") for asset in assets}) == len(assets)
                    and all(asset.get("name") in {ASSET, ASSET + ".sig"} for asset in assets),
                    "The release has unexpected assets. Refuse replacement.")
            if release.get("draft") is True:
                existing = {asset["name"] for asset in assets}
                for name in (ASSET, ASSET + ".sig"):
                    if name not in existing:
                        github.call("release", "upload", tag, str(snapshot / name), "--repo", REPOSITORY)
            else:
                checked(release.get("prerelease") is False, "A prerelease cannot be overwritten as stable.")
            downloaded = snapshot / "downloaded"
            downloaded.mkdir()
            github.call("release", "download", tag, "--repo", REPOSITORY, "--dir", str(downloaded),
                        "--pattern", ASSET, "--pattern", ASSET + ".sig")
            checked({item.name for item in downloaded.iterdir()} == {ASSET, ASSET + ".sig"}
                    and digest(downloaded / ASSET) == record["archive_sha256"]
                    and digest(downloaded / (ASSET + ".sig"), 16384) == record["signature_sha256"],
                    "GitHub assets differ from the approved package. Keep the draft unpublished.")
            verify_signature(downloaded / ASSET, downloaded / (ASSET + ".sig"), trusted_key)
            if release.get("draft") is True:
                github.call("release", "edit", tag, "--repo", REPOSITORY, "--draft=false", "--latest")
            latest = github.api(f"repos/{REPOSITORY}/releases/latest")
            checked(latest.get("tag_name") == tag and latest.get("draft") is False
                    and latest.get("prerelease") is False and len(latest.get("assets", [])) == 2
                    and {asset.get("name") for asset in latest["assets"]} == {ASSET, ASSET + ".sig"},
                    "Publication needs reconciliation. Inspect the actual release before retrying.")
            # Confirm bytes again after publication so a changed draft cannot be recorded as complete.
            final = snapshot / "published"
            final.mkdir()
            github.call("release", "download", tag, "--repo", REPOSITORY, "--dir", str(final),
                        "--pattern", ASSET, "--pattern", ASSET + ".sig")
            checked({item.name for item in final.iterdir()} == {ASSET, ASSET + ".sig"}
                    and digest(final / ASSET) == record["archive_sha256"]
                    and digest(final / (ASSET + ".sig"), 16384) == record["signature_sha256"],
                    "Published assets changed. Publication needs reconciliation.")
            reservations[0]["state"] = "published"
            write_json(registry_path, value)
            write_json(folder / "publication.json", {"format": 1, "tag": tag, "commit": record["commit"],
                                                     "archive_sha256": record["archive_sha256"], "release_id": latest["id"]})
            return latest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    sub = parser.add_subparsers(dest="operation", required=True)
    init = sub.add_parser("initialize", help="record the inspected highest release order")
    init.add_argument("--floor", type=int, required=True)
    init.add_argument("--confirm-inspected", action="store_true", required=True)
    reserved = sub.add_parser("reserve", help="reserve a unique version and increasing order")
    reserved.add_argument("version")
    retired = sub.add_parser("retire", help="keep a failed version and its order permanently used")
    retired.add_argument("version")
    for name in ("candidate", "prepare"):
        p = sub.add_parser(name)
        p.add_argument("--ref", required=True)
        p.add_argument("--output", type=Path, required=True)
        if name == "prepare":
            p.add_argument("--version", required=True)
            p.add_argument("--key", type=Path, required=True)
    p = sub.add_parser("approve", help="sign physical acceptance and exact package approval")
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--confirm-version", required=True)
    p = sub.add_parser("publish", help="publish a retained approved package through a verified draft")
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--trusted-key", type=Path, required=True)
    p.add_argument("--confirm-version", required=True)
    p.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    git, github = Git(args.source), GitHub()
    try:
        if args.operation == "initialize":
            initialize(git, args.floor)
        elif args.operation == "reserve":
            reserve(git, args.version)
        elif args.operation == "retire":
            retire(git, args.version)
        elif args.operation == "candidate":
            candidate(git, args.ref, args.output, github)
        elif args.operation == "prepare":
            prepare(git, args.ref, args.version, args.output, args.key, github)
        elif args.operation == "approve":
            approve(git, args.bundle, args.evidence, args.key, args.confirm_version, github)
        elif args.operation == "publish":
            publish(git, args.bundle, args.trusted_key, github, args.confirm_version, args.dry_run)
        print("Release operation completed: " + args.operation)
        return 0
    except NotReady as error:
        print(str(error))
        return 78
    except ReleaseError as error:
        print("Release operation refused: " + str(error))
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, tarfile.TarError):
        print("Release operation refused. Check the evidence, package and exact source before retrying.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Prepare exact packages and publish only an approved stable release."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import selectors
import subprocess
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
    checked(stat.S_ISREG(Path(path).lstat().st_mode), "A release input is not a regular file.")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        checked(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "A release input changed its file type.")
        value = stream.read(limit + 1)
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


def command(arguments, *, cwd=None, input_bytes=None, limit=MAX_JSON, timeout=60, env=None):
    """Drain bounded output and cancel owned descendants before reaping on failure."""
    with tempfile.TemporaryFile() as source:
        if input_bytes is not None:
            source.write(input_bytes)
            source.seek(0)
        process = subprocess.Popen(arguments, cwd=cwd, env=env, stdin=source,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, bufsize=0)
        deadline = time.monotonic() + timeout
        answer, total = bytearray(), 0
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                # Keep the leader unreaped while descendants still own an output pipe.
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    checked(remaining > 0, "A release command exceeded its time limit.")
                    for key, _ in selector.select(timeout=min(remaining, 0.1)):
                        block = os.read(key.fd, 65536)
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        total += len(block)
                        checked(total <= limit, "A release command exceeded its output limit.")
                        if key.data == "stdout":
                            answer.extend(block)
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise ReleaseError("A release command exceeded its time limit.") from None
            checked(process.returncode == 0, "A release command failed. No stable release was approved.")
            return bytes(answer)
        finally:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
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


def archive_record(path):
    checked(Path(path).stat().st_size <= MAX_ARCHIVE, "The archive exceeds the Pi download limit.")
    selected, names, total = {}, set(), 0
    with tarfile.open(path, "r|gz") as archive:
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


def verify_signature(path, signature, trusted_key, namespace="jittersim-release"):
    key = read_bytes(trusted_key, 4096).decode().strip().split()
    checked(len(key) >= 2 and key[0] == "ssh-ed25519", "Use a trusted Ed25519 public key.")
    with tempfile.TemporaryDirectory(prefix="jittersim-verify-") as folder:
        allowed = Path(folder) / "signers"
        allowed.write_text(f'release namespaces="{namespace}" {key[0]} {key[1]}\n')
        command(["ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I", "release", "-n", namespace,
                 "-s", str(signature)], input_bytes=read_bytes(path, MAX_ARCHIVE))


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
        record = archive_record(archive)
        checked(record["commit"] == commit and record["version"] == number and record["sequence"] == row["sequence"],
                "The built archive differs from its reservation.")
        verify_signature(archive, str(archive) + ".sig", str(key) + ".pub")
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
    manifest = archive_record(folder / ASSET)
    checked(all(record.get(name) == value for name, value in manifest.items()), "The release record differs from the archive.")
    checked(record.get("archive_sha256") == digest(folder / ASSET)
            and record.get("signature_sha256") == digest(folder / (ASSET + ".sig"), 16384),
            "The retained package changed.")
    verify_signature(folder / ASSET, folder / (ASSET + ".sig"), trusted_key)
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
    verify_signature(path, str(path) + ".sig", trusted_key, "jittersim-stable-approval")
    approval = read_json(path)
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
                git.call("push", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
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

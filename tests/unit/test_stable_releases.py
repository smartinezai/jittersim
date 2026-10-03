"""Prove stable gates reject mismatched source, packages and approval."""

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "deploy/raspberry-pi" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def release():
    return load("release-system")


@pytest.fixture
def locked():
    return load("prepare-locked-wheels")


def test_registry_requires_inspected_floor(release, tmp_path):
    git = release.Git(tmp_path)
    release.write_json(tmp_path / release.REGISTRY, {"format": 1, "sequence_floor": None, "reservations": []})
    with pytest.raises(release.ReleaseError, match="Inspect"):
        release.reserve(git, "0.2.0")
    release.initialize(git, 2000000000)
    assert release.reserve(git, "0.2.0")["sequence"] == 2000000001
    with pytest.raises(release.ReleaseError):
        release.initialize(git, 0)
    with pytest.raises(release.ReleaseError):
        release.reserve(git, "0.2.0")
    assert release.reserve(git, "0.10.0")["sequence"] == 2000000002
    with pytest.raises(release.ReleaseError):
        release.reserve(git, "0.9.0")


def archive(release, path, *, number="0.2.0", package="0.2.0", extra=None):
    entries = {"deploy/RELEASE": json.dumps({"format": 1, "commit": "a" * 40, "version": number, "sequence": 5}),
               release.POLICY: "{}", release.LOCKS + "/runtime.txt": "runtime",
               release.LOCKS + "/runtime-py39.txt": "runtime39", release.LOCKS + "/build.txt": "build",
               "pyproject.toml": f'version = "{package}"\n',
               "src/jittersim/__init__.py": f'__version__ = "{package}"\n'}
    entries.update(extra or {})
    with tarfile.open(path, "w:gz") as target:
        for name, value in entries.items():
            raw = value.encode()
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            target.addfile(member, io.BytesIO(raw))
    return path


@pytest.mark.parametrize("number,package", [("0.2.0.dev+abc", "0.2.0"), ("0.2.0", "0.3.0")])
def test_development_or_mismatched_archive_refused(release, tmp_path, number, package):
    with pytest.raises(release.ReleaseError):
        release.archive_record(archive(release, tmp_path / "release.tar.gz", number=number, package=package))


@pytest.mark.parametrize("entry", ["../escape", "/escape", ".git/config"])
def test_unsafe_archive_refused(release, tmp_path, entry):
    with pytest.raises(release.ReleaseError):
        release.archive_record(archive(release, tmp_path / "release.tar.gz", extra={entry: "bad"}))


def test_every_lock_part_of_final_identity(release, tmp_path):
    one = release.archive_record(archive(release, tmp_path / "one.tar.gz"))
    two = release.archive_record(archive(release, tmp_path / "two.tar.gz", extra={release.LOCKS + "/runtime-py39.txt": "different"}))
    assert one["locks_sha256"] != two["locks_sha256"]


class GitFixture:
    def __init__(self, root, policy):
        self.root = root
        self.data = policy
        self.commands = []

    def source(self, commit, path):
        return json.dumps(self.data).encode()

    def on_main(self, commit):
        self.commands.append(("main", commit))

    def commit(self, ref):
        return "a" * 40

    def call(self, *args):
        self.commands.append(args)
        return ""


class CI:
    def __init__(self, release):
        self.run = {"id": 12, "run_attempt": 1, "path": ".github/workflows/tests.yml", "head_sha": "a" * 40,
                    "event": "push", "head_branch": "main", "status": "completed", "conclusion": "success",
                    "repository": {"full_name": release.REPOSITORY}, "head_repository": {"full_name": release.REPOSITORY}}
        self.jobs = [{"name": "Tests", "conclusion": "success", "steps": [{"conclusion": "success"}]}]

    def api(self, path):
        if "attempts/" in path:
            return {"total_count": len(self.jobs), "jobs": self.jobs}
        return {"workflow_runs": [self.run]}


@pytest.mark.parametrize("change", ["wrong_sha", "wrong_event", "wrong_repo", "failed", "skipped", "missing_job", "skipped_step"])
def test_ci_refuses_incomplete_or_unrelated_checks(release, tmp_path, change):
    git = GitFixture(tmp_path, {"format": 1, "repository": release.REPOSITORY,
                              "workflows": {".github/workflows/tests.yml": ["Tests"]}})
    github = CI(release)
    if change == "wrong_sha":
        github.run["head_sha"] = "b" * 40
    if change == "wrong_event":
        github.run["event"] = "pull_request"
    if change == "wrong_repo":
        github.run["head_repository"]["full_name"] = "elsewhere/repo"
    if change == "failed":
        github.run["conclusion"] = "failure"
    if change == "skipped":
        github.jobs[0]["conclusion"] = "skipped"
    if change == "missing_job":
        github.jobs = []
    if change == "skipped_step":
        github.jobs[0]["steps"][0]["conclusion"] = "skipped"
    with pytest.raises(release.ReleaseError):
        release.verify_ci(git, "a" * 40, github)


def test_ci_accepts_complete_exact_main_checks(release, tmp_path):
    git = GitFixture(tmp_path, {"format": 1, "repository": release.REPOSITORY,
                              "workflows": {".github/workflows/tests.yml": ["Tests"]}})
    assert release.verify_ci(git, "a" * 40, CI(release))[0]["run_id"] == 12


@pytest.mark.parametrize("change", ["missing_target", "missing_gate", "failed_gate", "wrong_package", "no_evidence"])
def test_physical_acceptance_is_required(release, change):
    record = {"commit": "a" * 40, "archive_sha256": "b" * 64, "locks_sha256": "c" * 64}
    value = dict(record, format=1, targets=["Test Pi / Bookworm / arm64 / Python 3.11"],
                 release_notes="Test release. Supported target is the tested Pi. Upgrade using the signed package.",
                 gates={name: {"passed": True, "evidence": "controlled physical acceptance"} for name in release.GATES})
    if change == "missing_target":
        value["targets"] = []
    if change == "missing_gate":
        value["gates"].pop("upgrade")
    if change == "failed_gate":
        value["gates"]["recovery"]["passed"] = False
    if change == "wrong_package":
        value["archive_sha256"] = "d" * 64
    if change == "no_evidence":
        value["gates"]["safety_state"]["evidence"] = ""
    with pytest.raises(release.ReleaseError):
        release.acceptance(value, record)


@pytest.fixture
def signed(release, tmp_path):
    key = tmp_path / "release-key"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key)], check=True, capture_output=True)
    folder = tmp_path / "bundle"
    folder.mkdir()
    path = archive(release, folder / release.ASSET)
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "jittersim-release", str(path)], check=True, capture_output=True)
    record = release.archive_record(path)
    record.update(archive_sha256=release.digest(path), signature_sha256=release.digest(str(path) + ".sig"), checks=[])
    release.write_json(folder / "release.json", record)
    return folder, key, record


def test_signature_and_bundle_tampering_refused(release, signed):
    folder, key, record = signed
    assert release.bundle(folder, str(key) + ".pub") == record
    with open(folder / release.ASSET, "ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(release.ReleaseError):
        release.bundle(folder, str(key) + ".pub")


def test_missing_approval_never_contacts_github(release, signed, tmp_path):
    folder, key, _ = signed
    git = GitFixture(tmp_path, {})
    release.write_json(tmp_path / release.REGISTRY, {"format": 1, "sequence_floor": 0, "reservations": []})

    class NoGitHub:
        def api(self, path):
            pytest.fail("Missing approval must refuse before contacting GitHub")

    with pytest.raises(OSError):
        release.publish(git, folder, str(key) + ".pub", NoGitHub(), "0.2.0")
    assert not git.commands


@pytest.mark.parametrize("content", ["requests>=2\n", "requests==2 --hash=sha256:bad\n", "--index-url https://elsewhere\n", ""])
def test_unpinned_or_unsafe_lock_refused(locked, tmp_path, content):
    path = tmp_path / "runtime.txt"
    path.write_text(content)
    with pytest.raises(ValueError):
        locked.read_lock(path)


def test_checked_in_locks_complete(locked):
    runtime, build = locked.locks(ROOT)
    assert "requests" in locked.read_lock(runtime)
    assert "setuptools" in locked.read_lock(build)


def test_duplicate_package_refused(locked, tmp_path):
    path = tmp_path / "runtime.txt"
    path.write_text("requests==2 --hash=sha256:" + "a" * 64 + "\nrequests==3 --hash=sha256:" + "b" * 64 + "\n")
    with pytest.raises(ValueError, match="repeats"):
        locked.read_lock(path)


class Publisher(CI):
    """A stateful GitHub stand-in. Cryptographic approval still runs for real."""

    def __init__(self, release):
        super().__init__(release)
        self.release = release
        self.rows = []
        self.files = {}
        self.calls = []
        self.lose_upload = False
        self.change_on_publish = False
        self.tag_commit = "a" * 40
        self.owner = "smartinezai"

    def api(self, path):
        if path == "user":
            return {"login": self.owner}
        if path.endswith("releases?per_page=100"):
            return self.rows
        if "/git/ref/tags/" in path:
            return {"object": {"type": "commit", "sha": self.tag_commit}}
        if "/releases/tags/" in path or path.endswith("releases/latest"):
            return self.rows[0]
        return super().api(path)

    def call(self, *args):
        self.calls.append(args[:3])
        operation = args[1]
        if operation == "create":
            self.rows.append({"id": 51, "tag_name": args[2], "draft": True, "prerelease": False, "assets": []})
        elif operation == "upload":
            path = Path(args[3])
            assert path.name not in self.files, "Never replace an existing asset"
            self.files[path.name] = path.read_bytes()
            self.rows[0]["assets"].append({"name": path.name})
            if self.lose_upload:
                self.lose_upload = False
                raise self.release.ReleaseError("controlled lost upload response")
        elif operation == "download":
            target = Path(args[args.index("--dir") + 1])
            for name, raw in self.files.items():
                (target / name).write_bytes(raw)
        elif operation == "edit":
            self.rows[0]["draft"] = False
            if self.change_on_publish:
                self.files[self.release.ASSET] += b"changed"
        return b""


@pytest.fixture
def approved_package(release, signed, tmp_path):
    folder, key, record = signed
    git = GitFixture(tmp_path, {"format": 1, "repository": release.REPOSITORY,
                              "workflows": {".github/workflows/tests.yml": ["Tests"]}})
    github = Publisher(release)
    release.write_json(tmp_path / release.REGISTRY, {"format": 1, "sequence_floor": 0,
                       "reservations": [{"version": "0.2.0", "sequence": 5, "state": "reserved"}]})
    evidence = dict(record, format=1, targets=["Controlled test target"], release_notes="Test changes and upgrade instructions.",
                    gates={name: {"passed": True, "evidence": "Controlled test evidence"} for name in release.GATES})
    release.write_json(folder / "acceptance.json", evidence)
    release.approve(git, folder, folder / "acceptance.json", key, "0.2.0", github)
    return git, github, folder, key


def test_approved_package_publishes_exactly_once_and_reconciles_retry(release, approved_package):
    git, github, folder, key = approved_package
    first = release.publish(git, folder, str(key) + ".pub", github, "0.2.0")
    assert first["draft"] is False
    assert set(github.files) == {release.ASSET, release.ASSET + ".sig"}
    assert github.files[release.ASSET] == (folder / release.ASSET).read_bytes()
    release.publish(git, folder, str(key) + ".pub", github, "0.2.0")
    assert [call[1] for call in github.calls].count("create") == 1
    assert [call[1] for call in github.calls].count("upload") == 2
    assert [call[1] for call in github.calls].count("edit") == 1
    assert release.registry(git.root / release.REGISTRY)["reservations"][0]["state"] == "published"


def test_lost_upload_response_reuses_the_verified_existing_asset(release, approved_package):
    git, github, folder, key = approved_package
    github.lose_upload = True
    with pytest.raises(release.ReleaseError, match="lost"):
        release.publish(git, folder, str(key) + ".pub", github, "0.2.0")
    assert github.rows[0]["draft"] is True
    release.publish(git, folder, str(key) + ".pub", github, "0.2.0")
    assert [call[1] for call in github.calls].count("upload") == 2
    assert github.rows[0]["draft"] is False


@pytest.mark.parametrize("failure", ["wrong_asset", "wrong_tag", "newer_stable", "retired", "changed_approval", "changed_package"])
def test_publication_refuses_unsafe_retries(release, approved_package, failure):
    git, github, folder, key = approved_package
    if failure == "wrong_asset":
        github.rows = [{"id": 51, "tag_name": "v0.2.0", "draft": True, "prerelease": False,
                        "assets": [{"name": release.ASSET}]}]
        github.files[release.ASSET] = b"another package"
    if failure == "wrong_tag":
        github.tag_commit = "b" * 40
    if failure == "newer_stable":
        github.rows = [{"tag_name": "v0.3.0", "draft": False, "prerelease": False}]
    if failure == "retired":
        release.retire(git, "0.2.0")
    if failure == "changed_approval":
        with open(folder / "approval.json", "ab") as stream:
            stream.write(b" ")
    if failure == "changed_package":
        with open(folder / release.ASSET, "ab") as stream:
            stream.write(b"changed")
    with pytest.raises(release.ReleaseError):
        release.publish(git, folder, str(key) + ".pub", github, "0.2.0")
    assert not any(call[1] == "edit" for call in github.calls)
    assert release.registry(git.root / release.REGISTRY)["reservations"][0]["state"] != "published"


def test_dry_run_does_not_mutate_git_or_github(release, approved_package):
    git, github, folder, key = approved_package
    assert release.publish(git, folder, str(key) + ".pub", github, "0.2.0", dry_run=True)["state"] == "verified-dry-run"
    assert not github.calls
    assert not any(call[0] == "push" for call in git.commands)


def test_changed_bytes_after_publication_require_reconciliation(release, approved_package):
    git, github, folder, key = approved_package
    github.change_on_publish = True
    with pytest.raises(release.ReleaseError, match="reconciliation"):
        release.publish(git, folder, str(key) + ".pub", github, "0.2.0")
    assert not (folder / "publication.json").exists()
    assert release.registry(git.root / release.REGISTRY)["reservations"][0]["state"] == "reserved"


def test_retired_version_and_order_never_reused(release, tmp_path):
    git = release.Git(tmp_path)
    release.write_json(tmp_path / release.REGISTRY, {"format": 1, "sequence_floor": 10, "reservations": []})
    first = release.reserve(git, "0.2.0")
    release.retire(git, "0.2.0")
    release.retire(git, "0.2.0")
    with pytest.raises(release.ReleaseError):
        release.reserve(git, "0.2.0")
    assert release.reserve(git, "0.2.1")["sequence"] > first["sequence"]


@pytest.fixture
def committed_source(release, tmp_path):
    import shutil
    root = tmp_path / "source"
    root.mkdir()
    shutil.copytree(ROOT / "deploy", root / "deploy", ignore=shutil.ignore_patterns("__pycache__"))
    (root / "src/jittersim").mkdir(parents=True)
    (root / "src/jittersim/__init__.py").write_text('__version__ = "0.2.0"\n')
    (root / "pyproject.toml").write_text('[project]\nname = "jittersim"\nversion = "0.2.0"\n')
    (root / "README.md").write_text("Test package\n")
    policy = {"format": 1, "repository": release.REPOSITORY,
              "workflows": {".github/workflows/tests.yml": ["Tests"]}}
    release.write_json(root / release.POLICY, policy)
    release.write_json(root / release.REGISTRY, {"format": 1, "sequence_floor": 0,
                       "reservations": [{"version": "0.2.0", "sequence": 5, "state": "reserved"}]})
    for args in (["init", "-q"], ["config", "user.name", "Release test"],
                 ["config", "user.email", "test@example.invalid"], ["add", "."], ["commit", "-qm", "Test source"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    class MainGit(release.Git):
        def on_main(self, commit):
            assert commit == self.commit("HEAD")

    git = MainGit(root)
    github = CI(release)
    github.run["head_sha"] = git.commit("HEAD")
    return git, github


def test_prepare_retains_final_signed_version_order_and_source(release, committed_source, signed, tmp_path):
    git, github = committed_source
    _, key, _ = signed
    folder = tmp_path / "prepared"
    result = release.prepare(git, "HEAD", "0.2.0", folder, key, github)
    assert result["version"] == "0.2.0" and result["sequence"] == 5
    assert result["commit"] == git.commit("HEAD") == git.commit("v0.2.0")
    assert release.bundle(folder, str(key) + ".pub") == result
    assert not (folder / "approval.json").exists()
    assert all(not gate["passed"] for gate in release.read_json(folder / "acceptance.json")["gates"].values())
    before = (folder / release.ASSET).read_bytes()
    with pytest.raises(release.ReleaseError, match="Retain"):
        release.prepare(git, "HEAD", "0.2.0", folder, key, github)
    assert (folder / release.ASSET).read_bytes() == before


def test_automatic_candidate_is_unsigned_and_always_development(release, committed_source, tmp_path):
    git, github = committed_source
    git.call("tag", "v0.2.0")
    folder = tmp_path / "candidate"
    release.candidate(git, "HEAD", folder, github)
    with tarfile.open(folder / "development.tar.gz") as package:
        manifest = json.load(package.extractfile("deploy/RELEASE"))
    assert manifest["version"].startswith("0.2.0.dev+")
    assert not list(folder.glob("*.sig"))
    assert release.read_json(folder / "candidate.json")["channel"] == "development"



def test_command_output_is_bounded_and_credentials_are_not_reported(release):
    import sys
    with pytest.raises(release.ReleaseError, match="output") as failure:
        release.command([sys.executable, "-c", "import sys; sys.stderr.write('private-token' * 10000)"], limit=128)
    assert "private-token" not in str(failure.value)


def test_timeout_cancels_descendant_after_parent_exits(release, tmp_path):
    import sys
    import time
    marker = tmp_path / "escaped"
    child = "import time; from pathlib import Path; time.sleep(0.5); Path(" + repr(str(marker)) + ").touch()"
    parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(child) + "])"
    with pytest.raises(release.ReleaseError, match="time"):
        release.command([sys.executable, "-c", parent], timeout=0.15)
    time.sleep(0.6)
    assert not marker.exists()



@pytest.mark.parametrize("kind", ["link", "pipe"])
def test_release_input_rejects_links_and_pipes_without_reading(release, tmp_path, kind):
    import os
    path = tmp_path / "input"
    if kind == "link":
        real = tmp_path / "private"
        real.write_text("private content")
        path.symlink_to(real)
    else:
        os.mkfifo(path)
    with pytest.raises(release.ReleaseError, match="regular file"):
        release.read_bytes(path, 100)

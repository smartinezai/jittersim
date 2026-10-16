"""The one-command release path: time-based release order, the signed local verification
record that stands in for hosted CI only when hosted CI never ran, signed release notes in the
archive and a dry run of scripts/release.sh with a throwaway key."""

import importlib.util
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests.release_support import have_ssh_keygen, make_key

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40
TREE = "d" * 40
pytestmark = pytest.mark.skipif(not have_ssh_keygen() or shutil.which("git") is None,
                                reason="ssh-keygen and git make and check the release records")


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "deploy/raspberry-pi" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def release():
    return load("release-system")


@pytest.fixture
def key(tmp_path):
    return make_key(tmp_path / "keys", "owner")[0]


def policy(release, allowed=True, required=("lint", "python-tests", "browser-journeys"),
           workflows=None):
    return {"format": 1, "repository": release.REPOSITORY,
            "workflows": workflows or {".github/workflows/tests.yml": ["Tests"]},
            "local_verification": {"allowed": allowed, "required_checks": list(required)}}


class Git:
    def __init__(self, root, data):
        self.root = root
        self.data = data
        self.commands = []

    def source(self, commit, path):
        return json.dumps(self.data).encode()

    def on_main(self, commit):
        self.commands.append(("main", commit))

    def commit(self, ref):
        return COMMIT

    def call(self, *args):
        self.commands.append(args)
        if args[:2] == ("remote", "get-url"):
            return "https://github.com/smartinezai/jittersim.git"
        if args[:2] == ("rev-parse", "--verify") and args[2].endswith("^{tree}"):
            return TREE
        return ""


class HostedCI:
    """GitHub's answers. 'blocked' is what a billing block looks like: the run and every job
    ended as failed before a single step ran."""

    def __init__(self, release, state="passed", paths=(".github/workflows/tests.yml",)):
        self.runs = []
        self.jobs = {}
        for number, path in enumerate(paths, start=10):
            run = {"id": number, "run_attempt": 1, "path": path, "head_sha": COMMIT, "event": "push",
                   "head_branch": "main", "status": "completed", "conclusion": "success",
                   "repository": {"full_name": release.REPOSITORY},
                   "head_repository": {"full_name": release.REPOSITORY}}
            jobs = [{"name": "Tests", "conclusion": "success", "steps": [{"conclusion": "success"}]}]
            self.runs.append(run)
            self.jobs[number] = jobs
        self.set(state)

    def set(self, state, index=0):
        if state == "missing":
            self.runs = []
            return
        run, jobs = self.runs[index], self.jobs[self.runs[index]["id"]]
        if state == "blocked":
            run["conclusion"] = "failure"
            jobs[:] = [{"name": "Tests", "conclusion": "failure", "steps": []}]
        if state == "failed":
            run["conclusion"] = "failure"
            jobs[:] = [{"name": "Tests", "conclusion": "failure", "steps": [{"conclusion": "failure"}]}]

    def api(self, path):
        if "attempts/" in path:
            number = int(path.split("/runs/")[1].split("/")[0])
            return {"total_count": len(self.jobs[number]), "jobs": self.jobs[number]}
        return {"workflow_runs": self.runs}


def results(tmp_path, checks=("lint", "python-tests", "browser-journeys"), failing=()):
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"runner": "scripts/release.sh", "checks": [
        {"name": name, "command": "python -m " + name, "exit_code": 1 if name in failing else 0,
         "log_sha256": "b" * 64} for name in checks]}))
    return path


@pytest.fixture
def record(release, key, tmp_path):
    path = tmp_path / "evidence" / release.LOCAL_RECORD
    path.parent.mkdir()
    release.record_local(Git(tmp_path, {}), "HEAD", results(tmp_path), key, path)
    return path


def test_release_order_is_never_older_than_now(release, tmp_path, monkeypatch):
    git = release.Git(tmp_path)
    release.write_json(tmp_path / release.REGISTRY, {"format": 1, "sequence_floor": 10, "reservations": []})
    monkeypatch.setattr(release.time, "time", lambda: 1_900_000_000.5)
    assert release.reserve(git, "0.2.0")["sequence"] == 1_900_000_000
    assert release.reserve(git, "0.2.1")["sequence"] == 1_900_000_001
    monkeypatch.setattr(release.time, "time", lambda: 5.0)
    assert release.reserve(git, "0.2.2")["sequence"] == 1_900_000_002


def test_local_record_is_signed_for_one_commit(release, record, key):
    value = json.loads(record.read_text())
    assert value["commit"] == COMMIT and value["hosted_ci"] == "unavailable"
    assert [row["name"] for row in value["checks"]] == ["lint", "python-tests", "browser-journeys"]
    receipt = release.verify_local(COMMIT, record, str(key) + ".pub", release.DEFAULT_LOCAL_CHECKS)
    assert receipt["kind"] == "local" and receipt["checks"] == sorted(release.DEFAULT_LOCAL_CHECKS)
    with pytest.raises(release.ReleaseError, match="another commit"):
        release.verify_local("c" * 40, record, str(key) + ".pub", ())
    with pytest.raises(release.ReleaseError, match="another commit"):
        release.verify_local("c" * 40, record, str(key) + ".pub", (), tree="e" * 40)
    # The merge commit GitHub makes has the same files as the checked release commit.
    merged = release.verify_local("c" * 40, record, str(key) + ".pub", (), tree=TREE)
    assert merged["checked_commit"] == COMMIT and merged["commit"] == "c" * 40


def test_failed_check_never_becomes_a_record(release, key, tmp_path):
    with pytest.raises(release.ReleaseError, match="python-tests"):
        release.record_local(Git(tmp_path, {}), "HEAD", results(tmp_path, failing=("python-tests",)), key,
                             tmp_path / "record.json")
    assert not (tmp_path / "record.json.sig").exists()


@pytest.mark.parametrize("change", ["tampered", "other_key", "missing_check"])
def test_local_record_refuses_changes_other_keys_and_gaps(release, record, key, tmp_path, change):
    trusted = str(key) + ".pub"
    required = release.DEFAULT_LOCAL_CHECKS
    if change == "tampered":
        record.write_text(record.read_text().replace('"exit_code": 0', '"exit_code": 0 ', 1))
    if change == "other_key":
        trusted = str(make_key(tmp_path / "other", "stranger")[0]) + ".pub"
    if change == "missing_check":
        required = (*required, "linux-datapath")
    with pytest.raises(release.ReleaseError):
        release.verify_local(COMMIT, record, trusted, required)


@pytest.mark.parametrize("state", ["blocked", "missing"])
def test_local_record_stands_in_only_when_hosted_ci_never_ran(release, record, key, tmp_path, state):
    git = Git(tmp_path, policy(release))
    receipts = release.verify_ci(git, COMMIT, HostedCI(release, state), (record, str(key) + ".pub"))
    assert receipts[-1]["kind"] == "local"
    assert receipts[-1]["workflows_not_run"] == [".github/workflows/tests.yml"]


def test_blocked_ci_without_record_says_how_to_continue(release, tmp_path):
    with pytest.raises(release.ReleaseError, match="record-local"):
        release.verify_ci(Git(tmp_path, policy(release)), COMMIT, HostedCI(release, "blocked"))


def test_hosted_failure_is_never_overridden(release, record, key, tmp_path):
    with pytest.raises(release.ReleaseError, match="failed"):
        release.verify_ci(Git(tmp_path, policy(release)), COMMIT, HostedCI(release, "failed"),
                          (record, str(key) + ".pub"))


def test_policy_switch_turns_local_records_off(release, record, key, tmp_path):
    git = Git(tmp_path, policy(release, allowed=False))
    with pytest.raises(release.ReleaseError, match="failed"):
        release.verify_ci(git, COMMIT, HostedCI(release, "blocked"), (record, str(key) + ".pub"))
    with pytest.raises(release.NotReady):
        release.verify_ci(git, COMMIT, HostedCI(release, "missing"), (record, str(key) + ".pub"))


def test_policy_without_local_section_keeps_the_old_gate(release, record, key, tmp_path):
    data = policy(release)
    data.pop("local_verification")
    with pytest.raises(release.ReleaseError):
        release.verify_ci(Git(tmp_path, data), COMMIT, HostedCI(release, "blocked"), (record, str(key) + ".pub"))


def test_hosted_and_local_receipts_combine(release, record, key, tmp_path):
    paths = (".github/workflows/tests.yml", ".github/workflows/wifi-recovery.yml")
    git = Git(tmp_path, policy(release, workflows={path: ["Tests"] for path in paths}))
    ci = HostedCI(release, "passed", paths)
    ci.set("blocked", index=1)
    receipts = release.verify_ci(git, COMMIT, ci, (record, str(key) + ".pub"))
    assert receipts[0]["run_id"] == 10 and receipts[1]["workflows_not_run"] == [paths[1]]


def test_checked_in_policy_allows_local_records_and_names_the_checks(release):
    data = json.loads((ROOT / release.POLICY).read_text())
    assert data["local_verification"]["allowed"] is True
    assert set(release.DEFAULT_LOCAL_CHECKS) <= set(data["local_verification"]["required_checks"])


def archive(release, path, number="0.2.0"):
    import io
    entries = {"deploy/RELEASE": json.dumps({"format": 1, "commit": COMMIT, "version": number, "sequence": 5}),
               release.POLICY: "{}", release.LOCKS + "/runtime.txt": "runtime",
               release.LOCKS + "/runtime-py39.txt": "runtime39", release.LOCKS + "/build.txt": "build",
               "pyproject.toml": f'version = "{number}"\n', "src/jittersim/__init__.py": f'__version__ = "{number}"\n'}
    with tarfile.open(path, "w:gz") as target:
        for name, value in entries.items():
            raw = value.encode()
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            target.addfile(member, io.BytesIO(raw))
    return path


class Publisher(HostedCI):
    """GitHub during publication with hosted CI blocked. The notes file is kept."""

    def __init__(self, release):
        super().__init__(release, "blocked")
        self.release = release
        self.rows, self.files, self.calls, self.notes = [], {}, [], ""

    def api(self, path):
        if path == "user":
            return {"login": "smartinezai"}
        if path.endswith("releases?per_page=100"):
            return self.rows
        if "/git/ref/tags/" in path:
            return {"object": {"type": "commit", "sha": COMMIT}}
        if "/releases/tags/" in path:
            # GitHub documents this lookup as "a published release". A draft is a 404 here, so the
            # tool must find a draft it just created in the list of releases instead.
            tag = path.rsplit("/", 1)[1]
            found = [row for row in self.rows if row["tag_name"] == tag and not row["draft"]]
            if not found:
                raise self.release.ReleaseError("A release command failed. No stable release was approved.")
            return found[0]
        if path.endswith("releases/latest"):
            found = [row for row in self.rows if not row["draft"] and not row["prerelease"]]
            if not found:
                raise self.release.ReleaseError("A release command failed. No stable release was approved.")
            return found[0]
        return super().api(path)

    def call(self, *args):
        self.calls.append(args[:3])
        if args[1] == "create":
            self.notes = Path(args[args.index("--notes-file") + 1]).read_text()
            self.rows.append({"id": 51, "tag_name": args[2], "draft": True, "prerelease": False, "assets": []})
        elif args[1] == "upload":
            self.files[Path(args[3]).name] = Path(args[3]).read_bytes()
            self.rows[0]["assets"].append({"name": Path(args[3]).name})
        elif args[1] == "download":
            for name, raw in self.files.items():
                (Path(args[args.index("--dir") + 1]) / name).write_bytes(raw)
        elif args[1] == "edit":
            self.rows[0]["draft"] = False
        return b""


def test_package_with_local_record_is_approved_and_published_as_local(release, record, key, tmp_path):
    folder = tmp_path / "bundle"
    folder.mkdir()
    path = archive(release, folder / release.ASSET)
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "jittersim-release", str(path)],
                   check=True, capture_output=True)
    value = release.archive_record(path)
    value.update(archive_sha256=release.digest(path), signature_sha256=release.digest(str(path) + ".sig"), checks=[])
    release.write_json(folder / "release.json", value)
    for suffix in ("", ".sig"):
        shutil.copy(str(record) + suffix, folder / (release.LOCAL_RECORD + suffix))
    git = Git(tmp_path, policy(release))
    release.write_json(tmp_path / release.REGISTRY, {"format": 1, "sequence_floor": 0,
                       "reservations": [{"version": "0.2.0", "sequence": 5, "state": "reserved"}]})
    release.write_json(folder / "acceptance.json", dict(value, format=1, targets=[], release_notes="", gates={}))
    notes = tmp_path / "notes.md"
    notes.write_text("## v0.2.0\n\nFirst release.\n")
    with pytest.raises(release.ReleaseError, match="gate"):
        release.record_acceptance(folder, notes.read_text(), ["Test Pi"], ["physical=works"])
    release.record_acceptance(folder, notes.read_text(), ["Test Pi"],
                              [f"{gate}=checked by hand" for gate in sorted(release.GATES)])
    github = Publisher(release)
    approval = release.approve(git, folder, folder / "acceptance.json", key, "0.2.0", github)
    assert approval["checks"][-1]["kind"] == "local"
    release.publish(git, folder, str(key) + ".pub", github, "0.2.0")
    assert "LOCAL verification" in github.notes and "did not run" in github.notes
    (folder / release.LOCAL_RECORD).write_text((folder / release.LOCAL_RECORD).read_text() + " ")
    with pytest.raises(release.ReleaseError):
        release.approved(git, folder, str(key) + ".pub", github)


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def source(tmp_path):
    """A small committed copy of the release inputs with a bare 'origin' next to it."""
    root = tmp_path / "source"
    shutil.copytree(ROOT / "deploy", root / "deploy", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "scripts", root / "scripts")
    (root / "src/jittersim").mkdir(parents=True)
    (root / "src/jittersim/__init__.py").write_text('__version__ = "0.2.1"\n')
    (root / "pyproject.toml").write_text('[project]\nname = "jittersim"\nversion = "0.2.1"\n')
    (root / "README.md").write_text("Test package\n")
    shutil.copy(ROOT / "CHANGELOG.md", root / "CHANGELOG.md")
    with open(root / "CHANGELOG.md", "a") as changelog:
        if "## Unreleased" not in (ROOT / "CHANGELOG.md").read_text():
            changelog.write("\n## Unreleased\n\n- Hand-written entry.\n")
    (root / "docs/releases").mkdir(parents=True)
    (root / "docs/releases/registry.json").write_text(json.dumps(
        {"format": 1, "sequence_floor": None, "reservations": []}))
    git(tmp_path, "init", "-q", "--bare", "origin.git")
    for args in (["init", "-q"], ["config", "user.name", "Release test"],
                 ["config", "user.email", "test@example.invalid"], ["add", "."],
                 ["commit", "-qm", "Test source"], ["remote", "add", "origin", str(tmp_path / "origin.git")],
                 ["push", "-q", "origin", "HEAD:refs/heads/main"]):
        git(root, *args)
    return root


def run_release(source, key, tmp_path, *args, env=None):
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    (fake / "gh").write_text("#!/bin/sh\ntouch " + str(tmp_path / "gh-was-called") + "\nexit 1\n")
    (fake / "gh").chmod(0o755)
    environment = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ["PATH"], HOME=str(tmp_path / "home"),
                       JITTERSIM_RELEASE_FAKE_CHECKS="1")
    environment.pop("SSH_AUTH_SOCK", None)
    environment.update(env or {})
    return subprocess.run(["sh", str(source / "scripts/release.sh"), "--dry-run", "--home", str(tmp_path / "releases"),
                           "--key", str(key), *args], cwd=source, env=environment, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=180)


def test_release_script_dry_run_builds_signed_release_without_touching_github(release, source, key, tmp_path):
    head = git(source, "rev-parse", "HEAD")
    result = run_release(source, key, tmp_path, "0.3.0")
    assert result.returncode == 0, result.stdout + result.stderr
    state = tmp_path / "releases/0.3.0"
    package = state / "package" / release.ASSET
    manifest = release.inspect(package, str(key) + ".pub")
    assert manifest["version"] == "0.3.0" and manifest["sequence"] > 1_700_000_000
    with tarfile.open(package) as bundle:
        notes = bundle.extractfile("deploy/RELEASE-NOTES.md").read().decode()
    assert notes.startswith("## v0.3.0 - ") and "Dry run release notes" in notes
    # The hand-written Unreleased entries came along, above the generated notes.
    assert notes.index("- ") < notes.index("Dry run release notes")
    receipt = release.verify_local(manifest["commit"], state / "evidence" / release.LOCAL_RECORD,
                                   str(key) + ".pub", release.DEFAULT_LOCAL_CHECKS)
    assert receipt["runner"].endswith("(fake checks)")
    clone = state / "source"
    assert git(clone, "log", "-1", "--format=%s") == "Start 0.3.1 development"
    assert git(clone, "show", manifest["commit"] + ":pyproject.toml").endswith('version = "0.3.0"')
    changelog = git(clone, "show", manifest["commit"] + ":CHANGELOG.md")
    assert "## v0.3.0 - " in changelog and "## Unreleased" not in changelog
    # Nothing left this Mac: the checkout, its origin and GitHub are unchanged.
    assert git(source, "rev-parse", "HEAD") == head and not git(source, "tag", "--list")
    assert not git(tmp_path / "origin.git", "tag", "--list")
    assert git(tmp_path / "origin.git", "rev-parse", "main") == head
    assert not (tmp_path / "gh-was-called").exists()


def recorded_checks(tmp_path, number="0.3.0"):
    rows = (tmp_path / "releases" / number / "evidence" / "checks.tsv").read_text().splitlines()
    return {row.split("\t")[0]: row.split("\t")[1] for row in rows}


@pytest.mark.parametrize("scope, expected", [
    (None, "bash scripts/local-ci.sh all"),
    ("lint unit", "bash scripts/local-ci.sh lint unit"),
    ("none", None),
])
def test_release_script_gives_local_ci_a_command_it_accepts(release, source, key, tmp_path, scope, expected):
    """scripts/local-ci.sh without a command prints its usage and exits 2, so a bare call made
    every real release stop at its last check. The scope is 'all' unless the owner narrows it."""
    result = run_release(source, key, tmp_path, "0.3.0", env=None if scope is None else
                         {"JITTERSIM_RELEASE_LOCAL_CI": scope})
    assert result.returncode == 0, result.stdout + result.stderr
    command = recorded_checks(tmp_path).get("local-ci")
    assert command == expected
    if command:
        # What would run is a command local-ci.sh itself accepts (it runs nothing under --dry-run).
        words = command.split()[2:]
        check = subprocess.run(["bash", str(ROOT / "scripts/local-ci.sh"), "--dry-run", "--allow-dirty", *words],
                               cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        assert check.returncode == 0, check.stdout + check.stderr
    # The three checks the policy requires are always recorded, whatever the local-ci scope is.
    assert {"lint", "python-tests", "browser-journeys"} <= set(recorded_checks(tmp_path))


def test_generated_github_notes_stay_inside_the_release_section(release, source, key, tmp_path):
    """GitHub's generated notes start with a '## What's Changed' heading. Put into CHANGELOG.md as
    they are, that line ends the release's section, so the archive (cut at the next '## ' line)
    lost the whole generated list and CHANGELOG.md got a stray top-level section."""
    notes = tmp_path / "github-notes.md"
    notes.write_text("## What's Changed\n* Add a thing by @owner in https://github.com/o/r/pull/1\n\n"
                     "## New Contributors\n* @someone made their first contribution\n\n"
                     "**Full Changelog**: https://github.com/o/r/commits/v0.3.0\n")
    result = run_release(source, key, tmp_path, "0.3.0", "--notes-file", str(notes))
    assert result.returncode == 0, result.stdout + result.stderr
    state = tmp_path / "releases/0.3.0"
    manifest = release.inspect(state / "package" / release.ASSET, str(key) + ".pub")
    with tarfile.open(state / "package" / release.ASSET) as bundle:
        archived = bundle.extractfile("deploy/RELEASE-NOTES.md").read().decode()
    assert archived.startswith("## v0.3.0 - ")
    for line in ("Add a thing by @owner", "someone made their first", "**Full Changelog**"):
        assert line in archived, line
    changelog = git(state / "source", "show", manifest["commit"] + ":CHANGELOG.md")
    headings = [line for line in changelog.splitlines() if line.startswith("## ")]
    assert sum(line.startswith("## v0.3.0 - ") for line in headings) == 1
    assert not any(line.startswith("## What's Changed") or line.startswith("## New Contributors")
                   for line in headings)
    assert "### What's Changed" in changelog and "### New Contributors" in changelog


def test_release_script_refuses_an_old_or_malformed_version(source, key, tmp_path):
    git(source, "tag", "v0.3.0")
    result = run_release(source, key, tmp_path, "0.3.0")
    assert result.returncode != 0 and "not newer" in result.stderr
    assert run_release(source, key, tmp_path, "0.3").returncode == 2


def test_tagged_build_carries_only_its_changelog_section(release, source, key, tmp_path):
    (source / "CHANGELOG.md").write_text("# Changelog\n\n## v0.2.10 - later\n\nNot this.\n\n"
                                         "## v0.2.1 - 2026-10-09\n\nThis one.\n\n## v0.2.0 - earlier\n\nOld.\n")
    git(source, "commit", "-qam", "Notes")
    git(source, "tag", "v0.2.1")
    out = tmp_path / "out"
    out.mkdir()
    subprocess.run(["sh", "deploy/raspberry-pi/build-release.sh", "HEAD", str(out / "release.tar.gz")],
                   cwd=source, check=True, capture_output=True)
    with tarfile.open(out / "release.tar.gz") as bundle:
        assert bundle.extractfile("deploy/RELEASE-NOTES.md").read().decode() == "## v0.2.1 - 2026-10-09\n\nThis one.\n"
    git(source, "commit", "-q", "--allow-empty", "-m", "Development")
    subprocess.run(["sh", "deploy/raspberry-pi/build-release.sh", "HEAD", str(out / "dev.tar.gz")],
                   cwd=source, check=True, capture_output=True)
    with tarfile.open(out / "dev.tar.gz") as bundle:
        assert "deploy/RELEASE-NOTES.md" not in bundle.getnames()

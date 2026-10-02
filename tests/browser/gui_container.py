"""Run GUI evidence in an owned container after the shared Docker host is available."""

import argparse
import json
import re
import subprocess
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from tests.browser.gui_capture import STATES, deadline, stop_fixture

BASE_IMAGE_PATTERN = r"python:3\.13-slim-bookworm@sha256:[0-9a-f]{64}"
IMAGE_ID_PATTERN = r"sha256:[0-9a-f]{64}"
MANIFEST_LIMIT = 512 * 1024
CONTAINER_SCRIPT = """import subprocess
import sys
for module, output in (
    ('tests.browser.gui_baseline', '/artifacts/screenshots'),
    ('tests.browser.a11y_axe', '/artifacts/axe'),
):
    subprocess.run([sys.executable, '-m', module, '--output', output], check=True)
"""


def validate_base_image(value):
    if not re.fullmatch(BASE_IMAGE_PATTERN, value):
        raise ValueError("A pinned Python 3.13 slim-bookworm digest is required")
    return value


def clean_source(repo_root):
    repo_root = Path(repo_root).resolve()
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--show-toplevel"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=15,
    )
    if Path(result.stdout.strip()).resolve() != repo_root:
        raise ValueError("The build directory must be the repository root")
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=15,
    )
    commit = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("The source commit is invalid")
    result = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=all"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
    )
    if result.stdout:
        raise ValueError("Commit or remove source changes before a container baseline")
    if not (repo_root / "tests/browser/Dockerfile").is_file():
        raise ValueError("The browser Dockerfile is missing")
    return repo_root, commit


MAX_CONTEXT_ENTRIES = 5000
MAX_CONTEXT_BYTES = 128 * 1024 * 1024


@contextmanager
def source_context(repo_root, commit):
    """Stream only tracked inputs into an owned bounded build directory."""
    with tempfile.TemporaryDirectory(prefix="jittersim-gui-context-") as temporary:
        context = Path(temporary) / "context"
        context.mkdir()
        process = subprocess.Popen(
            ["git", "-C", str(repo_root), "archive", "--format=tar", commit,
             "pyproject.toml", "README.md", "src", "tests"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        try:
            with deadline(30), tarfile.open(fileobj=process.stdout, mode="r|") as source:
                count = 0
                total = 0
                seen = set()
                for member in source:
                    count += 1
                    if count > MAX_CONTEXT_ENTRIES:
                        raise RuntimeError("Tracked source exceeds its file limit")
                    parts = PurePosixPath(member.name)
                    if (not parts.parts or parts.is_absolute() or ".." in parts.parts
                            or not (member.isfile() or member.isdir()) or member.name in seen):
                        raise RuntimeError("Tracked source archive contains an unsafe entry")
                    seen.add(member.name)
                    if member.size < 0 or member.size > MAX_CONTEXT_BYTES - total:
                        raise RuntimeError("Tracked source exceeds its byte limit")
                    total += member.size
                    destination = context.joinpath(*parts.parts)
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    content = source.extractfile(member)
                    written = 0
                    with content, destination.open("wb") as output:
                        while True:
                            chunk = content.read(min(65536, member.size - written + 1))
                            if not chunk:
                                break
                            if written + len(chunk) > member.size:
                                raise RuntimeError("Tracked source entry exceeds its declared size")
                            output.write(chunk)
                            written += len(chunk)
                    if written != member.size:
                        raise RuntimeError("Tracked source archive is truncated")
                    destination.chmod(0o755 if member.mode & 0o111 else 0o644)
                if process.wait(timeout=5) != 0:
                    raise RuntimeError("Git source archive failed")
        finally:
            stop_fixture(process)
        dockerfile_path = context / "tests/browser/Dockerfile"
        if not dockerfile_path.is_file():
            raise RuntimeError("The selected commit lacks the browser Dockerfile")
        dockerfile = dockerfile_path.read_text(encoding="utf-8")
        if (not re.search(r"^ARG BROWSER_BASE_IMAGE(?:=[^\n]+)?$", dockerfile, re.MULTILINE)
                or re.findall(r"^FROM (.+)$", dockerfile, re.MULTILINE) != ["${BROWSER_BASE_IMAGE}"]):
            raise RuntimeError("The selected Dockerfile does not honor the pinned base image")
        yield context


def write_report(directory, report):
    data = json.dumps(report, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    if len(data) > MANIFEST_LIMIT:
        raise RuntimeError("Container report exceeds its size limit")
    destination = directory / "container-report.json"
    temporary = directory / "container-report.tmp"
    if destination.is_symlink() or temporary.is_symlink():
        raise RuntimeError("Container report path is unsafe")
    temporary.write_bytes(data)
    temporary.replace(destination)


def verify_runner_manifest(path, commit):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MANIFEST_LIMIT:
        raise RuntimeError("Runner manifest is missing or oversized")
    report = json.loads(path.read_text(encoding="utf-8"))
    states = report.get("states", [])
    if (report.get("status") != "complete" or report.get("complete") is not True
            or report.get("commit") != commit or report.get("source_dirty") is not False
            or report.get("expected_states") != 40 or len(states) != 40
            or {state.get("id") for state in states} != {state["id"] for state in STATES}
            or any(state.get("status") != "complete" for state in states)):
        raise RuntimeError("Runner manifest does not establish complete clean-source evidence")


def cleanup_owned(container_name, image_tag):
    results = []
    expires = time.monotonic() + 15
    for kind, command in (
        ("container", ["docker", "rm", "--force", container_name]),
        ("image", ["docker", "image", "rm", image_tag]),
    ):
        remaining = expires - time.monotonic()
        if remaining <= 0:
            results.append({"resource": kind, "status": "timeout"})
            continue
        try:
            result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=remaining, check=False)
            results.append({"resource": kind, "status": "removed" if result.returncode == 0
                            else "not_removed", "returncode": result.returncode})
        except (OSError, subprocess.TimeoutExpired) as error:
            results.append({"resource": kind, "status": "failed",
                            "failure_type": type(error).__name__})
    return results


def run(base_image, repo_root):
    base_image = validate_base_image(base_image)
    repo_root, commit = clean_source(repo_root)
    identifier = uuid.uuid4().hex
    stem = f"{commit[:12]}-{identifier}"
    image_tag = f"jittersim-gui-image-{stem}"
    container_name = f"jittersim-gui-container-{stem}"
    parent = repo_root / "test-results"
    if parent.is_symlink():
        raise ValueError("The artifact parent must not be a symlink")
    parent.mkdir(exist_ok=True)
    directory = parent / f"gui-container-{stem}"
    directory.mkdir()
    report = {"status": "running", "complete": False, "source_commit": commit,
              "source_dirty": False, "base_image": base_image, "image_tag": image_tag,
              "container_name": container_name, "stage": "prepare",
              "build_context": "tracked-commit-archive"}
    write_report(directory, report)
    docker_started = False
    try:
        with source_context(repo_root, commit) as context:
            report["stage"] = "build"
            docker_started = True
            subprocess.run(
                ["docker", "build", "--file", "tests/browser/Dockerfile", "--tag", image_tag,
                 "--build-arg", f"BROWSER_BASE_IMAGE={base_image}", "."],
                cwd=context, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=900, check=True,
            )
        report["stage"] = "inspect"
        result = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image_tag],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=15, check=True,
        )
        image_id = result.stdout.strip()
        if not re.fullmatch(IMAGE_ID_PATTERN, image_id):
            raise RuntimeError("Docker returned an invalid immutable image identifier")
        report["built_image_id"] = image_id
        report["stage"] = "run"
        write_report(directory, report)
        subprocess.run(
            ["docker", "run", "--rm", "--name", container_name, "--shm-size", "1g",
             "--env", f"JITTERSIM_GUI_SOURCE_COMMIT={commit}",
             "--env", "JITTERSIM_GUI_SOURCE_DIRTY=0", "--volume", f"{directory}:/artifacts",
             image_id, "python", "-c", CONTAINER_SCRIPT],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1500, check=True,
        )
        report["stage"] = "verify"
        for runner in ("screenshots", "axe"):
            verify_runner_manifest(directory / runner / "manifest.json", commit)
        report.update(status="complete", complete=True, stage="finished")
    except BaseException as error:
        report.update(status="failed", complete=False, failure_type=type(error).__name__)
        raise
    finally:
        report["cleanup"] = cleanup_owned(container_name, image_tag) if docker_started else []
        write_report(directory, report)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        directory = run(args.base_image, args.repo_root)
    except Exception as error:
        parser.exit(1, f"Container baseline failed ({type(error).__name__}). "
                    "Review the safe container report when one was created.\n")
    print(f"Container baseline complete: {directory}")


if __name__ == "__main__":
    main()

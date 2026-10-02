"""Run GUI evidence in an owned container after the shared Docker host is available."""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from tests.browser.gui_capture import STATES, deadline, stop_fixture

BASE_IMAGE_PATTERN = r"python:3\.13-slim-bookworm@sha256:[0-9a-f]{64}"
CACHED_IMAGE_PATTERN = r"jittersim-browser-tests(?:-[a-z0-9-]+)?@sha256:[0-9a-f]{64}"
MAX_WHEEL_BYTES = 16 * 1024 * 1024
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


def verify_runner_manifest(path, commit, server_hash=None):
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
    if server_hash is not None and (
            report.get("runtime_package", {}).get("location_kind") != "installed"
            or report.get("runtime_package", {}).get("server_source_sha256") != server_hash):
        raise RuntimeError("Runner did not use the intended installed wheel")


def cleanup_owned(container_name, image_tag=None):
    results = []
    expires = time.monotonic() + 15
    commands = [("container", ["docker", "rm", "--force", container_name])]
    if image_tag is not None:
        commands.append(("image", ["docker", "image", "rm", image_tag]))
    for kind, command in commands:
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


def run(base_image, repo_root, cached_browser_image=None):
    if bool(base_image) == bool(cached_browser_image):
        raise ValueError("Choose exactly one fresh base or cached browser image")
    if cached_browser_image:
        return run_cached(cached_browser_image, repo_root)
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
              "build_context": "tracked-commit-archive", "verification_mode": "fresh-build"}
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


def validate_cached_image(value):
    if not re.fullmatch(CACHED_IMAGE_PATTERN, value):
        raise ValueError("A pinned local JitterSim browser-image digest is required")
    return value


def inspect_local_image(reference):
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", reference],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=15, check=True,
    )
    image_id = result.stdout.strip()
    if not re.fullmatch(IMAGE_ID_PATTERN, image_id):
        raise RuntimeError("The cached image has no valid immutable identifier")
    return image_id


def wheel_identity(directory):
    wheels = list(directory.iterdir())
    if len(wheels) != 1 or not re.fullmatch(r"jittersim-[A-Za-z0-9_.]+-py3-none-any\.whl", wheels[0].name):
        raise RuntimeError("Exactly one universal JitterSim wheel is required")
    wheel = wheels[0]
    if wheel.is_symlink() or not wheel.is_file() or wheel.stat().st_size > MAX_WHEEL_BYTES:
        raise RuntimeError("The wheel is missing or exceeds its byte limit")
    digest = hashlib.sha256()
    size = 0
    with wheel.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            size += len(chunk)
            if size > MAX_WHEEL_BYTES:
                raise RuntimeError("The wheel exceeds its byte limit")
            digest.update(chunk)
    with zipfile.ZipFile(wheel) as archive:
        members = archive.infolist()
        if (len(members) > MAX_CONTEXT_ENTRIES or len({member.filename for member in members}) != len(members)
                or sum(member.file_size for member in members) > MAX_CONTEXT_BYTES):
            raise RuntimeError("The wheel contents exceed their limits")
        server = archive.getinfo("jittersim/api/server.py")
        if server.file_size > 4 * 1024 * 1024:
            raise RuntimeError("The dashboard source exceeds its byte limit")
        runtime_hashes = {}
        for member in members:
            if member.filename.startswith("jittersim/") and Path(member.filename).suffix in (".py", ".js", ".css"):
                parts = PurePosixPath(member.filename)
                if parts.is_absolute() or ".." in parts.parts:
                    raise RuntimeError("The wheel contains an unsafe runtime path")
                runtime_hashes[member.filename] = hashlib.sha256(archive.read(member)).hexdigest()
        if "jittersim/__init__.py" not in runtime_hashes:
            raise RuntimeError("The wheel lacks the package entry")
        server_hash = runtime_hashes[server.filename]
        metadata = [member for member in members if member.filename.endswith(".dist-info/WHEEL")]
        if len(metadata) != 1 or metadata[0].file_size > 4096:
            raise RuntimeError("The wheel metadata is invalid")
        if b"Tag: py3-none-any" not in archive.read(metadata[0]).splitlines():
            raise RuntimeError("The wheel does not declare a portable Python tag")
    return {"file": wheel.name, "bytes": size, "sha256": digest.hexdigest(),
            "server_source_sha256": server_hash, "runtime_files_sha256": runtime_hashes}


def build_host_wheel(context, directory):
    directory.mkdir()
    process = subprocess.Popen(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(directory), str(context)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        with deadline(120):
            if process.wait(timeout=120) != 0:
                raise RuntimeError("The host wheel build failed")
    finally:
        stop_fixture(process)
    return wheel_identity(directory)


def verify_cached_install(package_path, server_path, tests_path, fixture_path, wheel):
    """Check all wheel-owned runtime files and every already imported app module."""
    package_path = Path(package_path).resolve()
    server_path = Path(server_path).resolve()
    root = package_path.parent
    if "site-packages" not in root.parts or root.name != "jittersim" or package_path.name != "__init__.py":
        raise RuntimeError("The cached runtime imported old checkout source")
    if server_path != root / "api/server.py":
        raise RuntimeError("The cached runtime imported a different dashboard")
    if (Path(tests_path).resolve() != Path("/jittersim-gui-baseline/tests/__init__.py")
            or Path(fixture_path).resolve() != Path("/jittersim-gui-baseline/tests/browser/fixture.py")):
        raise RuntimeError("The cached runtime imported old tests")
    hashes = wheel["runtime_files_sha256"]
    if not hashes or len(hashes) > MAX_CONTEXT_ENTRIES:
        raise RuntimeError("The intended runtime file map is invalid")
    total = 0
    for relative, expected_hash in hashes.items():
        parts = PurePosixPath(relative)
        if not parts.parts or parts.parts[0] != "jittersim" or ".." in parts.parts:
            raise RuntimeError("The intended runtime path is invalid")
        path = root.parent.joinpath(*parts.parts)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise RuntimeError("The installed runtime file is outside the package")
        total += path.stat().st_size
        if total > MAX_CONTEXT_BYTES:
            raise RuntimeError("The installed runtime exceeds its byte limit")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise RuntimeError("The installed runtime differs from the intended wheel")
    for name, module in tuple(sys.modules.items()):
        if name == "jittersim" or name.startswith("jittersim."):
            value = getattr(module, "__file__", None)
            if value is not None:
                path = Path(value).resolve()
                if not path.is_relative_to(root):
                    raise RuntimeError("An imported app module came from old source")
                relative = path.relative_to(root.parent).as_posix()
                if relative not in hashes:
                    raise RuntimeError("An imported app module is not owned by the wheel")


def cached_runtime_main(wheel):
    """Executed only inside the coordinator's disposable Linux container."""
    if platform.system() != "Linux" or sys.version_info[:2] != (3, 13):
        raise RuntimeError("Cached runtime must use Linux and Python 3.13")
    if importlib.metadata.version("playwright") != "1.63.0":
        raise RuntimeError("Cached runtime Playwright version does not match")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as browser_tool:
        chromium = Path(browser_tool.chromium.executable_path)
        if not chromium.is_file():
            raise RuntimeError("Cached Chromium is missing")
    wheel_directory = Path("/jittersim-gui-wheels")
    if wheel_identity(wheel_directory) != wheel:
        raise RuntimeError("Mounted wheel identity does not match")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--force-reinstall",
         str(wheel_directory / wheel["file"])], check=True, timeout=120,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True, timeout=30,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import jittersim
    import jittersim.api.server as server_module
    import tests
    import tests.browser.fixture as fixture_module

    verify_cached_install(jittersim.__file__, server_module.__file__, tests.__file__,
                          fixture_module.__file__, wheel)
    dependencies = {name: importlib.metadata.version(name)
                    for name in ("jittersim", "playwright", "requests", "pycryptodome")}
    runtime = {"verification_mode": "cached-runtime", "system": "Linux",
               "python": platform.python_version(), "dependencies": dependencies,
               "chromium_present": True, "tests_location": "isolated-read-only-mount",
               "package_location": "site-packages", "verified_runtime_files": len(wheel["runtime_files_sha256"]),
               "wheel": wheel}
    Path("/artifacts/cached-runtime.json").write_text(json.dumps(runtime, indent=2), encoding="utf-8")
    exec(CONTAINER_SCRIPT, {"__name__": "__main__"})


def run_cached(reference, repo_root):
    reference = validate_cached_image(reference)
    repo_root, commit = clean_source(repo_root)
    image_id = inspect_local_image(reference)
    stem = f"{commit[:12]}-{uuid.uuid4().hex}"
    container_name = f"jittersim-gui-container-{stem}"
    parent = repo_root / "test-results"
    if parent.is_symlink():
        raise ValueError("The artifact parent must not be a symlink")
    parent.mkdir(exist_ok=True)
    directory = parent / f"gui-container-{stem}"
    directory.mkdir()
    report = {"status": "running", "complete": False, "source_commit": commit,
              "source_dirty": False, "cached_browser_image": reference, "runtime_image_id": image_id,
              "container_name": container_name, "stage": "prepare",
              "verification_mode": "cached-runtime", "build_context": "tracked-commit-archive"}
    write_report(directory, report)
    container_started = False
    try:
        with source_context(repo_root, commit) as context:
            report["stage"] = "wheel"
            wheels = context.parent / "wheels"
            wheel = build_host_wheel(context, wheels)
            report["wheel"] = wheel
            report["stage"] = "run"
            write_report(directory, report)
            script = "from tests.browser.gui_container import cached_runtime_main; cached_runtime_main(" + repr(wheel) + ")"
            command = [
                "docker", "run", "--pull=never", "--rm", "--name", container_name,
                "--shm-size", "1g", "--workdir", "/jittersim-gui-baseline", "--entrypoint", "python",
                "--env", "PYTHONPATH=", "--env", f"JITTERSIM_GUI_SOURCE_COMMIT={commit}",
                "--env", "JITTERSIM_GUI_SOURCE_DIRTY=0",
                "--volume", f"{context / 'tests'}:/jittersim-gui-baseline/tests:ro",
                "--volume", f"{wheels}:/jittersim-gui-wheels:ro",
                "--volume", f"{directory}:/artifacts", image_id, "-c", script,
            ]
            container_started = True
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=1500, check=True)
            report["stage"] = "verify"
            for runner in ("screenshots", "axe"):
                verify_runner_manifest(directory / runner / "manifest.json", commit,
                                       wheel["server_source_sha256"])
            runtime_path = directory / "cached-runtime.json"
            if runtime_path.is_symlink() or not runtime_path.is_file() or runtime_path.stat().st_size > MANIFEST_LIMIT:
                raise RuntimeError("Cached runtime identity report is missing")
            runtime = json.loads(runtime_path.read_text())
            if runtime.get("wheel") != wheel or runtime.get("verification_mode") != "cached-runtime":
                raise RuntimeError("Cached runtime identity report differs from the intended wheel")
            report.update(status="complete", complete=True, stage="finished")
    except BaseException as error:
        report.update(status="failed", complete=False, failure_type=type(error).__name__)
        raise
    finally:
        report["cleanup"] = cleanup_owned(container_name) if container_started else []
        write_report(directory, report)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    images = parser.add_mutually_exclusive_group(required=True)
    images.add_argument("--base-image")
    images.add_argument("--cached-browser-image")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        directory = run(args.base_image, args.repo_root, args.cached_browser_image)
    except Exception as error:
        parser.exit(1, f"Container baseline failed ({type(error).__name__}). "
                    "Review the safe container report when one was created.\n")
    print(f"Container baseline complete: {directory}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Prepare fixed dependencies and the application before replacing live files."""

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

LINE = re.compile(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+(?: --hash=sha256:[0-9a-f]{64}){1,256}\Z")
MAX_LOCK = 128 * 1024


def read_lock(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_LOCK:
        raise ValueError("The release dependency lock is missing or unsafe.")
    rows, names = [], set()
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        if not LINE.fullmatch(line) or len(rows) >= 32:
            raise ValueError("The release dependency lock is not valid.")
        name = re.sub(r"[-_.]+", "-", line.split("==", 1)[0]).lower()
        if name in names:
            raise ValueError("The release dependency lock repeats a package.")
        names.add(name)
        rows.append(line)
    if not rows:
        raise ValueError("The release dependency lock is empty.")
    return names


def locks(source):
    root = Path(source) / "deploy/raspberry-pi/release-locks"
    runtime = root / ("runtime-py39.txt" if sys.version_info[:2] == (3, 9) else "runtime.txt")
    build = root / "build.txt"
    runtime_names, build_names = read_lock(runtime), read_lock(build)
    if not {"requests", "pycryptodome", "urllib3", "idna", "certifi", "charset-normalizer"}.issubset(runtime_names):
        raise ValueError("The runtime dependency lock is incomplete.")
    if not {"pip", "setuptools", "wheel", "packaging"}.issubset(build_names):
        raise ValueError("The build dependency lock is incomplete.")
    if not (3, 9) <= sys.version_info[:2] <= (3, 13):
        raise ValueError("This Python version is outside the release dependency support range.")
    return runtime, build


def prepare(source, destination, run=subprocess.run, wheelhouse=None):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    runtime, build = locks(source)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Use an empty folder for prepared release packages.")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jittersim-locked-") as temporary:
        work = Path(temporary)
        environment = {key: value for key, value in os.environ.items() if not key.startswith("PIP_")}
        environment.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_CACHE_DIR="true", TMPDIR=str(work))

        def call(arguments):
            return run(arguments, check=True, timeout=60, env=environment, stdin=subprocess.DEVNULL)

        builder = work / "builder"
        call([sys.executable, "-I", "-m", "venv", str(builder)])
        python = str(builder / "bin/python")
        download_source = (["--no-index", "--find-links", str(Path(wheelhouse).resolve())]
                           if wheelhouse is not None else ["--index-url", "https://pypi.org/simple"])
        base = [python, "-I", "-m", "pip", "--disable-pip-version-check", "--no-input"]
        build_wheels = work / "build-wheels"
        call([*base, "download", "--require-hashes", "--only-binary=:all:", "--no-deps", "--timeout", "15",
              "--retries", "0", *download_source, "--dest", str(build_wheels), "-r", str(build)])
        call([*base, "install", "--require-hashes", "--only-binary=:all:", "--no-deps", "--no-index",
              "--find-links", str(build_wheels), "-r", str(build)])
        call([*base, "download", "--require-hashes", "--only-binary=:all:", "--no-deps", "--timeout", "15",
              "--retries", "0", *download_source, "--dest", str(destination), "-r", str(runtime)])
        call([*base, "wheel", "--no-index", "--no-build-isolation", "--no-deps", "--wheel-dir", str(destination), str(source)])
        checker = work / "checker"
        call([sys.executable, "-I", "-m", "venv", str(checker)])
        wheels = sorted(destination.glob("*.whl"))
        if (not 1 <= len(wheels) <= 32 or len(list(destination.iterdir())) != len(wheels)
                or any(path.is_symlink() or not 0 < path.stat().st_size <= 64 * 1024 * 1024 for path in wheels)
                or sum(path.stat().st_size for path in wheels) > 256 * 1024 * 1024):
            raise ValueError("The prepared wheel set is invalid.")
        call([str(checker / "bin/python"), "-I", "-m", "pip", "install", "--no-index", "--no-deps",
              *[str(path) for path in wheels]])
        call([str(checker / "bin/python"), "-I", "-m", "pip", "check"])
        call([str(checker / "bin/python"), "-I", "-c", "import jittersim, requests, Crypto"])
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in wheels}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--wheelhouse", type=Path, help="optional offline wheels checked against the same hashes")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.check:
            locks(args.source)
        elif args.output is not None:
            prepare(args.source, args.output, wheelhouse=args.wheelhouse)
        else:
            parser.error("--output is required unless --check is used")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print("Release package preparation failed. Keep the current installation.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

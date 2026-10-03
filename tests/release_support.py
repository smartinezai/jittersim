"""Throwaway release signing keys and signed release archives for tests.

Every key is made inside the test's own folder and is deleted with it. No test reads a key
from the home folder.
"""

import base64
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PI = ROOT / "deploy" / "raspberry-pi"
SSH_KEYGEN = shutil.which("ssh-keygen") or "/usr/bin/ssh-keygen"
NAMESPACE = "jittersim-release"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def have_ssh_keygen():
    return Path(SSH_KEYGEN).exists()


def make_key(directory, name="release", comment="owner@mac"):
    """Make an ed25519 key pair without a passphrase and return (private path, public line)."""

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    private = directory / name
    subprocess.run(
        [SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(private)],
        check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
    )
    return private, (directory / (name + ".pub")).read_text()


def sign(path, private, namespace=NAMESPACE):
    """Sign like sign-release.sh and return PATH.sig. An old signature is removed first because
    ssh-keygen asks before it replaces one."""

    signature = Path(str(path) + ".sig")
    if signature.exists():
        signature.unlink()
    subprocess.run(
        [SSH_KEYGEN, "-Y", "sign", "-f", str(private), "-n", namespace, str(path)],
        check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
    )
    return signature


def fingerprint(public_line):
    blob = base64.b64decode(public_line.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def ed25519_line(comment="owner@mac", key=None, kind=b"ssh-ed25519", outer="ssh-ed25519",
                 extra=b""):
    """A public key line built from raw bytes. The arguments make broken keys on purpose."""

    material = os.urandom(32) if key is None else key
    blob = struct.pack(">I", len(kind)) + kind + struct.pack(">I", len(material)) + material
    line = outer + " " + base64.b64encode(blob + extra).decode()
    return line + (" " + comment if comment else "")


def manifest(sequence=100, version="v1", commit=COMMIT):
    return json.dumps(
        {"format": 1, "commit": commit, "version": version, "sequence": sequence}
    ) + "\n"


def release_files(sequence=100, version="v1", commit=COMMIT, release=None):
    files = {
        "pyproject.toml": "[project]\nname='jittersim'",
        "README.md": "Release",
        "src/jittersim/api/server.py": "new code",
        "src/jittersim/updates.py": "new recovery manager",
        "deploy/RELEASE": manifest(sequence, version, commit) if release is None else release,
    }
    # The release ships this checkout's Pi files so the installer steps are real.
    files.update(
        {
            "deploy/raspberry-pi/" + item.name: item.read_text()
            for item in PI.iterdir()
            if item.is_file()
        }
    )
    return files


def write_archive(path, files, extra=()):
    """Write a gzip tar. EXTRA holds TarInfo entries or (TarInfo, bytes) pairs."""

    with tarfile.open(path, "w:gz") as archive:
        for name, content in files.items():
            raw = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(raw))
        for entry in extra:
            if isinstance(entry, tuple):
                info, raw = entry
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
            else:
                archive.addfile(entry)
    return Path(path)


def signed_release(directory, private, *, name="release.tar.gz", sequence=100, version="v1",
                   commit=COMMIT, release=None, drop=(), extra=(), replace=None):
    files = release_files(sequence, version, commit, release)
    for item in drop:
        files.pop(item)
    files.update(replace or {})
    path = write_archive(Path(directory) / name, files, extra)
    sign(path, private)
    return path


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

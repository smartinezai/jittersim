"""The one version label JitterSim shows everywhere and the notes of a signed release.

A release archive carries a signed manifest, deploy/RELEASE, whose version is `X.Y.Z` for a
release tagged vX.Y.Z and `X.Y.Z.dev+COMMIT` for a development build. An update copies the
archive's deploy folder to /opt/jittersim, so the installed label is read from there. Without
it (a checkout on a Mac) the package version is shown.
"""

import json
import re
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Optional

CODE_ROOT = Path("/opt/jittersim")
MANIFEST = Path("deploy") / "RELEASE"
NOTES = Path("deploy") / "RELEASE-NOTES.md"
MAX_MANIFEST = 4096
MAX_NOTES = 16 * 1024
LABEL = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(\.dev\+[0-9a-f]{7,40})?\Z")


def _read(path: Path, limit: int) -> Optional[bytes]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        return None
    return raw if len(raw) <= limit else None


def installed_label(code_root: Optional[Path] = None) -> Optional[str]:
    """The version of the signed manifest the Pi runs, or None."""

    raw = _read((CODE_ROOT if code_root is None else code_root) / MANIFEST, MAX_MANIFEST)
    if raw is None:
        return None
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return None
    label = value.get("version") if isinstance(value, dict) else None
    return label if isinstance(label, str) and LABEL.fullmatch(label) else None


def package_label() -> Optional[str]:
    try:
        return package_version("jittersim")
    except PackageNotFoundError:
        return None


def current_label(code_root: Optional[Path] = None) -> str:
    return installed_label(code_root) or package_label() or "development"


def release_notes(folder: Path) -> Optional[str]:
    """The signed notes of a staged or installed release folder, as text, cut at 16 KB.
    Characters that could move a terminal cursor are for the caller to remove."""

    try:
        with (folder / NOTES).open("rb") as handle:
            raw = handle.read(MAX_NOTES)
    except OSError:
        return None
    text = raw.decode("utf-8", errors="replace").strip()
    return text or None

"""Root-private installer source comes only from the verified archive copy."""

import hashlib
import io
import json
import os
import runpy
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/raspberry-pi/prepare-installer-source.py"


def archive(tmp_path, name="src/jittersim/updates.py", kind=tarfile.REGTYPE, content=b"owned source"):
    path = tmp_path / "release.tar.gz"
    with tarfile.open(path, "w:gz") as output:
        item = tarfile.TarInfo(name)
        item.type = kind
        item.size = len(content) if kind == tarfile.REGTYPE else 0
        item.mode = 0o777
        item.linkname = "/etc/hosts"
        output.addfile(item, io.BytesIO(content) if item.size else None)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(path, destination, digest, metadata=None):
    return runpy.run_path(str(SCRIPT))["prepare_source"](
        path, destination, digest, metadata or {"version": "0.2.0", "sha256": digest,
                                              "commit": "a" * 40, "sequence": 100})


def test_verified_copy_is_owned_private_and_carries_the_exact_label(tmp_path):
    path, digest = archive(tmp_path)
    target = tmp_path / "source"
    prepare(path, target, digest)
    assert target.stat().st_mode & 0o777 == 0o700
    module = target / "src/jittersim/updates.py"
    assert module.read_bytes() == b"owned source"
    assert module.stat().st_uid == os.geteuid()
    assert module.stat().st_mode & 0o777 == 0o755, "copied executable source must remain readable by the name service"
    assert not module.stat().st_mode & 0o022
    assert json.loads((target / ".jittersim-release.json").read_text())["sha256"] == digest
    assert not (target / ".archive.tar.gz").exists()


@pytest.mark.parametrize("damage", ["checksum", "symlink", "existing", "parent", "large"])
def test_unsafe_archive_or_destination_never_becomes_executable_source(tmp_path, damage):
    path, digest = archive(tmp_path)
    target = tmp_path / "source"
    if damage == "checksum":
        digest = "0" * 64
    elif damage == "symlink":
        saved = path.with_suffix(".saved")
        path.rename(saved)
        path.symlink_to(saved)
    elif damage == "existing":
        target.mkdir()
        (target / "unrelated").write_text("preserve")
    elif damage == "parent":
        tmp_path.chmod(0o777)
    else:
        with path.open("ab") as output:
            output.truncate(25 * 1024 * 1024 + 1)
    with pytest.raises((OSError, ValueError, RuntimeError)):
        prepare(path, target, digest)
    if damage == "existing":
        assert (target / "unrelated").read_text() == "preserve"
    else:
        assert not target.exists()


@pytest.mark.parametrize("name,kind", [("../escape", tarfile.REGTYPE),
                                     ("src/link", tarfile.SYMTYPE), ("src/hard", tarfile.LNKTYPE),
                                     ("src/device", tarfile.CHRTYPE), ("src/./bad", tarfile.REGTYPE)])
def test_unsafe_archive_members_are_refused_before_any_source_is_written(tmp_path, name, kind):
    path, digest = archive(tmp_path, name, kind)
    target = tmp_path / "source"
    with pytest.raises((OSError, ValueError, RuntimeError)):
        prepare(path, target, digest)
    assert not target.exists()


def test_oversized_archive_metadata_is_refused_before_it_can_be_loaded(tmp_path):
    path = tmp_path / "release.tar.gz"
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as output:
        item = tarfile.TarInfo("src/jittersim/updates.py")
        item.pax_headers = {"comment": "x" * (64 * 1024 + 1)}
        output.addfile(item)
    target = tmp_path / "source"
    with pytest.raises((OSError, ValueError, RuntimeError)):
        prepare(path, target, hashlib.sha256(path.read_bytes()).hexdigest())
    assert not target.exists()


def test_sparse_headers_are_refused_before_tarfile_reads_their_extensions(tmp_path):
    path, digest = archive(tmp_path, "src/sparse", tarfile.GNUTYPE_SPARSE)
    target = tmp_path / "source"
    with patch.object(tarfile.TarInfo, "_proc_sparse", side_effect=AssertionError("sparse processing ran")):
        with pytest.raises(RuntimeError, match="physical"):
            prepare(path, target, digest)
    assert not target.exists()


def test_pax_sparse_maps_are_refused_before_tarfile_processes_them(tmp_path):
    path = tmp_path / "release.tar.gz"
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as output:
        item = tarfile.TarInfo("src/sparse")
        item.pax_headers = {"GNU.sparse.major": "1", "GNU.sparse.minor": "0",
                            "GNU.sparse.realsize": "1000000000", "GNU.sparse.name": "src/sparse"}
        output.addfile(item)
    target = tmp_path / "source"
    with patch.object(tarfile.TarInfo, "_proc_gnusparse_10", side_effect=AssertionError("sparse map ran")):
        with pytest.raises(RuntimeError, match="sparse"):
            prepare(path, target, hashlib.sha256(path.read_bytes()).hexdigest())
    assert not target.exists()

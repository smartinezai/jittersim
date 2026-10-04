"""Root-private installer source comes only from the verified archive copy."""

import hashlib
import gzip
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


@pytest.mark.parametrize("damage", ["large-metadata", "physical-sparse", "pax-sparse"])
def test_raw_bounds_run_before_any_tarfile_member_parser(tmp_path, damage):
    path = tmp_path / "release.tar.gz"
    if damage == "physical-sparse":
        path, _ = archive(tmp_path, "src/sparse", tarfile.GNUTYPE_SPARSE)
    else:
        with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as output:
            item = tarfile.TarInfo("src/member")
            item.pax_headers = {"comment": "x" * (64 * 1024 + 1)} if damage == "large-metadata" else \
                {"GNU.sparse.major": "1", "GNU.sparse.minor": "0"}
            output.addfile(item)
    target = tmp_path / "source"
    # This models a library dispatch change independently of private methods.
    # The physical scan must refuse these bytes before entering any member parser.
    with patch.object(tarfile.TarInfo, "fromtarfile", side_effect=AssertionError("member parser ran")):
        with pytest.raises(RuntimeError):
            prepare(path, target, hashlib.sha256(path.read_bytes()).hexdigest())
    assert not target.exists()


@pytest.mark.parametrize("kind", [tarfile.DIRTYPE, tarfile.AREGTYPE])
def test_directory_payload_cannot_hide_a_sparse_header_from_the_physical_scan(tmp_path, kind):
    path = tmp_path / "release.tar.gz"
    hidden = tarfile.TarInfo("src/sparse")
    hidden.type = tarfile.GNUTYPE_SPARSE
    with tarfile.open(path, "w:gz", format=tarfile.GNU_FORMAT) as output:
        directory = tarfile.TarInfo("src/")
        directory.type = kind
        directory.size = 512
        output.addfile(directory, io.BytesIO(hidden.tobuf(tarfile.GNU_FORMAT)))
    target = tmp_path / "source"
    with patch.object(tarfile.TarInfo, "fromtarfile", side_effect=AssertionError("member parser ran")):
        with pytest.raises(RuntimeError, match="directory"):
            prepare(path, target, hashlib.sha256(path.read_bytes()).hexdigest())
    assert not target.exists()


@pytest.mark.parametrize("format", [tarfile.GNU_FORMAT, tarfile.PAX_FORMAT])
def test_normal_long_names_and_git_metadata_survive_the_physical_scan(tmp_path, format):
    path = tmp_path / "release.tar.gz"
    name = "src/" + "long-name-" * 18 + ".py"
    options = {"pax_headers": {"comment": "a" * 40}} if format == tarfile.PAX_FORMAT else {}
    with tarfile.open(path, "w:gz", format=format, **options) as output:
        item = tarfile.TarInfo(name)
        item.size = len(b"source")
        output.addfile(item, io.BytesIO(b"source"))
    target = tmp_path / "source"
    prepare(path, target, hashlib.sha256(path.read_bytes()).hexdigest())
    assert (target / name).read_bytes() == b"source"


def test_extended_header_recursion_is_bounded_before_the_member_parser(tmp_path):
    path = tmp_path / "release.tar.gz"
    header = tarfile.TarInfo("././@LongLink")
    header.type = tarfile.GNUTYPE_LONGNAME
    header.size = 1
    with gzip.open(path, "wb") as output:
        for _ in range(33):
            output.write(header.tobuf(tarfile.GNU_FORMAT) + bytes(512))
        output.write(tarfile.TarInfo("src/member").tobuf() + bytes(1024))
    target = tmp_path / "source"
    with patch.object(tarfile.TarInfo, "fromtarfile", side_effect=AssertionError("member parser ran")):
        with pytest.raises(RuntimeError, match="metadata"):
            prepare(path, target, hashlib.sha256(path.read_bytes()).hexdigest())
    assert not target.exists()


def test_trailing_compressed_padding_counts_toward_the_expansion_limit(tmp_path):
    path = tmp_path / "release.tar.gz"
    with gzip.open(path, "wb") as output:
        output.write(tarfile.TarInfo("src/member").tobuf() + bytes(16 * 1024))
    target = tmp_path / "source"
    receiver = runpy.run_path(str(SCRIPT))["prepare_source"]
    with patch.dict(receiver.__globals__, {"MAX_SOURCE": 1024, "MAX_ENTRIES": 1}), \
            patch.object(tarfile.TarInfo, "fromtarfile", side_effect=AssertionError("member parser ran")):
        with pytest.raises(RuntimeError, match="expanded"):
            receiver(path, target, hashlib.sha256(path.read_bytes()).hexdigest(),
                     {"version": "0.2.0", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "commit": "a" * 40, "sequence": 100})
    assert not target.exists()


def test_bad_compressed_bytes_return_the_safe_cli_error(tmp_path, capsys):
    path = tmp_path / "release.tar.gz"
    # Gzip header followed by a reserved deflate block type.
    path.write_bytes(b"\x1f\x8b\x08\x00" + bytes(6) + b"\x07")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    target = tmp_path / "source"
    label = {"version": "0.2.0", "sha256": digest, "commit": "a" * 40, "sequence": 100}
    main = runpy.run_path(str(SCRIPT))["main"]
    owner = os.geteuid()
    with patch.object(os, "geteuid", side_effect=[0, owner]), \
            patch.object(main.__globals__["sys"], "argv",
                         [str(SCRIPT), str(path), str(target), digest, json.dumps(label)]):
        assert main() == 1
    assert "The full installer did not run" in capsys.readouterr().err
    assert not target.exists()

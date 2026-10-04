"""Copy and verify first-install source before running anything from its archive."""

import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import time
import zlib
from pathlib import Path, PurePosixPath

MAX_ARCHIVE = 25 * 1024 * 1024
MAX_SOURCE = 256 * 1024 * 1024
MAX_ENTRIES = 20000


def check_archive_headers(archive, check):
    """Bound physical bytes before any version-specific member processing."""
    count, payload_bytes, read_bytes, extensions = 0, 0, 0, 0
    maximum = MAX_SOURCE + MAX_ENTRIES * 1024 + tarfile.RECORDSIZE
    extended = (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)
    allowed = (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE, *extended)
    pax_fields = {b"path", b"mtime", b"atime", b"ctime", b"uid", b"gid",
                  b"uname", b"gname", b"comment"}
    with gzip.open(archive, "rb") as raw:
        def read(size):
            nonlocal read_bytes
            check()
            block = raw.read(size)
            read_bytes += len(block)
            check()
            if read_bytes > maximum:
                raise RuntimeError("the expanded archive is too large")
            return block

        while True:
            header = read(512)
            if len(header) != 512:
                raise RuntimeError("the archive physical header is incomplete")
            if not any(header):
                if read(512) != bytes(512):
                    raise RuntimeError("the archive end marker is invalid")
                while True:
                    padding = read(1024 * 1024)
                    if not padding:
                        return
                    if any(padding):
                        raise RuntimeError("the archive contains data after its end")
            if header[156:157] not in allowed:
                raise RuntimeError("the archive physical header is unsupported")
            # frombuf parses this fixed 512-byte header only. It cannot enter
            # sparse, extended or recursive member processing.
            item = tarfile.TarInfo.frombuf(header, "utf-8", "surrogateescape")
            if item.isdir() and item.size != 0:
                # tarfile does not skip directory payload. Both scans must see
                # the same next physical header before any member processing.
                raise RuntimeError("the archive directory has unsupported payload")
            count += 1
            payload_bytes += item.size
            extensions = extensions + 1 if item.type in extended else 0
            if (count > MAX_ENTRIES or item.size < 0 or payload_bytes > MAX_SOURCE
                    or extensions > 32 or (item.type in extended and item.size > 64 * 1024)):
                raise RuntimeError("the archive metadata is too large")
            padded = (item.size + 511) // 512 * 512
            if item.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE):
                body = read(padded)
                if len(body) != padded:
                    raise RuntimeError("the archive metadata is incomplete")
                body = body[:item.size]
                if b"GNU.sparse" in body:
                    raise RuntimeError("the archive sparse metadata is unsupported")
                position = 0
                while position < len(body):
                    check()
                    separator = body.find(b" ", position, position + 12)
                    digits = body[position:separator] if separator >= 0 else b""
                    if not digits or not digits.isdigit():
                        raise RuntimeError("the archive metadata record is invalid")
                    size = int(digits)
                    end = position + size
                    if size < 5 or end > len(body) or body[end - 1:end] != b"\n":
                        raise RuntimeError("the archive metadata record is invalid")
                    key, equals, value = body[separator + 1:end - 1].partition(b"=")
                    if equals != b"=" or key not in pax_fields or len(value) > 4096:
                        raise RuntimeError("the archive metadata field is unsupported")
                    position = end
            else:
                while padded:
                    block = read(min(padded, 1024 * 1024))
                    if not block:
                        raise RuntimeError("the archive member is incomplete")
                    padded -= len(block)


def prepare_source(archive, destination, digest, metadata):
    archive, destination = Path(archive), Path(destination)
    if (not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(metadata, dict)
            or set(metadata) != {"version", "sha256", "commit", "sequence"}
            or metadata["sha256"] != digest
            or not isinstance(metadata["version"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}", metadata["version"])
            or not isinstance(metadata["commit"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", metadata["commit"])
            or type(metadata["sequence"]) is not int or not 0 < metadata["sequence"] < 2 ** 63):
        raise ValueError("the release label is invalid")
    deadline = time.monotonic() + 120

    def check():
        if time.monotonic() >= deadline:
            raise RuntimeError("the installer source exceeded its time allowance")

    parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    created = False
    try:
        owner = os.fstat(parent)
        if owner.st_uid != os.geteuid() or owner.st_mode & 0o077:
            raise RuntimeError("the installer source parent is not private")
        # Refuse every pre-existing path. Never replace another installer's source.
        os.mkdir(destination.name, 0o700, dir_fd=parent)
        created = True
        copied = destination / ".archive.tar.gz"
        descriptor = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as incoming, copied.open("xb") as output:
            info = os.fstat(incoming.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_ARCHIVE:
                raise RuntimeError("the release archive is unsafe")
            os.fchmod(output.fileno(), 0o600)
            calculated = hashlib.sha256()
            size = 0
            while True:
                check()
                block = incoming.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > MAX_ARCHIVE:
                    raise RuntimeError("the release archive is too large")
                calculated.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if calculated.hexdigest() != digest:
            raise RuntimeError("the release archive checksum changed")
        check_archive_headers(copied, check)
        with tarfile.open(copied, "r:gz") as bundle:
            entries, names, total, folders = [], set(), 0, {destination}
            for item in bundle:
                check()
                name = item.name.rstrip("/") if item.isdir() else item.name
                path = PurePosixPath(name)
                if (len(entries) >= MAX_ENTRIES or not name or len(name) > 4096 or path.is_absolute()
                        or ".." in path.parts or len(path.parts) > 32 or path.as_posix() != name
                        or name in names or not (item.isdir() or item.isfile())
                        or path.parts[0] not in ("src", "deploy", "pyproject.toml", "README.md")
                        or item.size < 0):
                    raise RuntimeError("the release archive contains unsafe source")
                total += item.size
                if total > MAX_SOURCE:
                    raise RuntimeError("the release source is too large")
                names.add(name)
                entries.append((item, path))
                for index in range(1, len(path.parts) + (1 if item.isdir() else 0)):
                    folders.add(destination.joinpath(*path.parts[:index]))
                    if len(folders) > MAX_ENTRIES:
                        raise RuntimeError("the release has too many source folders")
            # Validate every member before writing source. No candidate module is
            # imported while ownership and the checksum are being established.
            for item, relative in entries:
                check()
                target = destination.joinpath(*relative.parts)
                if item.isdir():
                    target.mkdir(mode=0o755, parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                incoming = bundle.extractfile(item)
                with incoming, target.open("xb") as output:
                    remaining = item.size
                    while remaining:
                        check()
                        block = incoming.read(min(1024 * 1024, remaining))
                        if not block:
                            raise RuntimeError("the release source is incomplete")
                        output.write(block)
                        remaining -= len(block)
                    output.flush()
                    os.fchmod(output.fileno(), 0o755 if item.mode & 0o111 else 0o644)
                    os.fsync(output.fileno())
        label = destination / ".jittersim-release.json"
        with label.open("x") as output:
            os.fchmod(output.fileno(), 0o644)
            json.dump(metadata, output)
            output.flush()
            os.fsync(output.fileno())
        copied.unlink()
        for folder in sorted(folders, key=lambda path: len(path.parts), reverse=True):
            check()
            descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        os.fsync(parent)
    except BaseException:
        if created:
            shutil.rmtree(destination)
        raise
    finally:
        os.close(parent)


def main():
    try:
        if os.geteuid() != 0 or len(sys.argv) != 5:
            raise RuntimeError("administrator access and verified source are required")
        prepare_source(sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4]))
        return 0
    except (OSError, EOFError, ValueError, RuntimeError, tarfile.TarError, zlib.error):
        print("Checking the installer source failed. The full installer did not run.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

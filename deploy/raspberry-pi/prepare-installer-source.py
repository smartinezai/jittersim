"""Copy and verify first-install source before running anything from its archive."""

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath

MAX_ARCHIVE = 25 * 1024 * 1024
MAX_SOURCE = 256 * 1024 * 1024
MAX_ENTRIES = 20000


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
    header_count = 0
    header_bytes = 0
    extensions = 0

    def check():
        if time.monotonic() >= deadline:
            raise RuntimeError("the installer source exceeded its time allowance")

    class BoundedInfo(tarfile.TarInfo):
        @classmethod
        def frombuf(cls, buf, encoding, errors):
            nonlocal header_count, header_bytes, extensions
            check()
            item = super().frombuf(buf, encoding, errors)
            if item.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE,
                                 tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                                 tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
                raise RuntimeError("the archive physical header is unsupported")
            header_count += 1
            header_bytes += item.size
            extended = item.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                                     tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)
            extensions = extensions + 1 if extended else 0
            if (header_count > MAX_ENTRIES or header_bytes > MAX_SOURCE or item.size < 0
                    or extensions > 32 or (extended and item.size > 64 * 1024)):
                raise RuntimeError("the archive metadata is too large")
            return item

        def _proc_pax(self, bundle):
            # PAX can also describe GNU sparse maps on an ordinary file header.
            # Reject those before the library enters its sparse-data parser.
            check()
            position = bundle.fileobj.tell()
            metadata = bundle.fileobj.read(self._block(self.size))
            check()
            if b"GNU.sparse" in metadata:
                raise RuntimeError("the archive sparse metadata is unsupported")
            bundle.fileobj.seek(position)
            return super()._proc_pax(bundle)

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
        with tarfile.open(copied, "r:gz", tarinfo=BoundedInfo) as bundle:
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
    except (OSError, ValueError, RuntimeError, tarfile.TarError):
        print("Checking the installer source failed. The full installer did not run.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

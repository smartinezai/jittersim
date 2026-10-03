#!/usr/bin/env python3
"""Replace release folders without retaining files from older packages."""

import shutil
import tempfile
from pathlib import Path

# The release label written by the update service or by deploy-from-mac.sh. An installed
# label that the new source does not carry is removed so `jittersim update check` never
# names a release that is no longer installed.
RELEASE_RECORD = ".jittersim-release.json"


def replace_source(source, installed):
    if source.resolve() == installed.resolve():
        if (installed / "build").exists():
            shutil.rmtree(installed / "build")
        return
    # Prepare every replacement before touching any installed source folder.
    prepared = Path(tempfile.mkdtemp(prefix=".release-", dir=installed))
    keep_recovery = False
    try:
        for name in ("src", "deploy"):
            shutil.copytree(source / name, prepared / name)
        for name in ("pyproject.toml", "README.md"):
            shutil.copyfile(source / name, prepared / name)
        if (source / RELEASE_RECORD).is_file():
            shutil.copyfile(source / RELEASE_RECORD, prepared / RELEASE_RECORD)
        previous = prepared / "previous"
        previous.mkdir()
        changed = []
        try:
            # Keep the old folders until all replacements succeed. An error
            # restores them instead of leaving a mixture of two releases.
            for name in ("src", "deploy", "pyproject.toml", "README.md", "build", RELEASE_RECORD):
                destination = installed / name
                if destination.exists():
                    destination.rename(previous / name)
                changed.append(name)
                if (prepared / name).exists():
                    (prepared / name).rename(destination)
        except Exception:
            try:
                for name in reversed(changed):
                    destination = installed / name
                    if destination.is_dir():
                        shutil.rmtree(destination)
                    elif destination.exists():
                        destination.unlink()
                    if (previous / name).exists():
                        (previous / name).rename(destination)
            except Exception as error:
                keep_recovery = True
                raise RuntimeError("Source recovery failed. Old folders are kept at "
                                   + str(previous)) from error
            raise
    finally:
        if not keep_recovery:
            shutil.rmtree(prepared)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("installed", type=Path)
    args = parser.parse_args()
    replace_source(args.source, args.installed)

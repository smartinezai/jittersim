"""The old arbitrary-reference publisher cannot bypass exact package approval."""

from pathlib import Path
import os
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/raspberry-pi/publish-release.sh"


@pytest.mark.parametrize("arguments", [[], ["--dry-run", "HEAD"], ["--signing-key", "key", "HEAD"],
                                      ["--allow-dirty", "HEAD"], ["--tag", "v0.2.0", "HEAD"]])
def test_legacy_publication_arguments_are_refused(tmp_path, arguments):
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\ntouch \"$GH_WAS_CALLED\"\n")
    fake.chmod(0o755)
    log = tmp_path / "called"
    result = subprocess.run(["sh", str(SCRIPT), *arguments], capture_output=True, text=True,
                            env={**os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"],
                                 "GH_WAS_CALLED": str(log)}, timeout=10)
    assert result.returncode == 2
    assert not log.exists()

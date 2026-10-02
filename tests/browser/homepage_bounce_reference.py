"""Exercise the isolated extension through actual Chromium navigation."""

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path


def run():
    root = Path(__file__).resolve().parents[2]
    source = root / "experiments/homepage-bounce-reference"
    spec = importlib.util.spec_from_file_location(
        "homepage_reference_fixture", source / "fixture.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    node = os.environ.get("JITTERSIM_NODE_EXECUTABLE") or shutil.which("node")
    if not node or not shutil.which(node):
        raise RuntimeError("Set JITTERSIM_NODE_EXECUTABLE to the existing Node runtime")
    fixture = module.Fixture()
    with tempfile.TemporaryDirectory(prefix="jittersim-homepage-reference-") as temporary:
        directory = Path(temporary)
        extension = directory / "extension"
        shutil.copytree(source / "extension", extension)
        (extension / "runtime-config.js").write_text(
            "globalThis.REFERENCE_CONFIG = " + json.dumps({"origin": fixture.origin}) + ";\n"
        )
        environment = {
            **os.environ,
            "HOMEPAGE_REFERENCE_ORIGIN": fixture.origin,
            "HOMEPAGE_REFERENCE_EXTENSION": str(extension),
            "HOMEPAGE_REFERENCE_PROFILE": str(directory / "browser-profile"),
            "HOMEPAGE_REFERENCE_HARNESS": fixture.harness_key,
            "HOMEPAGE_REFERENCE_CODE": fixture.code,
        }
        fixture.start()
        try:
            process = subprocess.Popen(
                [node, str(source / "journey.cjs")], env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=75)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=3)
                raise RuntimeError("Reference journey exceeded its deadline") from None
            print(stdout, end="")
            if process.returncode:
                raise RuntimeError(stderr.strip() or "Reference journey failed")
        finally:
            fixture.close()


if __name__ == "__main__":
    run()

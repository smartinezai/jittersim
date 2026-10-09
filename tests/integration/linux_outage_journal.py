"""Two-phase reboot proof. Run only inside an explicitly disposable Linux VM."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from jittersim import diagnostic_export, diagnostic_privacy, diagnostic_records, journal_policy

STATE = Path("/var/lib/jittersim-outage-proof.json")
REBOOT_RESULT = Path("/var/lib/jittersim-outage-proof-reboot.json")
MODULES = (diagnostic_records, diagnostic_export, diagnostic_privacy, journal_policy)


def _require_disposable():
    if (os.geteuid() != 0 or not sys.platform.startswith("linux")
            or os.environ.get("JITTERSIM_DISPOSABLE_JOURNAL_PROOF") != "yes"):
        raise RuntimeError("This proof requires root inside an explicitly disposable Linux VM.")
    # A privileged container may share its enclosing VM's kernel clock.
    for option, expected in (("--container", 1), ("--vm", 0)):
        result = subprocess.run(["/usr/bin/systemd-detect-virt", option, "--quiet"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=2, check=False)
        if result.returncode != expected:
            raise RuntimeError("An actual disposable VM is required. Containers and host systems are refused.")
    if not Path("/run/systemd/system").is_dir():
        raise RuntimeError("The VM must run systemd and journald.")


def _run(arguments, timeout=10):
    result = subprocess.run(arguments, stdin=subprocess.DEVNULL, capture_output=True,
                            timeout=timeout, check=False)
    if result.returncode or len(result.stdout) > 4096 or len(result.stderr) > 4096:
        raise RuntimeError("A fixed proof command failed.")
    return result.stdout


def _identity():
    return {Path(module.__file__).name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in MODULES}


def prepare(source):
    _require_disposable()
    journal_policy.check_runtime()
    journal_policy.install()
    operation = diagnostic_records.Operation("apply", "helper", configuration_version=1)
    operation.finish("succeeded")
    if operation.event is None:
        raise RuntimeError("The safe marker could not be created.")
    _run(["/usr/bin/journalctl", "--sync"])
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")
    payload = diagnostic_export.build_export()
    entries = json.loads(payload)["entries"]
    wanted = operation.event["operation"]
    phases = {e["event"].get("phase") for e in entries if e["event"].get("operation") == wanted}
    if phases != {"start", "outcome"}:
        raise RuntimeError("Run prepare through systemd-run --unit=jittersim-helper inside the disposable VM.")
    state = {"source": source, "boot": boot, "operation": wanted, "modules": _identity(),
             "systemd": _run(["/usr/bin/systemctl", "--version"]).decode().splitlines()[0]}
    journal_policy._atomic(STATE, json.dumps(state).encode())
    print(json.dumps({"phase": "prepared", **state}, sort_keys=True))


def verify(source):
    _require_disposable()
    state = json.loads(journal_policy._read(STATE, 4096))
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")
    if boot == state["boot"]:
        raise RuntimeError("The boot identity did not change. A process or container restart is insufficient.")
    if source != state["source"] or _identity() != state["modules"]:
        raise RuntimeError("The proof source changed across the VM reboot.")
    payload = diagnostic_export.build_export()
    entries = json.loads(payload)["entries"]
    found = [e for e in entries if e["boot"] == state["boot"]
             and e["event"].get("operation") == state["operation"]]
    if {e["event"].get("phase") for e in found} != {"start", "outcome"}:
        raise RuntimeError("The previous-boot safe marker did not survive.")
    if len(payload) > diagnostic_export.MAX_BYTES:
        raise RuntimeError("The export size limit was exceeded.")
    result = {"phase": "verified-after-reboot", "source": source,
              "previous_boot": state["boot"], "current_boot": boot,
              "export_bytes": len(payload), "systemd": state["systemd"], "modules": state["modules"]}
    journal_policy._atomic(REBOOT_RESULT, json.dumps(result).encode())
    journal_policy._atomic(REBOOT_RESULT.with_suffix(".export.json"), payload)
    print(json.dumps(result, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "verify"))
    parser.add_argument("--source", required=True)
    arguments = parser.parse_args()
    if not re.fullmatch("[a-f0-9]{40}", arguments.source):
        raise RuntimeError("Use the exact feature commit identity.")
    (prepare if arguments.phase == "prepare" else verify)(arguments.source)


if __name__ == "__main__":
    main()

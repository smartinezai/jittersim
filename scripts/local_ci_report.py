#!/usr/bin/env python3
"""Summarise one scripts/local-ci.sh run.

Usage:
    local_ci_report.py summary RUN_DIR    write summary.md and junit.xml and print the summary
    local_ci_report.py failed RUN_DIR     print what to re-run alone, one target per line

RUN_DIR holds `meta.tsv` (key and value per line) and `steps.tsv` (one step per line with the
columns id, result, exit, seconds, log, junit, command, note). Only the Python standard library
is used. Steps whose result is ADVISORY, INSTALLED or UNAVAILABLE report counts and never gate.
"""

import collections
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ADVISORY_RESULTS = ("ADVISORY", "INSTALLED", "UNAVAILABLE")
HOSTED_NOTE = (
    "Hosted GitHub Actions did not run (the account cannot start jobs). "
    "These are local results only."
)


def read_meta(run_dir):
    meta = collections.OrderedDict()
    path = Path(run_dir) / "meta.tsv"
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition("\t")
            if key:
                meta[key] = value
    return meta


def read_steps(run_dir):
    steps = []
    path = Path(run_dir) / "steps.tsv"
    if not path.exists():
        return steps
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        columns = (line.split("\t") + [""] * 8)[:8]
        step_id, result, exit_code, seconds, log, junit, command, note = columns
        steps.append({
            "id": step_id, "result": result, "exit": exit_code, "seconds": seconds,
            "log": log, "junit": junit, "command": command, "note": note,
        })
    return steps


def junit_counts(path):
    """Return (tests, failures, errors, skipped, skip_reasons) for one pytest junit file."""

    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    tests = failures = errors = skipped = 0
    reasons = collections.Counter()
    for case in root.iter("testcase"):
        tests += 1
        if case.find("failure") is not None:
            failures += 1
        if case.find("error") is not None:
            errors += 1
        skip = case.find("skipped")
        if skip is not None:
            skipped += 1
            reasons[(skip.get("message") or "no reason given").strip().splitlines()[0][:110]] += 1
    return tests, failures, errors, skipped, reasons


def describe_cases(step, run_dir):
    if step["result"] in ADVISORY_RESULTS:
        return step["note"] or "-", None
    if step["junit"] not in ("", "-"):
        counts = junit_counts(Path(run_dir) / step["junit"])
        if counts is None:
            return "no junit file", None
        tests, failures, errors, skipped, reasons = counts
        passed = tests - failures - errors - skipped
        return "%d passed, %d failed, %d errors, %d skipped" % (passed, failures, errors, skipped), reasons
    if step["result"] == "PASS":
        return "1 passed", None
    if step["result"] == "NOT RUN":
        return "not run", None
    return "1 failed", None


def table(steps, run_dir):
    rows = ["| Step | Result | Exit | Seconds | Cases | Log |", "|---|---|---|---|---|---|"]
    skip_reasons = collections.Counter()
    for step in steps:
        cases, reasons = describe_cases(step, run_dir)
        if reasons:
            skip_reasons.update(reasons)
        rows.append("| %s | %s | %s | %s | %s | %s |" % (
            step["id"], step["result"], step["exit"], step["seconds"], cases, step["log"] or "-"))
    return rows, skip_reasons


def build_summary(run_dir):
    run_dir = Path(run_dir)
    meta = read_meta(run_dir)
    steps = read_steps(run_dir)
    rows, skip_reasons = table(steps, run_dir)
    gating = [step for step in steps if step["result"] not in ADVISORY_RESULTS]
    advisory = [step for step in steps if step["result"] in ADVISORY_RESULTS]
    counts = collections.Counter(step["result"] for step in gating)
    lines = ["## Local CI run %s" % meta.get("run_id", run_dir.name), ""]
    labels = [
        ("commit", "Commit"), ("tree", "Tree"), ("working_tree", "Working tree"),
        ("base", "Base"), ("command", "Command"), ("scope", "Scope"), ("host", "Host"),
        ("tools", "Tools"), ("python_note", "Python"), ("docker", "Docker"),
        ("started", "Started"), ("finished", "Finished"), ("results", "Results"),
    ]
    for key, label in labels:
        if meta.get(key):
            lines.append("- %s: %s" % (label, meta[key]))
    lines.append("- Hosted CI: %s" % HOSTED_NOTE)
    lines.append("")
    lines.extend(rows)
    lines.append("")
    total = len(gating)
    failed = total - counts.get("PASS", 0)
    lines.append("Totals: %d steps, %d passed, %d not passed (%s)." % (
        total, counts.get("PASS", 0), failed,
        ", ".join("%d %s" % (count, name.lower()) for name, count in sorted(counts.items())
                  if name != "PASS") or "none"))
    if advisory:
        lines.append("Advisory steps (they never gate and are not counted above): %d." % len(advisory))
    if skip_reasons:
        lines.extend(["", "Skipped cases by reason:", ""])
        for reason, count in skip_reasons.most_common():
            lines.append("- %d: %s" % (count, reason))
    if meta.get("warnings"):
        lines.extend(["", "Warnings:", ""])
        lines.extend("- %s" % item for item in meta["warnings"].split(" || ") if item)
    return "\n".join(lines) + "\n", steps


def write_junit(run_dir, steps, meta):
    passing = ("PASS", "ADVISORY", "INSTALLED")
    suite = ET.Element("testsuite", {
        "name": "local-ci", "tests": str(len(steps)),
        "failures": str(sum(1 for step in steps
                            if step["result"] not in passing + ("NOT RUN", "UNAVAILABLE"))),
        "skipped": str(sum(1 for step in steps if step["result"] in ("NOT RUN", "UNAVAILABLE"))),
    })
    properties = ET.SubElement(suite, "properties")
    for key in ("commit", "tree", "scope"):
        if meta.get(key):
            ET.SubElement(properties, "property", {"name": key, "value": meta[key]})
    for step in steps:
        case = ET.SubElement(suite, "testcase", {
            "classname": "local-ci", "name": step["id"], "time": step["seconds"] or "0"})
        if step["result"] == "NOT RUN":
            ET.SubElement(case, "skipped", {"message": "not run"})
        elif step["result"] == "UNAVAILABLE":
            ET.SubElement(case, "skipped", {"message": step["note"] or "tool not available"})
        elif step["result"] in ("ADVISORY", "INSTALLED"):
            ET.SubElement(case, "system-out").text = step["note"]
        elif step["result"] != "PASS":
            failure = ET.SubElement(case, "failure", {
                "message": "%s with exit status %s" % (step["result"], step["exit"])})
            failure.text = step["command"]
    ET.ElementTree(suite).write(Path(run_dir) / "junit.xml", encoding="utf-8", xml_declaration=True)


def pytest_node_id(classname, name, root):
    """Turn a junit class name and test name into a pytest node id that exists on disk."""

    parts = classname.split(".")
    for end in range(len(parts), 0, -1):
        candidate = Path(root, *parts[:end]).with_suffix(".py")
        if candidate.is_file():
            relative = "/".join(parts[:end]) + ".py"
            return "::".join([relative] + parts[end:] + [name])
    return None


def failed_targets(run_dir, root="."):
    targets = []
    for step in read_steps(run_dir):
        if step["result"] in ("PASS", "NOT RUN") + ADVISORY_RESULTS:
            continue
        if step["junit"] not in ("", "-"):
            try:
                tree = ET.parse(Path(run_dir) / step["junit"])
            except (OSError, ET.ParseError):
                continue
            for case in tree.getroot().iter("testcase"):
                if case.find("failure") is not None or case.find("error") is not None:
                    node = pytest_node_id(case.get("classname", ""), case.get("name", ""), root)
                    if node and node not in targets:
                        targets.append(node)
        elif step["id"].startswith("browser:"):
            targets.append(step["id"])
    return targets


def main(argv):
    if len(argv) != 2 or argv[0] not in ("summary", "failed"):
        print(__doc__, file=sys.stderr)
        return 2
    action, run_dir = argv
    if action == "failed":
        for target in failed_targets(run_dir):
            print(target)
        return 0
    text, steps = build_summary(run_dir)
    (Path(run_dir) / "summary.md").write_text(text, encoding="utf-8")
    write_junit(run_dir, steps, read_meta(run_dir))
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

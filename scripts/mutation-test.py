#!/usr/bin/env python3
"""Run mutation tests on one pure-logic module with mutmut, in a scratch copy.

Mutation testing changes the source a little (a `<` becomes `<=`, a constant moves,
a branch is dropped) and runs the tests. A test suite that still passes has a gap:
the changed behaviour is not pinned by any assertion. See docs/mutation-testing-2026-10-18.md.

The tool is mutmut (https://github.com/boxed/mutmut). It needs Python 3.10 or newer and
lives in a separate tools venv, never in the project dependencies. It mutates a COPY of the
source under the work directory. This script never writes to the checkout, so hash-pinned
files can be mutated safely and mutated source can never be committed by accident.

    scripts/mutation-test.py setup                # create the tools venv once
    scripts/mutation-test.py list                 # the modules and their test selection
    scripts/mutation-test.py run codec            # copy, generate mutants, run them
    scripts/mutation-test.py summary codec        # kill rate of the last run
    scripts/mutation-test.py survivors codec      # diff of every surviving mutant

Environment: JITTERSIM_MUTATION_DIR (work directory, default /tmp/jittersim-mutation) and
JITTERSIM_MUTATION_VENV (tools venv, default /tmp/jittersim-mutation-venv).
"""

import argparse
import ast
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = Path(os.environ.get("JITTERSIM_MUTATION_DIR", "/tmp/jittersim-mutation"))
VENV = Path(os.environ.get("JITTERSIM_MUTATION_VENV", "/tmp/jittersim-mutation-venv"))
MUTMUT_VERSION = "mutmut==3.8.0"
TEST_REQUIREMENTS = (
    "pytest>=8,<9", "hypothesis>=6,<7", "pycryptodome>=3.14,<4", "requests>=2.25,<3",
)

# name -> source file to mutate, tests that run during stats collection, and optionally the
# functions to mutate when the file is large. mutmut runs, for each mutant, only the tests that
# reach the mutated function, so a wide test list costs little per mutant.
MODULES = {
    "deadlines": {
        "source": "src/jittersim/networking/deadlines.py",
        "tests": ["tests/unit/test_deadlines.py", "tests/unit/test_panic_time_budget.py",
                  "tests/unit/test_helper_total_deadlines.py", "tests/unit/test_executor.py"],
    },
    "compiler": {
        "source": "src/jittersim/engine/compiler.py",
        "tests": ["tests/unit/test_compiler.py", "tests/unit/test_general_compilation.py",
                  "tests/unit/test_service_list_plan.py", "tests/unit/test_service_list_engine.py",
                  "tests/unit/test_service_addresses.py", "tests/unit/test_conditions.py",
                  "tests/unit/test_changing_profiles.py", "tests/qa/test_compiler_properties.py"],
    },
    "codec": {
        "source": "src/jittersim/engine/codec.py",
        "tests": ["tests/unit/test_codec.py", "tests/unit/test_codec_contract.py",
                  "tests/unit/test_service_list_variation.py",
                  "tests/unit/test_random_value_ranges.py"],
    },
    "models": {
        "source": "src/jittersim/config/models.py",
        "tests": ["tests/unit/test_models.py", "tests/unit/test_models_limits.py",
                  "tests/unit/test_random_value_ranges.py", "tests/unit/test_schedules.py",
                  "tests/unit/test_domain_labels.py"],
    },
    "renderer": {
        "source": "src/jittersim/networking/renderer.py",
        "tests": ["tests/unit/test_renderer.py", "tests/unit/test_renderer_golden.py",
                  "tests/unit/test_service_list_renderer.py",
                  "tests/unit/test_random_value_ranges.py",
                  "tests/unit/test_service_list_variation.py"],
    },
    "updates": {
        "source": "src/jittersim/updates.py",
        "tests": ["tests/unit/test_updates.py", "tests/unit/test_signed_releases.py",
                  "tests/unit/test_release_versions.py", "tests/unit/test_signed_version_labels.py",
                  "tests/unit/test_stable_releases.py", "tests/unit/test_online_release.py",
                  "tests/unit/test_online_api.py", "tests/unit/test_update_preparation.py",
                  "tests/unit/test_trust_downloads.py", "tests/unit/test_update_decisions.py"],
        # The mutated module is much bigger than the real one. These size limits are about the
        # module's own file (the installer refuses a manager over 512 KiB), so they are raised
        # in the scratch copy. No function below reads them.
        "loosen": [("512 * 1024", "64 * 1024 * 1024"),
                   ("maximum=1024 * 1024", "maximum=64 * 1024 * 1024")],
        # Version order, signature and manifest checks, the sequence floor and key parsing.
        "functions": [
            "version_key", "validate_archive", "key_fingerprint", "_check_key_text",
            "parse_public_key", "_signer_lines", "_signature_fields", "_is_sequence",
            "read_manifest", "_record", "release_floor", "_raise_floor", "staged_problem",
            "_parse_release", "_is_id", "verify_signature", "trusted_keys", "_check_trust_file",
        ],
    },
    "storage": {
        "source": "src/jittersim/config/storage.py",
        "tests": ["tests/unit/test_storage.py", "tests/unit/test_merged_upgrade.py",
                  "tests/unit/test_upgrade_compatibility.py", "tests/unit/test_transfer.py",
                  "tests/unit/test_transfer_pi_settings.py", "tests/unit/test_protection.py",
                  "tests/unit/test_storage_migrations.py"],
        # Row conversion and the three schema migrations.
        "functions": [
            "_serialize_impairment", "_deserialize_randomness", "_deserialize_impairment",
            "_serialize_profile_settings", "_deserialize_profile_settings",
            "ConfigRepository._upgrade_schedules_table",
            "ConfigRepository._migrate_domain_observations",
            "ConfigRepository._migrate_unaffected_devices",
            "ConfigRepository._record_domain_list_cleared",
            "ConfigRepository._add_column_if_missing",
        ],
    },
    "dashboard_rules": {
        "source": "src/jittersim/dashboard_rules.py",
        "tests": ["tests/unit/test_dashboard_rules_parity.py",
                  "tests/unit/test_dashboard_rules_direct.py"],
    },
}


def run(command, **kwargs):
    print("+", " ".join(str(part) for part in command), flush=True)
    return subprocess.run(command, check=False, **kwargs)


# Folders and files that some tests read from the repository root. mutmut copies them next to
# the mutated source, because the tests find the repository from their own location.
EXTRAS = ("deploy", "scripts", "docs", "extensions", "experiments")


def config_text(spec):
    tests = [test for test in spec["tests"] if (ROOT / test).exists()]
    extras = [name for name in EXTRAS if (ROOT / name).exists()]
    extras += sorted(path.name for path in ROOT.glob("*.md"))

    def quoted(values):
        return "[" + ", ".join(json.dumps(value) for value in values) + "]"

    return "\n".join([
        "",
        "[tool.mutmut]",
        'source_paths = ["src"]',
        f"also_copy = {quoted(extras)}",
        f"only_mutate = {quoted([spec['source']])}",
        f"pytest_add_cli_args_test_selection = {quoted(tests)}",
        'pytest_add_cli_args = ["-p", "no:cacheprovider"]',
        "use_git_change_detection = false",
        "track_dependencies = false",
        "",
    ])


PRAGMA = "  # pragma: no mutate block"


def header_end_line(lines, node):
    """The line of the colon that ends the header of a def or class statement."""

    depth = 0
    readline = iter(line + "\n" for line in lines[node.lineno - 1:]).__next__
    for token in tokenize.generate_tokens(readline):
        if token.type == tokenize.OP:
            if token.string in "([{":
                depth += 1
            elif token.string in ")]}":
                depth -= 1
            elif token.string == ":" and depth == 0:
                return node.lineno + token.start[0] - 1
    raise ValueError(f"no header colon after line {node.lineno}")


def limit_to_functions(text, keep):
    """Tell mutmut to leave every function and class alone except the ones named in KEEP.

    A name is `function` or `Class.method`. A big module such as updates.py would otherwise
    grow to many times its size, which breaks tests that read the module itself.
    """

    lines = text.splitlines()
    marks = set()

    def skip(node):
        marks.add(header_end_line(lines, node))

    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name not in keep:
                skip(node)
        elif isinstance(node, ast.ClassDef):
            wanted = [name for name in keep if name.startswith(node.name + ".")]
            if not wanted:
                skip(node)
                continue
            for item in node.body:
                if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and f"{node.name}.{item.name}" not in keep):
                    skip(item)
    for number in marks:
        lines[number - 1] += PRAGMA
    return "\n".join(lines) + "\n"


def prepare(name, spec, fresh):
    target = WORK / name
    if fresh and target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", "browser", "kernel")
    for folder in ("src", "tests") + EXTRAS:
        if not (ROOT / folder).exists():
            continue
        destination = target / folder
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(ROOT / folder, destination, ignore=ignore)
    for document in ROOT.glob("*.md"):
        shutil.copy2(document, target / document.name)
    source = target / spec["source"]
    text = source.read_text(encoding="utf-8")
    for old, new in spec.get("loosen", ()):
        if old not in text:
            raise SystemExit(f"loosen: {old!r} is not in {spec['source']}")
        text = text.replace(old, new)
    if spec.get("functions"):
        text = limit_to_functions(text, spec["functions"])
    source.write_text(text, encoding="utf-8")
    (target / "pyproject.toml").write_text(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8") + config_text(spec),
        encoding="utf-8",
    )
    return target


def mutmut(*arguments):
    return [str(VENV / "bin" / "mutmut"), *arguments]


def command_setup(args):
    python = args.python
    if run([python, "-m", "venv", str(VENV)]).returncode:
        return 1
    pip = [str(VENV / "bin" / "python"), "-m", "pip", "install", "--quiet"]
    return run([*pip, MUTMUT_VERSION, *TEST_REQUIREMENTS]).returncode


def command_list(_args):
    for name, spec in MODULES.items():
        print(f"{name}: {spec['source']}\n    tests: {' '.join(spec['tests'])}")
        if spec.get("functions"):
            print(f"    only: {' '.join(spec['functions'])}")
    return 0


def command_run(args):
    spec = MODULES[args.module]
    target = prepare(args.module, spec, fresh=not args.resume)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    names = []
    log = target / "mutmut-run.log"
    command = mutmut("run", "--max-children", str(args.max_children), *names)
    print("+", " ".join(command), f"> {log}", flush=True)
    with log.open("w", encoding="utf-8") as output:
        result = subprocess.run(
            command, cwd=target, env=env, stdout=output, stderr=subprocess.STDOUT, check=False
        )
    print(f"mutmut exit {result.returncode}; work directory {target}")
    return result.returncode


DUMP = """
import json, sys
from mutmut.mutation.data import SourceFileMutationData
from mutmut.mutation.diff_apply import get_diff_for_mutant
from mutmut.stats import status_by_exit_code
from mutmut.utils.file_utils import walk_mutatable_files
survivors = ("survived", "no tests", "suspicious")
out = {}
for path in walk_mutatable_files():
    data = SourceFileMutationData(path=path)
    data.load()
    for name, code in data.exit_code_by_key.items():
        status = status_by_exit_code[code]
        out[name] = [status, get_diff_for_mutant(name, path=data.path)
                     if status in survivors else None]
json.dump(out, sys.stdout)
"""


def read_results(module):
    """Name -> (status, diff of a surviving mutant or None), read in one mutmut process."""
    completed = subprocess.run(
        [str(VENV / "bin" / "python"), "-c", DUMP], cwd=WORK / module, capture_output=True,
        text=True, check=True,
    )
    return {name: (status, diff) for name, (status, diff) in json.loads(completed.stdout).items()}


SURVIVING = ("survived", "no tests", "suspicious")


def diff_lines(text):
    """The removed and the added source lines of one `mutmut show` diff."""
    removed, added = [], []
    for line in text.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    return removed, added


LITERAL = re.compile(r"""[rRbBfF]*(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""")
LABEL_CALL = re.compile(r"\b_(?:as|validate)_\w+\(")


def is_message_text(source_lines, raise_spans, removed, added):
    """True when the mutant only changed text that a person reads in an error.

    That is a string (or a whole f-string replaced by None) inside a `raise` statement, or
    the field label that `_as_*` and `_validate_*` helpers print in their error. Such a mutant
    is "XX" around the words, the words in another case or no words. It changes what an error
    says and nothing that the program decides.
    """
    if len(removed) != 1 or len(added) != 1:
        return False
    old, new = removed[0], added[0]
    spans = [match.span() for match in LITERAL.finditer(old)]
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    changes = [(i1, i2) for tag, i1, i2, _, _ in matcher.get_opcodes() if tag != "equal"]
    if not changes or not spans:
        return False
    for first, last in changes:
        if not any(a <= first and last <= b and (first < last or a < first < b)
                   for a, b in spans):
            return False
    wanted = old.strip()
    in_raise = any(
        line.strip() == wanted and any(a <= number <= b for a, b in raise_spans)
        for number, line in enumerate(source_lines, start=1)
    )
    return in_raise or bool(LABEL_CALL.search(old))


def classify(module):
    """Name -> (status, category, diff). Category is logic or message."""
    source = ROOT / MODULES[module]["source"]
    text = source.read_text(encoding="utf-8")
    spans = [
        (node.lineno, node.end_lineno)
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Raise)
    ]
    result = {}
    for name, (status, diff) in read_results(module).items():
        category = "logic"
        if status in SURVIVING:
            removed, added = diff_lines(diff)
            if is_message_text(text.splitlines(), spans, removed, added):
                category = "message"
        result[name] = (status, category, diff)
    return result


def command_summary(args):
    rows = {name: row for name, row in classify(args.module).items()
            if row[0] != "not checked"}
    counts = {}
    for status, category, _ in rows.values():
        key = status if status not in SURVIVING else f"{status} ({category})"
        counts[key] = counts.get(key, 0) + 1
    killed = sum(1 for status, _, _ in rows.values()
                 if status in ("killed", "timeout", "segfault"))
    logic = sum(1 for status, category, _ in rows.values()
                if status in SURVIVING and category == "logic")
    message = sum(1 for status, category, _ in rows.values()
                  if status in SURVIVING and category == "message")
    print(f"{args.module}: {len(rows)} mutants, {dict(sorted(counts.items()))}")
    for label, judged in (("all mutants", killed + logic + message),
                          ("without error-text mutants", killed + logic)):
        rate = 100.0 * killed / judged if judged else 100.0
        print(f"{args.module}: kill rate {rate:.1f} percent of {label} ({killed} of {judged})")
    return 0


def command_survivors(args):
    for name, (status, category, diff) in sorted(classify(args.module).items()):
        if status in SURVIVING:
            print(f"### {name}: {status} [{category}]")
            print(diff.rstrip())
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="create the tools venv with mutmut")
    setup.add_argument("--python", default="python3.12", help="Python 3.10 or newer")
    setup.set_defaults(handler=command_setup)
    sub.add_parser("list", help="show the modules").set_defaults(handler=command_list)
    run_parser = sub.add_parser("run", help="generate and run the mutants of one module")
    run_parser.add_argument("module", choices=sorted(MODULES))
    run_parser.add_argument("--max-children", type=int, default=2)
    run_parser.add_argument("--resume", action="store_true", help="keep the earlier results")
    run_parser.set_defaults(handler=command_run)
    for name, handler in (("summary", command_summary), ("survivors", command_survivors)):
        item = sub.add_parser(name)
        item.add_argument("module", choices=sorted(MODULES))
        item.set_defaults(handler=handler)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())

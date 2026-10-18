"""The local CI runner must run what the workflows run, and its helpers must behave.

Hosted CI cannot start jobs, so scripts/local-ci.sh is what gates a pull request. These tests
fail when a workflow gains a browser module, a command or a version pin that the script does not
copy, so the two cannot drift apart without anyone noticing.
"""

import importlib.util
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
SCRIPT = SCRIPTS / "local-ci.sh"
WORKFLOWS = ROOT / ".github" / "workflows"


def load_helper(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow_lines(name):
    return [line.strip() for line in (WORKFLOWS / name).read_text().splitlines()]


def workflow_commands(name):
    """The text after `- run:` for every step of a workflow, in order."""

    return [line[len("- run: "):] for line in workflow_lines(name) if line.startswith("- run: ")]


def script_text():
    return SCRIPT.read_text()


def script_array(name):
    """The words or quoted strings of a bash array in the script, comments removed."""

    match = re.search(r"^%s=\(\n(.*?)\n\)" % re.escape(name), script_text(), re.S | re.M)
    assert match, "the script has no array named %s" % name
    items = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        items.append(line[1:-1] if line.startswith('"') and line.endswith('"') else line)
    return items


def script_value(name):
    match = re.search(r"^%s=(\S+)$" % re.escape(name), script_text(), re.M)
    assert match, "the script has no value named %s" % name
    return match.group(1)


def browser_modules(commands):
    return [match.group(1) for command in commands
            for match in [re.fullmatch(r"python -m tests\.browser\.(\w+)", command)] if match]


def run_script(*arguments, cwd=ROOT, script=SCRIPT, path_first=()):
    environment = dict(os.environ)
    environment["PATH"] = os.pathsep.join(
        [str(directory) for directory in path_first] + [str(Path(sys.executable).parent), environment["PATH"]])
    environment.pop("JITTERSIM_CI_REEXEC", None)
    return subprocess.run(
        ["bash", str(script)] + list(arguments), cwd=str(cwd), env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=120)


# -- the script and the workflows ----------------------------------------------------------


def test_pi_browser_modules_match_the_workflow_in_order():
    assert script_array("PI_BROWSER_MODULES") == browser_modules(workflow_commands("wifi-recovery.yml"))


def test_tests_workflow_modules_match_the_script():
    workflow = browser_modules(workflow_commands("tests.yml"))
    ours = [script_value("JOURNEYS_MODULE")] + script_array("GUI_MODULES") + [script_value("HOMEPAGE_MODULE")]
    assert sorted(workflow) == sorted(ours)


def test_gui_and_homepage_pytest_files_match_the_workflow():
    commands = workflow_commands("tests.yml")
    gui = [c for c in commands if c.startswith("python -m pytest tests/unit/test_gui")]
    assert gui == ["python -m pytest " + " ".join(script_array("GUI_PYTEST_FILES"))]
    assert "python -m pytest %s" % script_value("HOMEPAGE_PYTEST_FILE") in commands


def test_node_commands_and_prefix_match_the_workflow():
    commands = workflow_commands("tests.yml")
    for script in script_array("HOMEPAGE_NODE_SCRIPTS"):
        assert "node " + script in commands
    prefix = script_value("HOMEPAGE_NODE_PREFIX")
    assert "npm ci --ignore-scripts --prefix " + prefix in commands


def test_kernel_script_matches_the_workflow():
    assert "./" + script_value("KERNEL_SCRIPT") in workflow_commands("tests.yml")
    assert (ROOT / script_value("KERNEL_SCRIPT")).is_file()


def test_release_docker_runs_match_the_workflow_in_order():
    workflow = [c[len("docker run "):] for c in workflow_commands("wifi-recovery.yml")
                if c.startswith("docker run ")]
    assert script_array("RELEASE_DOCKER_RUNS") == workflow
    image = script_value("RELEASE_IMAGE")
    assert "docker build -f tests/wifi/Dockerfile -t %s ." % image in workflow_commands("wifi-recovery.yml")
    assert 'docker build -f tests/wifi/Dockerfile -t "$RELEASE_IMAGE" .' in script_text()


def test_lint_rules_and_playwright_pin_match_the_workflows():
    rules = script_text().split("LINT_ARGS=(check --select ", 1)[1].split(")", 1)[0]
    rules = rules.replace('"', "")
    expected = "ruff check --select " + rules
    assert expected in workflow_commands("tests.yml")
    assert expected in workflow_commands("wifi-recovery.yml")
    pin = "playwright==" + script_value("PLAYWRIGHT_VERSION")
    for name in ("tests.yml", "wifi-recovery.yml"):
        assert any(pin in command for command in workflow_commands(name)), name


def test_every_listed_module_exists_and_is_runnable():
    names = [script_value("JOURNEYS_MODULE")] + script_array("PI_BROWSER_MODULES")
    names += script_array("GUI_MODULES") + [script_value("HOMEPAGE_MODULE")]
    assert len(names) == len(set(names))
    for name in names:
        source = (ROOT / "tests" / "browser" / (name + ".py")).read_text()
        assert "__main__" in source, name


def test_no_browser_module_is_named_like_a_pytest_file():
    # The release image runs `pytest -q` over tests/ and has no Playwright. A browser module named
    # test_*.py or *_test.py would be collected there and fail to import.
    collected = sorted(
        path.name for path in (ROOT / "tests" / "browser").glob("*.py")
        if path.name.startswith("test_") or path.stem.endswith("_test")
    )
    assert collected == []


def test_the_list_command_prints_the_workflow_modules():
    result = run_script("list")
    assert result.returncode == 0, result.stderr
    listed = re.findall(r"^  python -m tests\.browser\.(\w+)$", result.stdout, re.M)
    expected = [script_value("JOURNEYS_MODULE")] + browser_modules(workflow_commands("wifi-recovery.yml"))
    assert listed == expected


# -- the mapping for --changed -------------------------------------------------------------


def mapping_rules():
    rules = []
    for line in (SCRIPTS / "local-ci-changed-map.txt").read_text().splitlines():
        line = line.split("#", 1)[0].split()
        if line:
            rules.append((line[0], line[1:]))
    return rules


def selected(*files):
    result = run_script("list", "--changed", "--files", " ".join(files))
    assert result.returncode == 0, result.stderr
    return re.findall(r"^  python -m tests\.browser\.(\w+)$", result.stdout, re.M)


def test_the_mapping_names_only_real_modules_and_keywords():
    known = {script_value("JOURNEYS_MODULE")} | set(script_array("PI_BROWSER_MODULES"))
    assert mapping_rules()
    for pattern, targets in mapping_rules():
        assert targets, pattern
        for target in targets:
            assert target in known | {"all", "none"}, (pattern, target)


def test_documentation_changes_select_no_browser_module():
    assert selected("docs/local-ci.md", "README.md") == []


def test_a_mapped_source_file_selects_its_modules_in_workflow_order():
    assert selected("src/jittersim/api/trust.js") == ["trust_access"]
    assert selected("tests/browser/router_copy.py") == ["trust_access", "router_copy"]


def test_a_changed_module_file_selects_that_module():
    assert selected("tests/browser/token_remember.py") == ["token_remember"]


def test_an_unmapped_file_selects_every_module():
    everything = [script_value("JOURNEYS_MODULE")] + script_array("PI_BROWSER_MODULES")
    assert selected("src/jittersim/api/dashboard.js") == everything
    assert selected("docs/a.md", "src/jittersim/api/dashboard.js") == everything
    assert selected("tests/browser/a_new_module_nobody_mapped.py") == everything


def test_only_rejects_an_unknown_module():
    result = run_script("list", "--only", "ad_blocking,no_such_module")
    assert result.returncode == 2
    assert "no_such_module" in result.stderr


# -- refusing a dirty tree --------------------------------------------------------------------


@pytest.fixture
def scratch_repository(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repository = tmp_path / "repository"
    shutil.copytree(SCRIPTS, repository / "scripts", ignore=shutil.ignore_patterns("__pycache__"))

    def git(*arguments):
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
             "-c", "commit.gpgsign=false"] + list(arguments),
            cwd=str(repository), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    repository.mkdir(exist_ok=True)
    git("init", "-q")
    git("add", ".")
    git("commit", "-q", "-m", "scratch")
    return repository, git


def test_a_dirty_tree_is_refused_unless_allowed(scratch_repository):
    repository, _git = scratch_repository
    script = repository / "scripts" / "local-ci.sh"
    clean = run_script("--dry-run", "lint", cwd=repository, script=script)
    assert clean.returncode == 0, clean.stderr
    assert "would run lint" in clean.stdout
    assert "working tree: clean" in clean.stdout
    (repository / "untracked.txt").write_text("x")
    refused = run_script("--dry-run", "lint", cwd=repository, script=script)
    assert refused.returncode == 2
    assert "untracked.txt" in refused.stderr
    allowed = run_script("--allow-dirty", "--dry-run", "lint", cwd=repository, script=script)
    assert allowed.returncode == 0, allowed.stderr
    assert "DIRTY (allowed with --allow-dirty)" in allowed.stdout


@pytest.mark.skipif(shutil.which("npm") is None,
                    reason="the plan for `all` includes the Homepage job, which needs npm (the Pi release image has none)")
def test_a_dry_run_writes_nothing(scratch_repository):
    repository, _git = scratch_repository
    script = repository / "scripts" / "local-ci.sh"
    result = run_script("--dry-run", "all", cwd=repository, script=script)
    assert result.returncode == 0, result.stderr
    assert not (repository / "test-results").exists()
    assert not list(repository.glob(".venv-local-ci-*"))
    # The locked packages, the image build and one line for each Docker run.
    assert result.stdout.count("would run release-") == 2 + len(script_array("RELEASE_DOCKER_RUNS"))


def fake_python(directory, version):
    """A program that only answers which version it is, as find_python asks (dry runs need no more)."""

    directory.mkdir(exist_ok=True)
    program = directory / ("python" + version)
    program.write_text("#!/bin/sh\necho %s\n" % version)
    program.chmod(0o755)
    return directory


def test_python_3_9_gets_no_playwright_because_it_cannot_install_it(scratch_repository, tmp_path):
    # Playwright 1.61 and newer need Python 3.10. The hosted Python 3.9 job installs '.[dev]' only
    # and the tests that need a browser skip there. Installing the pin made the 3.9 environment fail,
    # so `unit --python 3.9` (the one way to run the oldest row of the matrix here) never started.
    repository, _git = scratch_repository
    script = repository / "scripts" / "local-ci.sh"
    pin = "playwright==" + script_value("PLAYWRIGHT_VERSION")
    old = run_script("--dry-run", "--python", "3.9", "unit", cwd=repository, script=script,
                     path_first=[fake_python(tmp_path / "bin39", "3.9")])
    assert old.returncode == 0, old.stderr
    assert "would install" in old.stdout and "no Playwright" in old.stdout
    assert pin not in old.stdout
    new = run_script("--dry-run", "--python", "3.12", "unit", cwd=repository, script=script,
                     path_first=[fake_python(tmp_path / "bin312", "3.12")])
    assert new.returncode == 0, new.stderr
    assert pin in new.stdout and "no Playwright" not in new.stdout


@pytest.mark.parametrize("command", ["browser", "gui", "homepage"])
def test_the_browser_steps_refuse_python_3_9_and_say_why(scratch_repository, tmp_path, command):
    repository, _git = scratch_repository
    script = repository / "scripts" / "local-ci.sh"
    refused = run_script("--dry-run", "--python", "3.9", command, cwd=repository, script=script,
                         path_first=[fake_python(tmp_path / "bin39", "3.9")])
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "Python 3.10 or newer" in refused.stderr
    assert "--python 3.13" in refused.stderr


def test_lint_plans_the_advisory_checks_unless_they_are_switched_off(scratch_repository):
    repository, _git = scratch_repository
    script = repository / "scripts" / "local-ci.sh"
    with_checks = run_script("--dry-run", "lint", cwd=repository, script=script)
    assert with_checks.returncode == 0, with_checks.stderr
    assert "advisory checks (counts only, they never fail the run)" in with_checks.stdout
    assert "would run advisory-ruff-full" in with_checks.stdout
    assert "--select E,F,I,S,B,UP,SIM,RUF".replace(",", "\\,") in with_checks.stdout
    assert "would run lint" in with_checks.stdout
    without = run_script("--dry-run", "--no-advisory", "lint", cwd=repository, script=script)
    assert without.returncode == 0, without.stderr
    assert "advisory-" not in without.stdout and "advisory checks" not in without.stdout
    assert "would run lint" in without.stdout


# -- the exec helper ---------------------------------------------------------------------------


def exec_helper(*arguments, preexec_fn=None):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "local_ci_exec.py")] + list(arguments),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60,
        preexec_fn=preexec_fn)


def test_the_helper_keeps_the_exit_status_and_writes_the_log(tmp_path):
    log = tmp_path / "step.log"
    result = exec_helper("--log", str(log), "--", sys.executable, "-c", "print('hello'); raise SystemExit(7)")
    assert result.returncode == 7
    text = log.read_text()
    assert "hello" in text and "finished with status 7" in text


def test_the_helper_stops_a_command_at_the_time_limit(tmp_path):
    log = tmp_path / "step.log"
    result = exec_helper("--timeout", "1", "--log", str(log), "--", sys.executable, "-c",
                         "import time; print('started', flush=True); time.sleep(60)")
    assert result.returncode == 124
    text = log.read_text()
    assert "started" in text and "limit passed" in text


def test_the_helper_reports_a_missing_command(tmp_path):
    log = tmp_path / "step.log"
    result = exec_helper("--log", str(log), "--", str(tmp_path / "no-such-command"))
    assert result.returncode == 127
    assert "cannot start" in log.read_text()


@pytest.mark.skipif(not hasattr(signal, "SIGINT"), reason="signals are not available")
def test_the_helper_restores_ctrl_c_for_a_background_run(tmp_path):
    ignore = lambda: signal.signal(signal.SIGINT, signal.SIG_IGN)  # noqa: E731
    probe = "import signal; print(signal.getsignal(signal.SIGINT) is signal.default_int_handler)"
    for arguments in (["--exec", "--"], ["--log", str(tmp_path / "step.log"), "--echo", "--"]):
        result = exec_helper(*(arguments + [sys.executable, "-c", probe]), preexec_fn=ignore)
        assert result.stdout.strip().splitlines()[-1 if "--echo" not in arguments else 2] == "True", result.stdout


def test_the_helper_does_not_wait_forever_for_a_command_that_ignores_ctrl_c(tmp_path):
    log = tmp_path / "step.log"
    program = ("import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
               "print('ready', flush=True); time.sleep(120)")
    helper = subprocess.Popen(
        [sys.executable, str(SCRIPTS / "local_ci_exec.py"), "--log", str(log), "--interrupt-grace", "1",
         "--", sys.executable, "-c", program], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(200):
            if log.exists() and "ready" in log.read_text():
                break
            helper.poll()
            time.sleep(0.1)
        helper.send_signal(signal.SIGINT)
        returncode = helper.wait(timeout=30)
    finally:
        if helper.poll() is None:
            helper.kill()
        helper.communicate()
    assert returncode != 0
    assert "stopping it" in log.read_text()


# -- the report ---------------------------------------------------------------------------------

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
<testcase classname="tests.unit.test_local_ci" name="test_ok" time="0.1"/>
<testcase classname="tests.unit.test_local_ci" name="test_broken" time="0.1"><failure message="boom">x</failure></testcase>
<testcase classname="tests.unit.test_local_ci" name="test_skipped" time="0.1"><skipped message="needs Linux"/></testcase>
<testcase classname="tests.unit.test_local_ci" name="test_skipped_too" time="0.1"><skipped message="needs Linux"/></testcase>
</testsuite></testsuites>
"""


@pytest.fixture
def run_directory(tmp_path):
    steps = [
        ("lint", "PASS", "0", "3", "lint.log", "-", "ruff"),
        ("unit-py3.12", "FAIL", "1", "90", "unit.log", "unit.xml", "pytest"),
        ("browser:ad_blocking", "FAIL", "1", "12", "ad.log", "-", "python -m tests.browser.ad_blocking"),
        ("kernel", "BLOCKED", "69", "0", "kernel.log", "-", "blocked"),
        ("browser:patterns", "NOT RUN", "-", "0", "-", "-", "python -m tests.browser.patterns"),
    ]
    (tmp_path / "steps.tsv").write_text("\n".join("\t".join(row) for row in steps) + "\n")
    (tmp_path / "meta.tsv").write_text(
        "run_id\tlocal-ci-test\ncommit\t`abc123` (branch `x`)\nscope\tfull\nwarnings\tfirst || second || \n")
    (tmp_path / "unit.xml").write_text(JUNIT)
    return tmp_path


def test_the_summary_lists_every_step_with_counts_and_the_commit(run_directory):
    report = load_helper("local_ci_report")
    assert report.main(["summary", str(run_directory)]) == 0
    text = (run_directory / "summary.md").read_text()
    assert "`abc123`" in text
    assert "| unit-py3.12 | FAIL | 1 | 90 | 1 passed, 1 failed, 0 errors, 2 skipped | unit.log |" in text
    assert "| lint | PASS | 0 | 3 | 1 passed | lint.log |" in text
    assert "| kernel | BLOCKED | 69 |" in text
    assert "Totals: 5 steps, 1 passed, 4 not passed" in text
    assert "- 2: needs Linux" in text
    assert "Hosted GitHub Actions did not run" in text
    assert "- first" in text and "- second" in text
    suite = (run_directory / "junit.xml").read_text()
    assert 'tests="5"' in suite and 'failures="3"' in suite and 'skipped="1"' in suite


def test_failed_lists_what_to_run_alone(run_directory):
    report = load_helper("local_ci_report")
    targets = report.failed_targets(str(run_directory), root=str(ROOT))
    assert targets == ["tests/unit/test_local_ci.py::test_broken", "browser:ad_blocking"]


def test_a_class_in_the_node_id_is_found_on_disk():
    report = load_helper("local_ci_report")
    assert report.pytest_node_id(
        "tests.unit.test_local_ci.TestThing", "test_x", str(ROOT)) == "tests/unit/test_local_ci.py::TestThing::test_x"
    assert report.pytest_node_id("tests.nowhere.test_x", "test_y", str(ROOT)) is None


def test_the_docs_name_every_command():
    text = (ROOT / "docs" / "local-ci.md").read_text()
    for command in ("lint", "unit", "browser", "gui", "homepage", "kernel", "release-checks", "all", "rerun"):
        assert "local-ci.sh " + command in text, command


ADVISORY_STEPS = [
    ("lint", "PASS", "0", "1", "lint.log", "-", "ruff", ""),
    ("advisory-ruff-full", "ADVISORY", "0", "2", "r.log", "-", "ruff", "10757 findings"),
    ("advisory-zizmor", "ADVISORY", "14", "1", "z.log", "-", "zizmor", "13 findings (4 suppressed)"),
    ("advisory-install-actionlint", "INSTALLED", "0", "9", "i.log", "-", "brew install actionlint", ""),
    ("advisory-pip-audit", "UNAVAILABLE", "0", "0", "-", "-", "tool not available", "pip-audit is not installed"),
]


def test_advisory_steps_report_counts_and_never_count_as_failures(tmp_path):
    (tmp_path / "steps.tsv").write_text("\n".join("\t".join(row) for row in ADVISORY_STEPS) + "\n")
    (tmp_path / "meta.tsv").write_text("run_id\tlocal-ci-test\n")
    report = load_helper("local_ci_report")
    assert report.main(["summary", str(tmp_path)]) == 0
    text = (tmp_path / "summary.md").read_text()
    assert "| advisory-ruff-full | ADVISORY | 0 | 2 | 10757 findings | r.log |" in text
    assert "| advisory-zizmor | ADVISORY | 14 | 1 | 13 findings (4 suppressed) | z.log |" in text
    assert "| advisory-pip-audit | UNAVAILABLE |" in text
    assert "Totals: 1 steps, 1 passed, 0 not passed (none)." in text
    assert "Advisory steps (they never gate and are not counted above): 4." in text
    suite = (tmp_path / "junit.xml").read_text()
    assert 'failures="0"' in suite and 'skipped="1"' in suite
    assert report.failed_targets(str(tmp_path), root=str(ROOT)) == []

# Local CI

Hosted GitHub Actions cannot start jobs for this repository (the account hit a
billing and spending limit). Every pull request is therefore gated by running
the workflow commands on a Mac. `scripts/local-ci.sh` is the one committed
command that does this. It runs the commands of `.github/workflows/tests.yml`
and `.github/workflows/wifi-recovery.yml`, one step at a time, and writes a
summary you can paste into the pull request.

Hosted CI is still the real gate. These are local results and the summary says
so. Read the "Not covered" section before you trust a green run.

## Quick start

```sh
git fetch --all --prune
scripts/local-ci.sh lint            # gating ruff plus advisory checks
scripts/local-ci.sh unit            # the whole pytest suite
scripts/local-ci.sh browser         # journeys and the 27 Pi browser modules
scripts/local-ci.sh all             # everything below, one after the other
```

A long run in the background (use `kill -INT`, `kill -TERM` or Ctrl-C to stop it):

```sh
nohup scripts/local-ci.sh unit > /tmp/local-ci-unit.out 2>&1 &
tail -f /tmp/local-ci-unit.out
```

The first run creates `.venv-local-ci-3.12` (the number is the Python version)
in the checkout and installs the project, pytest, ruff and Playwright 1.63.0
into it. The browser commands also run `playwright install chromium`, which
downloads Chromium once into Playwright's cache.

## Commands

| Command | What it runs | Workflow job |
| --- | --- | --- |
| `scripts/local-ci.sh lint` | `ruff check --select E9,F63,F7,F82,F811 src tests`, then the advisory checks below | Lint |
| `scripts/local-ci.sh unit` | `python -m pytest` on the whole suite, on one Python | Python 3.9, 3.11, 3.13 |
| `scripts/local-ci.sh browser` | `tests.browser.journeys` and the 27 modules of the Pi browser job | Browser journeys, Pi browser journeys |
| `scripts/local-ci.sh gui` | the three GUI pytest files, `gui_baseline` and `a11y_axe` | GUI baseline |
| `scripts/local-ci.sh homepage` | `npm ci`, the homepage fixture test, `homepage_bounce_reference`, two Node journeys | Homepage reference |
| `scripts/local-ci.sh kernel` | `tests/kernel/run-in-docker.sh` (Docker, privileged) | Linux datapath |
| `scripts/local-ci.sh release-checks` | `prepare-locked-wheels.py`, then the Docker build and 13 Docker runs of the Pi Linux checks | Python matrix (locked packages), Pi Linux release checks |
| `scripts/local-ci.sh all` | all of the above in that order | everything above |
| `scripts/local-ci.sh rerun TARGET...` | failed tests alone (see below) | none |
| `scripts/local-ci.sh list` | the browser modules and the planned commands | none |

`scripts/local-ci.sh --dry-run COMMAND` prints the commands and runs nothing.
`scripts/local-ci.sh help` lists every option.

## What a run does and does not do

- It refuses to start when `git status` shows any change, including untracked
  files, unless you pass `--allow-dirty`. The summary then says the tree was
  dirty. After the run it checks `git status` again and warns if a test changed
  the tree.
- It prints the exact commit (hash, tree, branch, subject, and how far it is
  from `origin/main`) at the start and at the top of the summary.
- It never retries anything. A failed step stays failed. Re-running a step is
  your decision and `rerun` records it as a new run.
- It runs one step at a time, and only one local-ci run at a time on the
  machine (a lock directory in `$TMPDIR`). Steps run under `nice -n 10`.
- Every step has a time limit (browser modules 15 minutes, pytest an hour,
  Docker steps an hour). A step that passes the limit is stopped with
  everything it started and reported as `TIMEOUT`. Change all limits with
  `--step-timeout SECONDS`.
- Ctrl-C works in background runs. A shell starts a background job with SIGINT
  ignored and every child inherits that. The script starts itself again through
  `scripts/local_ci_exec.py`, which restores it, and runs every step through the
  same helper.
- The package is installed into the venv as a wheel, like the workflows do, and
  `build/` is removed first so a stale build cannot hide a missing file.
- A Docker step that cannot start (Docker is down, or the Docker VM is short of
  memory) is recorded as `BLOCKED` and the run exits with 1. It is never
  skipped silently.

## Results

Each run writes `test-results/local-ci-<timestamp>/` (that folder is ignored by git):

| File | Content |
| --- | --- |
| `summary.md` | The commit, the table of steps with result, exit code, seconds and case counts, the skipped cases by reason, warnings |
| `junit.xml` | One test case per step (pytest steps also keep their own junit file) |
| `steps.tsv`, `meta.tsv` | The raw records the summary is built from |
| `<step>.log` | The command and its full output |
| `unit-py3.12.xml` and similar | pytest junit files |
| `gui-baseline/`, `gui-axe/` | The GUI baseline and accessibility output |

Paste `summary.md` into the pull request. A tidy way to state it: "Hosted CI
did not run. Local results for commit X: ...".

A summary whose scope line says `subset` is not a full run. Do not present it as
one.

## Advisory checks

`lint` runs these after the gating ruff step. They print a count and never fail the run:

| Step | Command | What the count is |
| --- | --- | --- |
| `advisory-ruff-full` | `ruff check --statistics --exit-zero --select E,F,I,S,B,UP,SIM,RUF src tests` | findings with the wider rule set (the `S` rules are the bandit rules) |
| `advisory-shellcheck` | `shellcheck -x -f gcc` on every tracked shell script (found by the first line) | warnings, errors and notes |
| `advisory-actionlint` | `actionlint -oneline .github/workflows/*.yml` | findings |
| `advisory-zizmor` | `zizmor --offline --no-progress .github/workflows` | findings (zizmor's own summary line, with suppressed ones) |
| `advisory-pip-audit` | `pip-audit --require-hashes --disable-pip -r <lock>` for the three files in `deploy/raspberry-pi/release-locks/` | known vulnerabilities |

The narrow ruff set above stays the gate. Raise a gate only in its own pull request after the
backlog is fixed. A missing tool is installed once with `brew install` (shellcheck, actionlint,
zizmor) or with pip into `.venv-local-ci-tools` (pip-audit, which needs Python 3.10 or newer).
A tool that cannot be installed or run shows as `UNAVAILABLE`, which is not a failure. Pass
`--no-install` to forbid installs and `--no-advisory` to skip the checks. pip-audit asks the
network (OSV and PyPI) about the pinned packages and sends nothing else.

## Failed tests

```sh
scripts/local-ci.sh rerun tests/unit/test_example.py::test_name   # one pytest test, alone
scripts/local-ci.sh rerun ad_blocking                             # one browser module
scripts/local-ci.sh rerun browser:ad_blocking tests/unit/test_example.py::TestThing::test_name
scripts/local-ci.sh rerun --failed                                # everything that failed in the newest run
scripts/local-ci.sh rerun --failed test-results/local-ci-20261009-193000
scripts/local-ci.sh rerun tests/unit/test_example.py -- -x -s     # extra pytest arguments after --
```

Each rerun makes its own `local-ci-<timestamp>-rerun` folder. A test that passes
alone and fails in the full run is a real finding (order or load). Say so in
the pull request. Do not call it flaky without a reason.

## Only the browser modules a change can affect

```sh
scripts/local-ci.sh browser --changed             # compare with origin/main
scripts/local-ci.sh browser --changed --base main
scripts/local-ci.sh browser --only ad_blocking,patterns
```

`--changed` maps each changed path to browser modules with
`scripts/local-ci-changed-map.txt`. A path with no rule runs every module, so a
new file never skips coverage. The rules are short on purpose and only name
modules that certainly cover a file. This is a quick pre-check. The full
`browser` run is what gates a pull request, and the summary marks a subset as one.

## Python versions

The workflows run Python 3.9, 3.11 and 3.13. Checked on this Mac on 9 October 2026:

| Python | Where | How to use it |
| --- | --- | --- |
| 3.9.6 | `/usr/bin/python3` (Xcode command line tools) | `scripts/local-ci.sh unit --python 3.9` |
| 3.12.13 | `/opt/homebrew/bin/python3.12` | the default (`--python` unset) |
| 3.11, 3.13 | not installed on the Mac | `brew install python@3.13 python@3.11`, then `--python 3.13`, or use Docker |

The default is the newest of 3.13, 3.12, 3.11, 3.10, 3.9 that exists. The summary
says which Python the run covered.

Docker can cover the missing versions without installing anything:

```sh
scripts/local-ci.sh unit --docker 3.11
scripts/local-ci.sh unit --docker 3.13
```

This runs pytest in a throw-away `python:X.Y-slim-bookworm` container with the
source copied in (the checkout is mounted read-only). The container is limited to
1 CPU and 1 GB. Two things differ from a native run. The image has no Git, and
the worktree's `.git` file points outside the container, so the Git-backed tests
skip, as in the "native no-Git" suite of the 4 October verification. The kernel
tests also skip because the container is not privileged. Read the skip counts
in the summary.

The browser, GUI and homepage commands run on the venv Python only.

## The Docker VM

Docker Desktop here runs one VM with 1 CPU and about 2 GB of memory. Unrelated MAIA
containers (Supabase and friends) share it and must never be stopped. The runner
therefore:

- only starts containers with `--rm`, and never stops, removes or prunes a
  container or image it did not start;
- starts every container with `--memory 1g` (change with `JITTERSIM_CI_DOCKER_MEMORY`),
  except the one inside `tests/kernel/run-in-docker.sh`, which is the repository's script;
- looks at the VM before the first Docker step and records `BLOCKED` when less than
  512 MB is free (`JITTERSIM_CI_DOCKER_MIN_FREE_MB`; override with `--ignore-docker-memory`).

`kernel` and `release-checks` need privileged containers and take a long time on one
CPU. Run them alone, at a time the Docker VM is quiet.

## Not covered

- The release-candidate workflow. `deploy/raspberry-pi/release-system.py candidate`
  reads the results of the hosted checks from the GitHub API, so it cannot pass while
  hosted CI is down.
- The 3.11 and 3.13 rows of the Python matrix unless you run them yourself (see above).
- `playwright install --with-deps`. On a Mac Chromium needs no system packages.
- Anything on a real Pi, router or phone.
- The macOS arm64 build is not the amd64 `ubuntu-latest` image. Run the Docker steps
  when a change touches Linux behavior.

## Why not act, just or make

The owner's rule is to prefer mature open-source tools over custom code. The runner
already uses them where they fit (ruff, pytest and its junit output, Playwright,
npm, Docker, shellcheck for the script itself). The decisions:

- **nektos/act** runs the workflow files in Docker. It is not the default here for
  these reasons. Every job would run in a `catthehacker/ubuntu:act-latest` container
  (a large image, size not measured here) inside the Docker VM, which has 1 CPU and about 2 GB and is
  shared with MAIA containers. The Python matrix has `max-parallel: 3` and would start
  three containers at once. The browser jobs run `playwright install --with-deps`, which
  runs `apt-get` as root in the container every time. The kernel and Pi jobs need
  privileged containers, and the act guide does not say that act supports them. Their
  steps also call `docker run` themselves, so they would need Docker inside Docker. `release-candidate.yml` is
  triggered by `workflow_run` and cannot run in act. Artifacts and cache actions behave
  differently. Finally act does not write the junit files, the summary table, the dirty
  tree check or the rerun helper, so the script would still be needed. act stays a
  trial for one lightweight job (below).
- **just** and **make** only name commands. The script already has the subcommands, and
  the part that matters (results folder, exit codes, counts, lock, time limits) does not
  fit in a Makefile. Wrapping `scripts/local-ci.sh` in one adds a file and nothing else.
  If the owner wants `make check` later, it should call `scripts/local-ci.sh lint` and
  the other commands.
- **A self-hosted runner** is the only option that runs the real workflow files as written.
  See the trial below.

## Trial: self-hosted runner or act (not run yet)

These are the commands for a later trial. None of them was run for this page, and act must
not be started against the shared Docker VM without the owner's agreement.

Self-hosted runner. It decides in one job whether GitHub starts a self-hosted job while the
account is billing-blocked. Use a machine without secrets and only the owner's own branches,
because a runner executes pull request code.

1. In the repository on GitHub: Settings, Actions, Runners, New self-hosted runner.
   Choose macOS and arm64. GitHub shows the exact download commands and a registration
   token that expires after one hour. Only the owner can create that token. Do not paste
   it into a file or a chat.
2. Run the commands GitHub shows, in a new folder outside the repository. The last two are:
   `./config.sh --url https://github.com/smartinezai/jittersim --token <token from the page>`
   and `./run.sh`. The terminal then prints "Connected to GitHub" and "Listening for Jobs".
3. On a throw-away branch add `.github/workflows/runner-trial.yml` with
   `on: workflow_dispatch` and one job with `runs-on: self-hosted` and a step
   `run: echo hello && uname -a`. Start it from the Actions tab.
4. If the job starts, the existing workflows need `runs-on: [self-hosted, linux]` (or a
   label of your choice) and a Linux machine or VM for the kernel and Pi jobs. If it does
   not start, the billing limit blocks it too.

act, for one light job (Lint) on a machine whose Docker has room:

```sh
brew install act
act -l pull_request
act pull_request -j lint --container-architecture linux/amd64 -P ubuntu-latest=catthehacker/ubuntu:act-latest
```

`-l EVENT` lists workflows for an event and `-j JOB` selects one job. The
`--container-architecture linux/amd64` flag runs the image the way a hosted runner
does, through emulation on Apple Silicon, which is slow. These flags and image names come from
the act user guide (nektosact.com).

## Changing the runner

- The list of browser modules, the Docker runs, the pytest files and the version pins are copied
  from the workflows. `tests/unit/test_local_ci.py` fails when they differ, so a workflow
  change and a script change go in the same pull request.
- Run `shellcheck -x scripts/local-ci.sh` after editing the script (`brew install shellcheck`).
- The helpers `scripts/local_ci_exec.py` and `scripts/local_ci_report.py` use only the
  Python standard library and run on the system Python 3.9.

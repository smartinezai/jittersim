#!/usr/bin/env bash
# One-command local CI for JitterSim. It runs the commands of the GitHub workflows
# (.github/workflows/tests.yml and wifi-recovery.yml) on this machine, one step at a time,
# and writes logs, junit files and a summary under test-results/local-ci-<timestamp>/.
# Read docs/local-ci.md first. Run `scripts/local-ci.sh help` for the options.
#
# It works with the bash 3.2 that macOS ships. It never retries a step, and it never
# stops, removes or prunes a Docker container or image that it did not start with --rm.
# `lint` also runs advisory checks (full Ruff rules, shellcheck, actionlint, zizmor, pip-audit).
# They print counts and never fail the run.

# The functions the traps call are not seen as called by shellcheck.
# shellcheck disable=SC2329
set -u
set -o pipefail

ORIGINAL_ARGS="$*"
SELF=${BASH_SOURCE[0]}
SELF_DIR=$(cd "$(dirname "$SELF")" && pwd -P)

# A background job starts with SIGINT ignored. Start again through the helper, which restores it,
# so Ctrl-C and `kill -INT` reach this script and everything under it.
if [ -z "${JITTERSIM_CI_REEXEC:-}" ]; then
  export JITTERSIM_CI_REEXEC=1
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      exec "$candidate" "$SELF_DIR/local_ci_exec.py" --exec -- bash "$SELF" "$@"
    fi
  done
fi

ROOT=$(cd "$SELF_DIR/.." && pwd -P)
HELPER="$SELF_DIR/local_ci_exec.py"
REPORT="$SELF_DIR/local_ci_report.py"
MAP_FILE="$SELF_DIR/local-ci-changed-map.txt"

# ---------------------------------------------------------------------------------------------
# What runs. tests/unit/test_local_ci.py compares these lists with the workflow files, so a
# module added to a workflow and not here fails a test instead of silently skipping locally.
# ---------------------------------------------------------------------------------------------

PLAYWRIGHT_VERSION=1.63.0

# tests.yml, job "Browser journeys".
JOURNEYS_MODULE=journeys

# wifi-recovery.yml, job "Pi browser journeys", in workflow order.
PI_BROWSER_MODULES=(
  wifi_recovery
  wifi_restart_reconnect
  dashboard_access
  protected_dashboard_access
  trust_access
  address_move
  address_move_privacy
  general_profile
  device_apply_start
  saved_profiles
  schedules_timers
  limits_quotas
  patterns
  random_value_ranges
  dashboard_navigation
  protected_clients
  ad_blocking
  group_devices
  impact_check
  sidebar_status
  apply_clarity
  router_copy
  observation_confirmation
  traffic_chart
  device_names
  phone_layout
  help_icons
  token_remember
  dns_failure
  page_polling
  update_status_recovery
  live_regions
)

# tests.yml, job "GUI baseline".
GUI_PYTEST_FILES=(
  tests/unit/test_gui_capture.py
  tests/unit/test_gui_axe_report.py
  tests/unit/test_gui_container.py
)
GUI_MODULES=(
  gui_baseline
  a11y_axe
)

# tests.yml, job "Homepage reference".
HOMEPAGE_PYTEST_FILE=tests/test_homepage_reference_fixture.py
HOMEPAGE_MODULE=homepage_bounce_reference
HOMEPAGE_NODE_PREFIX=experiments/homepage-bounce-reference
HOMEPAGE_NODE_SCRIPTS=(
  tests/browser/homepage-live/dashboard.cjs
  tests/browser/homepage-live/journey.cjs
)

# tests.yml job "Linux datapath".
KERNEL_SCRIPT=tests/kernel/run-in-docker.sh

# wifi-recovery.yml, job "Pi Linux release checks": the arguments after `docker run`.
RELEASE_IMAGE=jittersim-wifi-release
RELEASE_DOCKER_RUNS=(
  "--rm jittersim-wifi-release python tests/integration/linux_installer_permissions.py"
  "--rm --cap-add NET_ADMIN jittersim-wifi-release python tests/integration/linux_dashboard_peer_identity.py"
  "--rm --init --privileged jittersim-wifi-release"
  "--rm --privileged jittersim-wifi-release python tests/integration/linux_network_smoke.py"
  "--rm --privileged jittersim-wifi-release python tests/integration/linux_exemption_packets.py"
  "--rm --privileged jittersim-wifi-release python tests/integration/linux_many_queues.py"
  "--rm --privileged jittersim-wifi-release python tests/integration/linux_protected_not_monitored.py"
  "--rm --privileged --network none jittersim-wifi-release python tests/integration/linux_adblocking.py"
  "--rm --privileged jittersim-wifi-release python tests/integration/linux_service_list_packets.py"
  "--rm --privileged jittersim-wifi-release python tests/integration/linux_service_list_variation.py"
  "--rm --privileged jittersim-wifi-release python tests/integration/linux_queue_stats.py"
  "--rm --privileged jittersim-wifi-release python -m tests.integration.linux_dns_watch"
  "--rm --cap-add NET_ADMIN --cap-add NET_RAW jittersim-wifi-release python tests/integration/linux_dashboard_access_packets.py"
)

LINT_ARGS=(check --select "E9,F63,F7,F82,F811" src tests)

# Advisory checks that `lint` adds after the gating ruff step. They only report counts.
ADVISORY_RUFF_RULES="E,F,I,S,B,UP,SIM,RUF"
ADVISORY_WORKFLOWS=".github/workflows"
PIP_AUDIT_LOCKS=(
  deploy/raspberry-pi/release-locks/runtime.txt
  deploy/raspberry-pi/release-locks/runtime-py39.txt
  deploy/raspberry-pi/release-locks/build.txt
)

# Time limits in seconds. The workflows give a whole job 5 to 30 minutes on a hosted runner.
# A loaded Mac is slower, so these are looser. --step-timeout replaces them all.
TIMEOUT_LINT=300
TIMEOUT_SETUP=1800
TIMEOUT_UNIT=3600
TIMEOUT_MODULE=900
TIMEOUT_GUI=1800
TIMEOUT_DOCKER=3600

# ---------------------------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------------------------

ALLOW_DIRTY=0
CHANGED=0
BASE_REF=origin/main
FILES_OVERRIDE=
ONLY=
WANT_PYTHON=
DOCKER_PYTHON=
RESULTS_ROOT=${JITTERSIM_CI_RESULTS:-test-results}
DRY_RUN=0
FAIL_FAST=0
ECHO=0
STEP_TIMEOUT=
FAILED_MODE=0
IGNORE_DOCKER_MEMORY=0
ADVISORY=1
NO_INSTALL=0
NICE_LEVEL=${JITTERSIM_CI_NICE:-10}
DOCKER_MEMORY=${JITTERSIM_CI_DOCKER_MEMORY:-1g}
DOCKER_MIN_FREE_MB=${JITTERSIM_CI_DOCKER_MIN_FREE_MB:-512}

COMMAND=
TARGETS=()
EXTRA=()

usage() {
  cat <<'EOF'
Usage: scripts/local-ci.sh [options] COMMAND [TARGET...] [-- EXTRA ARGUMENTS]

Commands (each one is one workflow job, or a few):
  lint            ruff as the Lint job does, then advisory checks that only print counts
  unit            the whole pytest suite on one Python (the Python matrix job, one version)
  browser         tests.browser.journeys and the 28 Pi browser modules
  gui             the GUI baseline job: its pytest files, gui_baseline and a11y_axe
  homepage        the Homepage reference job (needs npm and the network for npm ci)
  kernel          the Linux datapath job (Docker, privileged)
  release-checks  the locked packages and the Pi Linux release checks (Docker, privileged)
  all             all of the above, one after the other
  rerun TARGET..  run failed tests alone: a pytest node id, a browser module name or
                  browser:MODULE. Use --failed [RUN_DIR] for everything that failed in a run.
  list            show the browser modules and the planned commands
  help            show this text

Options:
  --allow-dirty        run although git status shows changes (the summary says so)
  --changed            browser: only the modules the changed files map to (scripts/local-ci-changed-map.txt)
  --base REF           with --changed: compare with REF (default origin/main)
  --files "A B"        with --changed: use this list of paths and do not ask git
  --only A,B           browser: only these modules
  --python X.Y         use this Python for the virtual environment (default: the newest of 3.13 to 3.9)
  --docker X.Y         unit and rerun: run pytest in the python:X.Y-slim-bookworm image, not the venv
  --results-dir DIR    where to write local-ci-<timestamp> (default test-results)
  --step-timeout N     use N seconds as the limit of every step
  --fail-fast          stop after the first step that does not pass
  --failed             with rerun: take the targets from the failures of RUN_DIR (default: newest run)
  --echo               also print each step's output while it runs
  --dry-run            print the commands and run nothing
  --ignore-docker-memory   run Docker steps although the Docker VM looks short of memory
  --no-advisory        lint: skip the advisory checks (full Ruff, shellcheck, actionlint, zizmor, pip-audit)
  --no-install         lint: do not install a missing advisory tool (brew, pip into .venv-local-ci-tools)
  -h, --help           show this text

Exit status: 0 all steps passed, 1 a step did not pass, 2 refused or bad usage, 130 interrupted.
EOF
}

die() {
  printf 'local-ci: %s\n' "$*" >&2
  exit 2
}

need_value() {
  [ "$2" -ge 2 ] || die "$1 needs a value"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --allow-dirty) ALLOW_DIRTY=1 ;;
    --changed) CHANGED=1 ;;
    --base) need_value "$1" $#; BASE_REF=$2; shift ;;
    --files) need_value "$1" $#; FILES_OVERRIDE=$2; shift ;;
    --only) need_value "$1" $#; ONLY=$2; shift ;;
    --python) need_value "$1" $#; WANT_PYTHON=$2; shift ;;
    --docker) need_value "$1" $#; DOCKER_PYTHON=$2; shift ;;
    --results-dir) need_value "$1" $#; RESULTS_ROOT=$2; shift ;;
    --step-timeout) need_value "$1" $#; STEP_TIMEOUT=$2; shift ;;
    --fail-fast) FAIL_FAST=1 ;;
    --failed) FAILED_MODE=1 ;;
    --echo) ECHO=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --ignore-docker-memory) IGNORE_DOCKER_MEMORY=1 ;;
    --no-advisory) ADVISORY=0 ;;
    --no-install) NO_INSTALL=1 ;;
    -h|--help) usage; exit 0 ;;
    --) shift; EXTRA=("$@"); break ;;
    -*) die "unknown option $1 (see --help)" ;;
    *)
      if [ -z "$COMMAND" ]; then COMMAND=$1; else TARGETS+=("$1"); fi
      ;;
  esac
  shift
done

[ -n "$COMMAND" ] || { usage >&2; exit 2; }
case "$COMMAND" in
  help) usage; exit 0 ;;
  lint|unit|browser|gui|homepage|kernel|release-checks|all|rerun|list) ;;
  *) die "unknown command $COMMAND (see --help)" ;;
esac
case "$STEP_TIMEOUT" in ''|*[!0-9]*) [ -z "$STEP_TIMEOUT" ] || die "--step-timeout needs a whole number of seconds" ;; esac
if [ -n "$DOCKER_PYTHON" ]; then
  case "$COMMAND" in unit|rerun) ;; *) die "--docker only works with unit and rerun" ;; esac
  case "$DOCKER_PYTHON" in [0-9].[0-9]|[0-9].[0-9][0-9]) ;; *) die "--docker needs a version like 3.11" ;; esac
fi
if [ "$CHANGED" = 1 ] && [ -n "$ONLY" ]; then die "use either --changed or --only"; fi

cd "$ROOT" || die "cannot enter $ROOT"

# ---------------------------------------------------------------------------------------------
# Browser module helpers
# ---------------------------------------------------------------------------------------------

ALL_BROWSER_MODULES=("$JOURNEYS_MODULE" "${PI_BROWSER_MODULES[@]}")

is_browser_module() {
  local candidate
  for candidate in "${ALL_BROWSER_MODULES[@]}"; do
    [ "$candidate" = "$1" ] && return 0
  done
  return 1
}

# Print the modules a list of changed paths maps to, one per line, in workflow order.
# A path with no rule selects every module.
select_changed_modules() {
  local files=$1 file base line pattern targets target matched picked=" " all=0 module
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    matched=0
    case "$file" in
      tests/browser/*.py)
        base=${file##*/}
        base=${base%.py}
        if is_browser_module "$base"; then picked="$picked$base "; matched=1; fi
        ;;
    esac
    while IFS= read -r line || [ -n "$line" ]; do
      line=${line%%#*}
      pattern=
      targets=
      read -r pattern targets <<<"$line"
      [ -n "$pattern" ] || continue
      # shellcheck disable=SC2053  # the pattern is a glob on purpose
      if [[ $file == $pattern ]]; then
        matched=1
        for target in $targets; do
          case "$target" in
            all) all=1 ;;
            none) ;;
            *)
              is_browser_module "$target" || die "$MAP_FILE names $target, which is not a browser module"
              picked="$picked$target "
              ;;
          esac
        done
      fi
    done < "$MAP_FILE"
    [ "$matched" = 1 ] || all=1
  done <<<"$files"
  for module in "${ALL_BROWSER_MODULES[@]}"; do
    if [ "$all" = 1 ]; then
      printf '%s\n' "$module"
    else
      case "$picked" in *" $module "*) printf '%s\n' "$module" ;; esac
    fi
  done
}

changed_files() {
  if [ -n "$FILES_OVERRIDE" ]; then
    # shellcheck disable=SC2086  # a list of paths separated by blanks
    printf '%s\n' $FILES_OVERRIDE
    return
  fi
  local merge_base
  merge_base=$(git merge-base "$BASE_REF" HEAD 2>/dev/null) || die "cannot find a common commit with $BASE_REF (run git fetch, or pass --base)"
  git diff --name-only "$merge_base"
  git ls-files --others --exclude-standard
}

SELECTED_MODULES=()
SCOPE="full"

choose_browser_modules() {
  SELECTED_MODULES=()
  local module
  if [ -n "$ONLY" ]; then
    local item
    for item in ${ONLY//,/ }; do
      is_browser_module "$item" || die "--only names $item, which is not a browser module (see: scripts/local-ci.sh list)"
      SELECTED_MODULES+=("$item")
    done
    SCOPE="subset: --only $ONLY (not a full browser run)"
  elif [ "$CHANGED" = 1 ]; then
    local files selected
    files=$(changed_files) || exit 2
    selected=$(select_changed_modules "$files") || exit 2
    while IFS= read -r module; do
      [ -n "$module" ] && SELECTED_MODULES+=("$module")
    done <<<"$selected"
    if [ ${#SELECTED_MODULES[@]} -eq ${#ALL_BROWSER_MODULES[@]} ]; then
      SCOPE="full (every browser module was selected by --changed)"
    else
      SCOPE="subset: --changed selected ${#SELECTED_MODULES[@]} of ${#ALL_BROWSER_MODULES[@]} browser modules: ${SELECTED_MODULES[*]:-none} (not a full browser run)"
    fi
  else
    SELECTED_MODULES=("${ALL_BROWSER_MODULES[@]}")
  fi
}

# ---------------------------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------------------------

if [ "$COMMAND" = list ]; then
  choose_browser_modules
  printf 'Browser modules (%d), in the order browser runs them:\n' "${#SELECTED_MODULES[@]}"
  printf '  python -m tests.browser.%s\n' "${SELECTED_MODULES[@]+"${SELECTED_MODULES[@]}"}"
  printf '\nGUI baseline: pytest %s; python -m tests.browser.%s\n' "${GUI_PYTEST_FILES[*]}" "${GUI_MODULES[0]}"
  printf '              python -m tests.browser.%s\n' "${GUI_MODULES[1]}"
  printf 'Homepage reference: pytest %s; python -m tests.browser.%s; node %s\n' \
    "$HOMEPAGE_PYTEST_FILE" "$HOMEPAGE_MODULE" "${HOMEPAGE_NODE_SCRIPTS[*]}"
  printf 'Linux datapath: %s\n' "$KERNEL_SCRIPT"
  printf 'Pi Linux release checks: docker build + %d docker run commands\n' "${#RELEASE_DOCKER_RUNS[@]}"
  exit 0
fi

# ---------------------------------------------------------------------------------------------
# Git state
# ---------------------------------------------------------------------------------------------

git rev-parse --git-dir >/dev/null 2>&1 || die "$ROOT is not a git checkout"

tree_status() {
  git status --porcelain --untracked-files=all
}

START_STATUS=$(tree_status)
WORKING_TREE="clean"
if [ -n "$START_STATUS" ]; then
  if [ "$ALLOW_DIRTY" != 1 ]; then
    {
      printf 'local-ci: refusing to run: git status shows changes. Commit or stash them,\n'
      printf 'or pass --allow-dirty to test the working tree as it is. The first lines:\n'
      printf '%s\n' "$START_STATUS" | head -n 15 | sed 's/^/  /'
    } >&2
    exit 2
  fi
  WORKING_TREE="DIRTY (allowed with --allow-dirty): $(printf '%s\n' "$START_STATUS" | grep -c .) changed paths"
fi

COMMIT=$(git rev-parse HEAD)
TREE=$(git rev-parse 'HEAD^{tree}')
BRANCH=$(git symbolic-ref --short -q HEAD || echo "detached HEAD")
SUBJECT=$(git log -1 --format=%s)
BASE_INFO="$BASE_REF not found (run git fetch)"
if BASE_COMMIT=$(git rev-parse --verify -q "$BASE_REF^{commit}"); then
  BASE_INFO="$BASE_REF $BASE_COMMIT ($(git rev-list --count "$BASE_COMMIT..HEAD") commits ahead, $(git rev-list --count "HEAD..$BASE_COMMIT") behind)"
fi

# ---------------------------------------------------------------------------------------------
# Run directory, lock, helpers
# ---------------------------------------------------------------------------------------------

STAMP=$(date +%Y%m%d-%H%M%S)
RUN_ID="local-ci-$STAMP"
[ "$COMMAND" = rerun ] && RUN_ID="local-ci-$STAMP-rerun"
case "$RESULTS_ROOT" in /*) RESULTS_ABS=$RESULTS_ROOT ;; *) RESULTS_ABS=$ROOT/$RESULTS_ROOT ;; esac
RUN_DIR=$RESULTS_ABS/$RUN_ID
STEPS_FILE=$RUN_DIR/steps.tsv
META_FILE=$RUN_DIR/meta.tsv
LOCK_DIR=${TMPDIR:-/tmp}/jittersim-local-ci.lock
LOCK_HELD=0
CHILD=
INTERRUPTED=0
STOP=0
FAILED_STEPS=0
STEP_COUNT=0
WARNINGS=()
PY_SYS=
VENV=
VENV_PY=
PY_VERSION=
PY_STATE=
# The Playwright pin this environment gets. Empty on Python 3.9, which cannot install it.
PLAYWRIGHT_FOR_PY=$PLAYWRIGHT_VERSION
CHROMIUM_STATE=
LAST_RESULT=
ADVISORY_COUNTER=
ADVISORY_RESULT=
STEP_NOTE=

HELPER_PYTHON=$(command -v python3 || command -v python || true)
[ -n "$HELPER_PYTHON" ] || die "python3 is needed to run the helpers in scripts/"

take_lock() {
  [ "$DRY_RUN" = 1 ] && return 0
  local holder
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    holder=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
    if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
      die "another local-ci run (process $holder) holds $LOCK_DIR. Heavy steps run one at a time on this machine."
    fi
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR" 2>/dev/null || die "cannot take the lock $LOCK_DIR"
  fi
  printf '%s\n' "$$" > "$LOCK_DIR/pid"
  LOCK_HELD=1
}

release_lock() {
  if [ "$LOCK_HELD" = 1 ]; then
    rm -rf "$LOCK_DIR"
    LOCK_HELD=0
  fi
}

interrupt() {
  INTERRUPTED=1
  STOP=1
  if [ -n "$CHILD" ]; then kill -s "$1" "$CHILD" 2>/dev/null; fi
}

sanitize() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'
}

meta() {
  [ "$DRY_RUN" = 1 ] && return 0
  printf '%s\t%s\n' "$1" "$2" >> "$META_FILE"
}

warn() {
  WARNINGS+=("$1")
  printf 'local-ci: warning: %s\n' "$1" >&2
}

record_step() {  # id result exit seconds log junit command [note]
  [ "$DRY_RUN" = 1 ] && return 0
  local command_line
  # One line per step: fold white space and cut long commands (the log has the full text).
  command_line=$(printf '%s' "$7" | tr '\n\t' '  ' | tr -s ' ' | cut -c1-300)
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6" "$command_line" "${8:-}" >> "$STEPS_FILE"
}

record_blocked() {  # id message
  local id=$1 message=$2 log
  log=$(sanitize "$id").log
  STEP_COUNT=$((STEP_COUNT + 1))
  FAILED_STEPS=$((FAILED_STEPS + 1))
  printf 'local-ci: %s\n' "$message" >&2
  [ "$DRY_RUN" = 1 ] && return 0
  printf '%s\n' "$message" > "$RUN_DIR/$log"
  record_step "$id" BLOCKED 69 0 "$log" - "blocked before it could start"
  printf '  %-44s BLOCKED\n' "$id"
}

# run_step ID TIMEOUT JUNIT -- COMMAND...   (JUNIT is a path relative to the run directory, or -)
run_step() {
  local id=$1 limit=$2 junit=$3
  shift 4
  LAST_RESULT=
  if [ "$STOP" = 1 ]; then
    record_step "$id" "NOT RUN" - 0 - "$junit" "$*"
    return 0
  fi
  STEP_COUNT=$((STEP_COUNT + 1))
  if [ "$DRY_RUN" = 1 ]; then
    printf 'would run %s:' "$id"
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  [ -n "$STEP_TIMEOUT" ] && limit=$STEP_TIMEOUT
  local log code result started elapsed
  log=$(sanitize "$id").log
  started=$SECONDS
  if [ -t 1 ]; then printf '  %-44s running\r' "$id"; fi
  local helper_args=(--timeout "$limit" --log "$RUN_DIR/$log")
  [ "$ECHO" = 1 ] && helper_args+=(--echo)
  nice -n "$NICE_LEVEL" "$HELPER_PYTHON" "$HELPER" "${helper_args[@]}" -- "$@" &
  CHILD=$!
  wait "$CHILD"
  code=$?
  while kill -0 "$CHILD" 2>/dev/null; do
    wait "$CHILD"
    code=$?
  done
  CHILD=
  elapsed=$((SECONDS - started))
  case "$code" in
    0) result=PASS ;;
    124) result=TIMEOUT ;;
    *) result=FAIL ;;
  esac
  STEP_NOTE=
  if [ -n "$ADVISORY_COUNTER" ]; then
    advisory_result "$ADVISORY_COUNTER" "$RUN_DIR/$log" "$code"
    result=$ADVISORY_RESULT
  elif [ "$INTERRUPTED" = 1 ] && [ "$result" != PASS ]; then
    result=INTERRUPTED
  fi
  LAST_RESULT=$result
  record_step "$id" "$result" "$code" "$elapsed" "$log" "$junit" "$*" "$STEP_NOTE"
  printf '  %-44s %-11s exit %-3s %5ss  %s\n' "$id" "$result" "$code" "$elapsed" "$STEP_NOTE"
  if [ -n "$ADVISORY_COUNTER" ]; then
    :  # an advisory step never fails the run
  elif [ "$result" != PASS ]; then
    FAILED_STEPS=$((FAILED_STEPS + 1))
    if [ "$ECHO" != 1 ]; then
      printf '    last lines of %s:\n' "$log"
      tail -n 12 "$RUN_DIR/$log" | sed 's/^/      | /'
    fi
    [ "$FAIL_FAST" = 1 ] && STOP=1
  fi
  return 0
}

# ---------------------------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------------------------

python_version_of() {
  "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null
}

find_python() {  # $1 = X.Y or empty for the newest
  local wanted=$1 version candidate want
  local wants=("$wanted")
  [ -z "$wanted" ] && wants=(3.13 3.12 3.11 3.10 3.9)
  for want in "${wants[@]}"; do
    for candidate in "python$want" "/opt/homebrew/bin/python$want" "/usr/local/bin/python$want" python3 /usr/bin/python3; do
      command -v "$candidate" >/dev/null 2>&1 || continue
      version=$(python_version_of "$candidate") || continue
      if [ "$version" = "$want" ]; then
        command -v "$candidate"
        return 0
      fi
    done
  done
  return 1
}

prepare_python() {
  [ "$PY_STATE" = ok ] && return 0
  [ "$PY_STATE" = failed ] && return 1
  PY_SYS=$(find_python "$WANT_PYTHON") || die "no Python ${WANT_PYTHON:-3.9 to 3.13} found. Install one (for example brew install python@3.13), pass --python, or use --docker X.Y for unit tests."
  PY_VERSION=$(python_version_of "$PY_SYS")
  case "$PY_VERSION" in 3.9|3.1[0-9]) ;; *) die "Python $PY_VERSION is outside the supported range 3.9 to 3.13" ;; esac
  # Playwright 1.61 and newer need Python 3.10. The hosted Python 3.9 job installs the dev extras
  # only and the tests that need a browser skip there, so the 3.9 environment does the same.
  PLAYWRIGHT_FOR_PY=$PLAYWRIGHT_VERSION
  [ "$PY_VERSION" = 3.9 ] && PLAYWRIGHT_FOR_PY=
  VENV=$ROOT/.venv-local-ci-$PY_VERSION
  VENV_PY=$VENV/bin/python
  export PATH="$VENV/bin:$PATH"
  export VIRTUAL_ENV=$VENV
  export PYTHONUNBUFFERED=1
  unset PYTHONHOME
  if [ "$DRY_RUN" = 1 ]; then
    printf 'would use the virtual environment %s (Python %s from %s)\n' "$VENV" "$PY_VERSION" "$PY_SYS"
    if [ -n "$PLAYWRIGHT_FOR_PY" ]; then
      printf 'would install: the project with its dev extras and playwright==%s\n' "$PLAYWRIGHT_FOR_PY"
    else
      printf 'would install: the project with its dev extras and no Playwright (it needs Python 3.10 or newer, and the hosted Python 3.9 job does not install it either)\n'
    fi
    PY_STATE=ok
    return 0
  fi
  # The script is a single-quoted bash program: its variables belong to the inner shell.
  # shellcheck disable=SC2016
  run_step "setup-venv-py$PY_VERSION" "$TIMEOUT_SETUP" - -- bash -c '
    set -eu
    py=$1; venv=$2; want_playwright=$3
    if [ ! -x "$venv/bin/python" ]; then "$py" -m venv "$venv"; fi
    stamp="$venv/.local-ci-stamp"
    wanted="$(git hash-object pyproject.toml) playwright==$want_playwright"
    if [ "$(cat "$stamp" 2>/dev/null || true)" != "$wanted" ]; then
      "$venv/bin/python" -m pip install --quiet --upgrade pip
      rm -rf build src/*.egg-info
      if [ -n "$want_playwright" ]; then
        "$venv/bin/python" -m pip install --quiet ".[dev]" "playwright==$want_playwright"
      else
        "$venv/bin/python" -m pip install --quiet ".[dev]"
      fi
      printf "%s" "$wanted" > "$stamp"
    fi
    # Install the package itself again so the tests see this commit, as CI installs a wheel.
    rm -rf build src/*.egg-info
    "$venv/bin/python" -m pip install --quiet --force-reinstall --no-deps .
    rm -rf build src/*.egg-info
    "$venv/bin/python" -c "import jittersim, sys; print(jittersim.__file__); sys.exit(0 if \"site-packages\" in jittersim.__file__ else 1)"
  ' setup "$PY_SYS" "$VENV" "$PLAYWRIGHT_FOR_PY"
  if [ "$LAST_RESULT" = PASS ] && [ -x "$VENV_PY" ]; then
    PY_STATE=ok
    return 0
  fi
  PY_STATE=failed
  return 1
}

ensure_chromium() {
  if [ -z "$PLAYWRIGHT_FOR_PY" ]; then
    die "the browser, gui and homepage steps need Python 3.10 or newer: Playwright $PLAYWRIGHT_VERSION does not install on Python $PY_VERSION, and the workflows run these jobs on Python 3.13. Leave --python unset or pass --python 3.13."
  fi
  [ -n "$CHROMIUM_STATE" ] && return 0
  CHROMIUM_STATE=checked
  if [ "$DRY_RUN" = 1 ]; then
    printf 'would run: python -m playwright install chromium (it does nothing when Chromium is already installed)\n'
    return 0
  fi
  run_step "setup-chromium" "$TIMEOUT_SETUP" - -- "$VENV_PY" -m playwright install chromium
}

collect_tools() {
  [ -n "$VENV_PY" ] && [ -x "$VENV_PY" ] || return 0
  local tools="Python $PY_VERSION ($VENV_PY)"
  tools="$tools; ruff $("$VENV_PY" -m ruff --version 2>/dev/null | awk '{print $2}')"
  tools="$tools; pytest $("$VENV_PY" -m pytest --version 2>&1 | awk '{print $2}')"
  local installed
  installed=$("$VENV_PY" -m pip show playwright 2>/dev/null | awk '/^Version:/ {print $2}')
  tools="$tools; playwright ${installed:-not installed}"
  command -v node >/dev/null 2>&1 && tools="$tools; node $(node --version)"
  meta tools "$tools"
  meta python_note "this run covers Python $PY_VERSION only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)"
}

# ---------------------------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------------------------

DOCKER_CHECKED=
DOCKER_OK=0

docker_ready() {  # id
  if [ -z "$DOCKER_CHECKED" ]; then
    DOCKER_CHECKED=1
    if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
      DOCKER_OK=0
      DOCKER_REASON="the Docker daemon is not reachable. Start Docker, then run this command again."
    else
      local cpus total_mb used_mb free_mb running
      cpus=$(docker info --format '{{.NCPU}}' 2>/dev/null)
      total_mb=$(( $(docker info --format '{{.MemTotal}}' 2>/dev/null) / 1048576 ))
      running=$(docker ps -q 2>/dev/null | grep -c .)
      used_mb=$(docker stats --no-stream --format '{{.MemUsage}}' 2>/dev/null | cut -d/ -f1 | awk '
        { n = $1 + 0; u = $1; sub(/^[0-9.]+/, "", u); m = 1
          if (u == "KiB") m = 1/1024; else if (u == "GiB") m = 1024; else if (u == "B") m = 1/1048576
          else if (u == "kB") m = 0.000954; else if (u == "MB") m = 0.9537; else if (u == "GB") m = 953.7
          s += n * m }
        END { printf "%d", s }')
      free_mb=$((total_mb - used_mb))
      DOCKER_INFO="Docker VM: $cpus CPU, $total_mb MiB memory, about $free_mb MiB free, $running containers already running (never stopped or removed by local-ci); each local-ci container is limited to $DOCKER_MEMORY"
      meta docker "$DOCKER_INFO"
      printf '  %s\n' "$DOCKER_INFO"
      if [ "$free_mb" -lt "$DOCKER_MIN_FREE_MB" ] && [ "$IGNORE_DOCKER_MEMORY" != 1 ]; then
        DOCKER_OK=0
        DOCKER_REASON="the Docker VM has about $free_mb MiB free, less than the $DOCKER_MIN_FREE_MB MiB this runner needs, and other containers share it. Free memory, or pass --ignore-docker-memory."
      else
        DOCKER_OK=1
      fi
    fi
  fi
  if [ "$DOCKER_OK" = 1 ]; then return 0; fi
  record_blocked "$1" "$DOCKER_REASON"
  return 1
}

# ---------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------

cmd_lint() {
  prepare_python || return 0
  run_step lint "$TIMEOUT_LINT" - -- "$VENV_PY" -m ruff "${LINT_ARGS[@]}"
  run_advisories
}

# ---------------------------------------------------------------------------------------------
# Advisory checks. They run after the gating ruff step, print how many findings they see and
# never fail the run. A missing tool is installed once (brew, or pip into .venv-local-ci-tools)
# unless --no-install is given. A tool that cannot run is reported as UNAVAILABLE.
# ---------------------------------------------------------------------------------------------

# advisory_result COUNTER LOG EXIT: set ADVISORY_RESULT and STEP_NOTE from the tool's own summary.
advisory_result() {
  local counter=$1 log=$2 code=$3 found=
  ADVISORY_RESULT=ADVISORY
  STEP_NOTE=
  if [ "$code" = 124 ] || [ "$code" = 126 ] || [ "$code" = 127 ]; then
    ADVISORY_RESULT=UNAVAILABLE
    STEP_NOTE="the tool did not finish (exit $code)"
    return
  fi
  case "$counter" in
    install)
      if [ "$code" = 0 ]; then
        ADVISORY_RESULT=INSTALLED
      else
        ADVISORY_RESULT=UNAVAILABLE
        STEP_NOTE="the install failed"
      fi
      return
      ;;
    ruff)
      if grep -q 'All checks passed' "$log"; then
        found=0
      else
        found=$(sed -n 's/^Found \([0-9][0-9]*\) error.*/\1/p' "$log" | tail -n 1)
      fi
      ;;
    lines)  # the output of shellcheck and actionlint: exit 0 is clean, exit 1 has one finding on each matching line
      if [ "$code" = 0 ]; then
        found=0
      elif [ "$code" = 1 ]; then
        found=$(grep -cE '^[^ ]+:[0-9]+:[0-9]+: ' "$log")
      fi
      ;;
    zizmor)
      if grep -q 'No findings to report' "$log"; then
        found=0
      else
        STEP_NOTE=$(grep -E '^[0-9]+ findings?' "$log" | tail -n 1)
        found=$(printf '%s' "$STEP_NOTE" | sed -n 's/^\([0-9][0-9]*\) finding.*/\1/p')
      fi
      ;;
    audit)  # pip-audit, one or more runs in one log
      if grep -q 'Found [0-9]* known vulnerabilit' "$log"; then
        found=$(sed -n 's/^Found \([0-9][0-9]*\) known vulnerabilit.*/\1/p' "$log" | awk '{ s += $1 } END { print s + 0 }')
      elif grep -q 'No known vulnerabilities found' "$log" && [ "$code" = 0 ]; then
        found=0
      fi
      ;;
  esac
  if [ -z "$found" ]; then
    ADVISORY_RESULT=UNAVAILABLE
    STEP_NOTE="no result in the log (exit $code)"
    return
  fi
  if [ -z "$STEP_NOTE" ]; then STEP_NOTE="$found findings"; fi
}

# run_advisory ID COUNTER TIMEOUT -- COMMAND...
run_advisory() {
  local id=$1 counter=$2 limit=$3
  shift 4
  ADVISORY_COUNTER=$counter
  run_step "$id" "$limit" - -- "$@"
  ADVISORY_COUNTER=
}

record_unavailable() {  # id note
  STEP_COUNT=$((STEP_COUNT + 1))
  if [ "$DRY_RUN" = 1 ]; then
    printf 'would skip %s: %s\n' "$1" "$2"
    return 0
  fi
  record_step "$1" UNAVAILABLE - 0 - - "tool not available" "$2"
  printf '  %-44s %-11s %s\n' "$1" UNAVAILABLE "$2"
}

# ensure_brew_tool COMMAND FORMULA: true when COMMAND exists afterwards.
ensure_brew_tool() {
  command -v "$1" >/dev/null 2>&1 && return 0
  if [ "$DRY_RUN" = 1 ]; then
    printf 'would install %s with: brew install %s (only when missing and without --no-install)\n' "$1" "$2"
    return 0
  fi
  [ "$NO_INSTALL" = 1 ] && return 1
  command -v brew >/dev/null 2>&1 || return 1
  ADVISORY_COUNTER=install
  run_step "advisory-install-$1" "$TIMEOUT_SETUP" - -- brew install "$2"
  ADVISORY_COUNTER=
  command -v "$1" >/dev/null 2>&1
}

AUDIT_TOOL=
ensure_pip_audit() {
  AUDIT_TOOL=$(command -v pip-audit 2>/dev/null || true)
  [ -n "$AUDIT_TOOL" ] && return 0
  AUDIT_TOOL=$ROOT/.venv-local-ci-tools/bin/pip-audit
  [ -x "$AUDIT_TOOL" ] && return 0
  if [ "$DRY_RUN" = 1 ]; then
    printf 'would install pip-audit into %s (only when missing and without --no-install)\n' "$ROOT/.venv-local-ci-tools"
    return 0
  fi
  [ "$NO_INSTALL" = 1 ] && return 1
  local tools_python
  tools_python=$(find_python "") || return 1
  ADVISORY_COUNTER=install
  # The program in single quotes runs in its own shell, where its variables are expanded.
  # shellcheck disable=SC2016
  run_step "advisory-install-pip-audit" "$TIMEOUT_SETUP" - -- bash -c '
    set -eu
    "$1" -m venv "$2"
    "$2/bin/python" -m pip install --quiet pip-audit
  ' install "$tools_python" "$ROOT/.venv-local-ci-tools"
  ADVISORY_COUNTER=
  [ -x "$AUDIT_TOOL" ]
}

# The tracked files that are shell scripts, found by the first line so a script without .sh counts.
shell_scripts() {
  # The awk program is in single quotes on purpose. LC_ALL=C lets awk read binary files.
  # shellcheck disable=SC2016
  git ls-files -z | LC_ALL=C xargs -0 awk 'FNR == 1 { if ($0 ~ /^#!.*[ \/](ba|da|k)?sh( |$)/) print FILENAME; nextfile }' 2>/dev/null
}

run_advisories() {
  [ "$ADVISORY" = 1 ] || return 0
  [ "$STOP" = 1 ] && return 0
  printf '  advisory checks (counts only, they never fail the run)\n'
  run_advisory advisory-ruff-full ruff "$TIMEOUT_LINT" -- \
    "$VENV_PY" -m ruff check --statistics --exit-zero --select "$ADVISORY_RUFF_RULES" src tests
  local scripts=() file
  while IFS= read -r file; do
    [ -n "$file" ] && scripts+=("$file")
  done < <(shell_scripts)
  if [ ${#scripts[@]} -eq 0 ]; then
    record_unavailable advisory-shellcheck "no shell scripts found"
  elif ensure_brew_tool shellcheck shellcheck; then
    run_advisory advisory-shellcheck lines "$TIMEOUT_LINT" -- shellcheck -x -f gcc "${scripts[@]}"
  else
    record_unavailable advisory-shellcheck "shellcheck is not installed (brew install shellcheck)"
  fi
  if ensure_brew_tool actionlint actionlint; then
    run_advisory advisory-actionlint lines "$TIMEOUT_LINT" -- actionlint -oneline "$ADVISORY_WORKFLOWS"/*.yml
  else
    record_unavailable advisory-actionlint "actionlint is not installed (brew install actionlint)"
  fi
  if ensure_brew_tool zizmor zizmor; then
    run_advisory advisory-zizmor zizmor "$TIMEOUT_LINT" -- zizmor --offline --no-progress "$ADVISORY_WORKFLOWS"
  else
    record_unavailable advisory-zizmor "zizmor is not installed (brew install zizmor)"
  fi
  if ensure_pip_audit; then
    # The program in single quotes runs in its own shell, where its variables are expanded.
    # shellcheck disable=SC2016
    run_advisory advisory-pip-audit audit "$TIMEOUT_LINT" -- bash -c '
      tool=$1
      shift
      status=0
      for lock in "$@"; do
        printf "== %s\n" "$lock"
        "$tool" --require-hashes --disable-pip -r "$lock" || status=$?
      done
      exit "$status"
    ' pip-audit "$AUDIT_TOOL" "${PIP_AUDIT_LOCKS[@]}"
  else
    record_unavailable advisory-pip-audit "pip-audit is not installed (pip install pip-audit in a tools environment)"
  fi
}

cmd_unit() {
  if [ -n "$DOCKER_PYTHON" ]; then
    docker_pytest "unit-docker-py$DOCKER_PYTHON" "unit-docker-py$DOCKER_PYTHON.xml" "$TIMEOUT_UNIT" ""
    return 0
  fi
  prepare_python || return 0
  run_step "unit-py$PY_VERSION" "$TIMEOUT_UNIT" "unit-py$PY_VERSION.xml" -- \
    "$VENV_PY" -m pytest -p no:cacheprovider -ra "--junitxml=$RUN_DIR/unit-py$PY_VERSION.xml"
}

# docker_pytest ID JUNIT TIMEOUT TARGET... : pytest in a throw-away python:X.Y-slim container
# with the source copied in, so the checkout and its virtual environments stay untouched.
docker_pytest() {
  local id=$1 junit=$2 limit=$3 target=${4:-}
  local image="python:$DOCKER_PYTHON-slim-bookworm"
  if [ "$DRY_RUN" != 1 ]; then docker_ready "$id" || return 0; fi
  # The program in single quotes runs in the container, where its variables are expanded.
  # shellcheck disable=SC2016
  run_step "$id" "$limit" "$junit" -- \
    docker run --rm --init --memory "$DOCKER_MEMORY" --cpus 1 \
      -v "$ROOT:/src:ro" -v "$RUN_DIR:/out" -e PYTHONUNBUFFERED=1 "$image" bash -c '
        set -eu
        mkdir /work
        tar -C /src --exclude=./.venv-local-ci-* --exclude=./test-results --exclude=./node_modules -cf - . | tar -C /work -xf -
        cd /work
        python -m pip install --quiet ".[dev]"
        python -m pytest -p no:cacheprovider -ra "--junitxml=/out/$1" "${@:2}"
      ' pytest "$junit" ${target:+"$target"} ${EXTRA[@]+"${EXTRA[@]}"}
}

run_browser_module() {  # module
  run_step "browser:$1" "$TIMEOUT_MODULE" - -- "$VENV_PY" -m "tests.browser.$1"
}

cmd_browser() {
  choose_browser_modules
  prepare_python || return 0
  ensure_chromium
  meta scope "$SCOPE"
  local module
  for module in ${SELECTED_MODULES[@]+"${SELECTED_MODULES[@]}"}; do
    run_browser_module "$module"
  done
  if [ ${#SELECTED_MODULES[@]} -eq 0 ]; then
    printf '  no browser module is mapped to the changed files, so none ran\n'
  fi
}

cmd_gui() {
  prepare_python || return 0
  ensure_chromium
  run_step gui-pytest "$TIMEOUT_GUI" gui-pytest.xml -- \
    "$VENV_PY" -m pytest -p no:cacheprovider -ra "--junitxml=$RUN_DIR/gui-pytest.xml" "${GUI_PYTEST_FILES[@]}"
  run_step gui-baseline "$TIMEOUT_GUI" - -- "$VENV_PY" -m "tests.browser.${GUI_MODULES[0]}" --output "$RUN_DIR/gui-baseline"
  run_step gui-axe "$TIMEOUT_GUI" - -- "$VENV_PY" -m "tests.browser.${GUI_MODULES[1]}" --output "$RUN_DIR/gui-axe"
}

cmd_homepage() {
  prepare_python || return 0
  ensure_chromium
  command -v npm >/dev/null 2>&1 || { record_blocked homepage-npm "npm is not installed, and the Homepage reference job needs it."; return 0; }
  run_step homepage-npm-ci "$TIMEOUT_SETUP" - -- npm ci --ignore-scripts --prefix "$HOMEPAGE_NODE_PREFIX"
  run_step homepage-pytest "$TIMEOUT_GUI" homepage-pytest.xml -- \
    "$VENV_PY" -m pytest -p no:cacheprovider -ra "--junitxml=$RUN_DIR/homepage-pytest.xml" "$HOMEPAGE_PYTEST_FILE"
  run_step homepage-reference "$TIMEOUT_GUI" - -- "$VENV_PY" -m "tests.browser.$HOMEPAGE_MODULE"
  local script
  for script in "${HOMEPAGE_NODE_SCRIPTS[@]}"; do
    run_step "homepage-node-$(basename "$script" .cjs)" "$TIMEOUT_GUI" - -- \
      env "JITTERSIM_PLAYWRIGHT_MODULE=$ROOT/$HOMEPAGE_NODE_PREFIX/node_modules/playwright" node "$script"
  done
}

cmd_kernel() {
  if [ "$DRY_RUN" != 1 ]; then docker_ready kernel || return 0; fi
  run_step kernel "$TIMEOUT_DOCKER" - -- "./$KERNEL_SCRIPT"
}

docker_run_name() {  # the arguments after docker run -> a short step name
  local last=${*: -1}
  if [ "$last" = "$RELEASE_IMAGE" ]; then printf 'image-default-command'; return; fi
  last=${last##*/}
  last=${last%.py}
  printf '%s' "${last##*.}"
}

cmd_release_checks() {
  prepare_python || return 0
  run_step "release-locked-wheels-py$PY_VERSION" "$TIMEOUT_DOCKER" - -- \
    "$VENV_PY" deploy/raspberry-pi/prepare-locked-wheels.py --source . --output "$RUN_DIR/locked-wheels-py$PY_VERSION"
  if [ "$DRY_RUN" != 1 ]; then docker_ready release-docker || return 0; fi
  run_step release-docker-build "$TIMEOUT_DOCKER" - -- docker build -f tests/wifi/Dockerfile -t "$RELEASE_IMAGE" .
  local entry parts
  for entry in "${RELEASE_DOCKER_RUNS[@]}"; do
    read -r -a parts <<<"$entry"
    run_step "release-$(docker_run_name "${parts[@]}")" "$TIMEOUT_DOCKER" - -- \
      docker run --memory "$DOCKER_MEMORY" "${parts[@]}"
  done
}

cmd_all() {
  cmd_lint
  cmd_unit
  cmd_browser
  cmd_gui
  cmd_homepage
  cmd_kernel
  cmd_release_checks
}

cmd_rerun() {
  local targets=()
  if [ "$FAILED_MODE" = 1 ]; then
    local previous=${TARGETS[0]:-}
    if [ -z "$previous" ]; then
      local candidate
      for candidate in "$RESULTS_ABS"/local-ci-*; do
        case "$candidate" in *-rerun) ;; *) [ -f "$candidate/steps.tsv" ] && previous=$candidate ;; esac
      done
    fi
    [ -n "$previous" ] && [ -f "$previous/steps.tsv" ] || die "no earlier run found to read failures from (pass its directory)"
    while IFS= read -r line; do
      [ -n "$line" ] && targets+=("$line")
    done < <("$HELPER_PYTHON" "$REPORT" failed "$previous")
    [ ${#targets[@]} -gt 0 ] || { printf 'local-ci: %s has no failed test or browser module to re-run\n' "$previous"; return 0; }
  else
    local item
    for item in ${TARGETS[@]+"${TARGETS[@]}"}; do targets+=("$item"); done
  fi
  [ ${#targets[@]} -gt 0 ] || die "rerun needs a pytest node id, a browser module name, or --failed"
  local target number=0 module junit
  local needs_venv=0
  if [ -z "$DOCKER_PYTHON" ]; then needs_venv=1; fi
  for target in "${targets[@]}"; do
    case "$target" in
      browser:*|tests.browser.*) needs_venv=1 ;;
      *) is_browser_module "$target" && needs_venv=1 ;;
    esac
  done
  [ "$needs_venv" = 1 ] && { prepare_python || return 0; }
  for target in "${targets[@]}"; do
    number=$((number + 1))
    module=
    case "$target" in
      browser:*) module=${target#browser:} ;;
      tests.browser.*) module=${target#tests.browser.} ;;
      *::*|tests/*) ;;
      *) is_browser_module "$target" && module=$target ;;
    esac
    if [ -n "$module" ]; then
      is_browser_module "$module" || die "$module is not a browser module (see: scripts/local-ci.sh list)"
      ensure_chromium
      run_browser_module "$module"
      continue
    fi
    case "$target" in
      *::*|tests/*) ;;
      *) die "cannot tell what $target is: give a pytest node id such as tests/unit/test_x.py::test_y, or a browser module" ;;
    esac
    junit="rerun-$number.xml"
    if [ -n "$DOCKER_PYTHON" ]; then
      docker_pytest "rerun-docker:$target" "$junit" "$TIMEOUT_UNIT" "$target"
    else
      run_step "rerun:$target" "$TIMEOUT_UNIT" "$junit" -- \
        "$VENV_PY" -m pytest -p no:cacheprovider -ra "--junitxml=$RUN_DIR/$junit" "$target" ${EXTRA[@]+"${EXTRA[@]}"}
    fi
  done
}

# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------

finish() {
  local code=$?
  trap - EXIT
  if [ "$DRY_RUN" != 1 ] && [ -d "$RUN_DIR" ] && [ -f "$STEPS_FILE" ]; then
    local end_status
    end_status=$(tree_status)
    if [ "$end_status" != "$START_STATUS" ]; then
      warn "the run changed the working tree: $(printf '%s\n' "$end_status" | head -n 5 | tr '\n' ';')"
    fi
    meta finished "$(date '+%Y-%m-%d %H:%M:%S %z')"
    if [ ${#WARNINGS[@]} -gt 0 ]; then
      local joined=
      local item
      for item in "${WARNINGS[@]}"; do joined="$joined$item || "; done
      meta warnings "$joined"
    fi
    printf '\n'
    "$HELPER_PYTHON" "$REPORT" summary "$RUN_DIR"
    printf '\nSummary: %s/summary.md\n' "$RUN_DIR"
  fi
  release_lock
  if [ "$INTERRUPTED" = 1 ]; then exit 130; fi
  if [ "$code" = 0 ] && [ "$FAILED_STEPS" -gt 0 ]; then exit 1; fi
  exit "$code"
}

trap 'interrupt INT' INT
trap 'interrupt TERM' TERM
trap finish EXIT

case "$COMMAND" in lint) ;; *) take_lock ;; esac

if [ "$DRY_RUN" != 1 ]; then
  mkdir -p "$RUN_DIR" || die "cannot create $RUN_DIR"
  : > "$STEPS_FILE"
  : > "$META_FILE"
  meta run_id "$RUN_ID"
  meta commit "\`$COMMIT\` (branch \`$BRANCH\`): $SUBJECT"
  meta tree "\`$TREE\`"
  meta working_tree "$WORKING_TREE"
  meta base "$BASE_INFO"
  meta command "scripts/local-ci.sh $ORIGINAL_ARGS"
  meta scope "$SCOPE"
  meta host "$(uname -s) $(uname -r) $(uname -m)"
  meta started "$(date '+%Y-%m-%d %H:%M:%S %z')"
  meta results "$RUN_DIR"
  printf 'Testing commit %s (%s)\n  %s\n  working tree: %s\n  results: %s\n\n' \
    "$COMMIT" "$BRANCH" "$SUBJECT" "$WORKING_TREE" "$RUN_DIR"
else
  printf 'Dry run: commands are printed and nothing runs. Commit %s, working tree: %s\n' "$COMMIT" "$WORKING_TREE"
fi

case "$COMMAND" in
  lint) cmd_lint ;;
  unit) cmd_unit ;;
  browser) cmd_browser ;;
  gui) cmd_gui ;;
  homepage) cmd_homepage ;;
  kernel) cmd_kernel ;;
  release-checks) cmd_release_checks ;;
  all) cmd_all ;;
  rerun) cmd_rerun ;;
esac

collect_tools
exit 0

#!/bin/sh
# Build the release archive of one commit on this Mac, copy it to the Pi and stage it for the
# verified update service. With --install it also installs it and shows the release the Pi
# runs afterwards. Only git, ssh, scp, tar and shasum are needed: not the Python package.
# The script never reads, stores or passes a password. The steps that need root run in one
# `ssh -t` session so the Pi asks for its sudo password in this terminal.
# It is plain POSIX sh so it runs with the bash 3.2 that macOS ships and with dash.
set -eu

DEFAULT_HOST=jittersim@192.168.0.17
DEFAULT_KEY="$HOME/.ssh/jittersim_ed25519"
# The update service's own copy of src/jittersim/updates.py. It runs with the system Python.
MANAGER=/usr/local/lib/jittersim-update.py
# Every ssh and scp call: a short connection timeout and a dead connection noticed within
# 45 seconds. Host keys come from the user's known_hosts as they are.
CONNECTION_OPTIONS="-o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
-o IdentitiesOnly=yes"
# Whole-call limits in seconds for the calls that never ask anything.
CHECK_SECONDS=60
COPY_SECONDS=600
UNPACK_SECONDS=180
NETWORK_HINT="Deploying needs a connection to the Pi's router network: Ethernet or the \
router's Wi-Fi. A Mac on the Pi's test Wi-Fi cannot reach the Pi over SSH."

usage() {
    cat <<EOF
usage: deploy-from-mac.sh [--ref REF] [--host USER@HOST] [--key PATH] [--install]
                          [--use-installer] [--dry-run]

Runs on your Mac in a JitterSim checkout. It builds the release archive of REF with
build-release.sh, copies it to the Pi over SSH and stages it for the verified update
service. Staging checks the checksum and every file of the archive. Then 'sudo jittersim
update install' or the dashboard's Install staged release installs it. Staging needs root
on the Pi so the Pi asks for its sudo password in this terminal.

  --ref REF          tag, branch or commit to release (default: HEAD). The archive is built
                     from the commit so uncommitted changes are left out.
  --host USER@HOST   the Pi's SSH login on its router network address
                     (default: $DEFAULT_HOST)
  --key PATH         the SSH key for that login (default: ~/.ssh/jittersim_ed25519)
  --install          install the staged release too and show the release the Pi runs.
                     It runs in the same sudo session as the staging.
  --use-installer    unpack the archive on the Pi and use its full installer instead of the
                     update service. Needed once on a Pi without the update service and for
                     OS package or Wi-Fi changes.
  --dry-run          resolve REF and print every command without running any of them.
  -h, --help         show this help

Deploying needs a connection to the Pi's router network (Ethernet or the router's Wi-Fi). A
Mac on the Pi's test Wi-Fi cannot reach the Pi over SSH. Every ssh and scp call uses your
known_hosts as it is: connect once with 'ssh -i KEY USER@HOST' to check and save the Pi's
host key. Only the sudo step opens a terminal on the Pi. The release is named after the
short commit: compare it with 'git rev-parse --short REF'.
EOF
}

fail() {
    printf 'deploy-from-mac: %s\n' "$1" >&2
    exit "${2:-1}"
}

# Quote one argument for a POSIX shell, also for the remote shell that ssh hands it to.
# Plain words stay as they are so the printed commands are easy to read.
quote() {
    case $1 in
        ''|*[!A-Za-z0-9_./:=@%+,-]*) ;;
        *) printf '%s' "$1"; return 0 ;;
    esac
    rest=$1
    quoted=
    while :; do
        case $rest in
            *\'*)
                quoted="$quoted${rest%%\'*}'\\''"
                rest=${rest#*\'}
                ;;
            *)
                quoted="$quoted$rest"
                break
                ;;
        esac
    done
    printf "'%s'" "$quoted"
}

# Print a command with every argument quoted.
words() {
    line=
    for word in "$@"; do
        line="$line $(quote "$word")"
    done
    printf '%s' "${line# }"
}

# Show a command and run it unless this is a dry run.
run() {
    printf '+ %s\n' "$(words "$@")"
    if [ "$dry_run" = yes ]; then
        return 0
    fi
    "$@"
}

# Run a command that never asks anything and stop it after LIMIT seconds. It returns 124
# when the limit stopped it, like timeout(1) which macOS does not have.
with_deadline() {
    limit=$1
    shift
    rm -f "$work/deadline"
    "$@" &
    child=$!
    (
        trap 'kill "$sleeper" 2>/dev/null; exit 0' TERM
        sleep "$limit" &
        sleeper=$!
        wait "$sleeper"
        if kill -TERM "$child" 2>/dev/null; then
            : > "$work/deadline"
        fi
    ) </dev/null >/dev/null 2>&1 &
    watchdog=$!
    # Keep the caller's errexit setting: it may be checking the result itself.
    case $- in
        *e*) errexit=yes ;;
        *) errexit=no ;;
    esac
    set +e
    wait "$child"
    result=$?
    kill -TERM "$watchdog" 2>/dev/null
    wait "$watchdog" 2>/dev/null
    if [ "$errexit" = yes ]; then
        set -e
    fi
    if [ -e "$work/deadline" ]; then
        rm -f "$work/deadline"
        return 124
    fi
    return "$result"
}

# Show a command and run it with a deadline unless this is a dry run.
run_limited() {
    limit=$1
    shift
    printf '+ %s\n' "$(words "$@")"
    if [ "$dry_run" = yes ]; then
        return 0
    fi
    with_deadline "$limit" "$@"
}

ref=HEAD
host=$DEFAULT_HOST
key=$DEFAULT_KEY
install=no
use_installer=no
dry_run=no
while [ "$#" -gt 0 ]; do
    case $1 in
        --ref|--host|--key)
            [ "$#" -ge 2 ] || fail "$1 needs a value" 2
            case $1 in
                --ref) ref=$2 ;;
                --host) host=$2 ;;
                --key) key=$2 ;;
            esac
            shift 2
            ;;
        --install) install=yes; shift ;;
        --use-installer) use_installer=yes; shift ;;
        --dry-run) dry_run=yes; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

# A host is USER@NAME or NAME with ASCII letters, digits, dots, dashes and underscores only.
# It never starts with a dash so ssh cannot read it as an option and it holds nothing a
# shell would interpret. The C locale keeps bash from counting other letters as letters.
valid_host() (
    LC_ALL=C
    export LC_ALL
    case $1 in
        ''|-*|*@|@*|*@*@*|*@-*|*[!A-Za-z0-9._@-]*) return 1 ;;
    esac
    return 0
)
valid_host "$host" \
    || fail "refusing host '$host': use USER@HOST with letters, digits, dots, dashes and underscores" 2
case $key in
    ''|-*) fail "refusing key path '$key'" 2 ;;
esac
[ -f "$key" ] || fail "the SSH key $key does not exist" 2
case $ref in
    ''|-*) fail "refusing ref '$ref'" 2 ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(git -C "$script_dir" rev-parse --show-toplevel) || fail "run this from a JitterSim checkout"
commit=$(git -C "$repo" rev-parse --verify --quiet "$ref^{commit}") || fail "unknown ref '$ref'" 2
short=$(git -C "$repo" rev-parse --short "$commit")
case $short in
    *[!0-9a-f]*|'') fail "unexpected commit name '$short'" ;;
esac
release="jittersim-release-$short"
remote_archive="$release.tar.gz"

printf 'Commit: %s (%s)\n' "$commit" "$short"
if [ -n "$(git -C "$repo" status --porcelain --untracked-files=no)" ]; then
    printf 'Uncommitted changes in this checkout are not part of the archive.\n'
fi

# shellcheck disable=SC2086 # the options are single words on purpose
ssh_batch() {
    run_limited "$1" ssh -o BatchMode=yes $CONNECTION_OPTIONS -i "$key" -- "$host" "$2"
}

# The same call for an answer that is read: the command is shown first because the answer
# is captured.
# shellcheck disable=SC2086
show_ssh_batch() {
    printf '+ %s\n' "$(words ssh -o BatchMode=yes $CONNECTION_OPTIONS -i "$key" -- "$host" "$1")"
}

# shellcheck disable=SC2086
ssh_batch_answer() {
    with_deadline "$1" ssh -o BatchMode=yes $CONNECTION_OPTIONS -i "$key" -- "$host" "$2"
}

# shellcheck disable=SC2086
ssh_terminal() {
    run ssh -t $CONNECTION_OPTIONS -i "$key" -- "$host" "$1"
}

unreachable() {
    if [ "$1" -eq 124 ]; then
        fail "$2 did not finish within $3 seconds. $NETWORK_HINT"
    fi
    fail "could not reach $host over SSH (exit $1). $NETWORK_HINT Also check the key and that the Pi's host key is in your known_hosts: connect once with ssh -i $(quote "$key") $host"
}

if [ "$dry_run" = yes ]; then
    work="${TMPDIR:-/tmp}/jittersim-deploy.XXXXXX"
    sha='SHA256_OF_THE_ARCHIVE'
else
    work=$(mktemp -d "${TMPDIR:-/tmp}/jittersim-deploy.XXXXXX")
    trap 'rm -rf "$work"' EXIT
    trap 'exit 1' HUP INT TERM
fi
# scp reads a relative path with a colon as a remote one.
case $work in
    /*) ;;
    *) work="$(pwd)/$work" ;;
esac
archive="$work/$remote_archive"

# build-release.sh reads the paths relative to the checkout so it runs from its top. It
# prints the archive's SHA-256 checksum and this script checks that value with shasum.
printf '+ %s\n' "$(words sh deploy/raspberry-pi/build-release.sh "$commit" "$archive")"
if [ "$dry_run" = no ]; then
    built=$(cd "$repo" && sh deploy/raspberry-pi/build-release.sh "$commit" "$archive") \
        || fail "building the release archive failed"
    reported=$(printf '%s\n' "$built" | tail -n 1 | cut -d ' ' -f 1)
    if command -v shasum >/dev/null 2>&1; then
        sha=$(shasum -a 256 "$archive" | cut -d ' ' -f 1)
    else
        sha=$(sha256sum "$archive" | cut -d ' ' -f 1)
    fi
    case $sha in
        *[!0-9a-f]*|'') fail "could not compute the archive checksum" ;;
    esac
    [ "${#sha}" -eq 64 ] || fail "could not compute the archive checksum"
    [ "$reported" = "$sha" ] \
        || fail "build-release.sh printed the checksum '$reported' but the archive has $sha"
fi
printf 'Archive: %s\nSHA-256: %s\n' "$remote_archive" "$sha"

# One look at the Pi before copying: can it be reached, does it have the update service and
# which release does it run? `jittersim update check` needs no sudo.
printf 'Connecting to %s with the key %s.\n' "$host" "$key"
probe="if $(words test -f "$MANAGER"); then echo update-service; fi; \
$(words jittersim update check) 2>/dev/null || true"
show_ssh_batch "$probe"
if [ "$dry_run" = yes ]; then
    answer=update-service
else
    set +e
    answer=$(ssh_batch_answer "$CHECK_SECONDS" "$probe")
    status=$?
    set -e
    [ "$status" -eq 0 ] || unreachable "$status" "Checking the Pi" "$CHECK_SECONDS"
fi
running=$(printf '%s\n' "$answer" | sed -n 's/^Installed: //p' | head -n 1)
if [ "$use_installer" = no ]; then
    case $answer in
        *update-service*) ;;
        *) fail "the Pi has no update service yet. Run this once with --use-installer." ;;
    esac
    if [ "$running" = "$short" ]; then
        printf 'The Pi already runs release %s. Nothing to stage or install.\n' "$short"
        exit 0
    fi
elif [ "$running" = "$short" ]; then
    printf 'The Pi already runs release %s. The installer runs again.\n' "$short"
fi

set +e
# shellcheck disable=SC2086
run_limited "$COPY_SECONDS" scp -o BatchMode=yes $CONNECTION_OPTIONS -i "$key" -- \
    "$archive" "$host:$remote_archive"
status=$?
set -e
if [ "$status" -eq 124 ]; then
    unreachable 124 "Copying the archive" "$COPY_SECONDS"
elif [ "$status" -ne 0 ]; then
    fail "copying the archive to the Pi failed (exit $status). $NETWORK_HINT"
fi

# Read the release label the Pi reports and compare it with this commit. The helper may still
# be starting after an install so the check is tried a few times.
check_release() {
    check=$(words jittersim update check)
    show_ssh_batch "$check"
    if [ "$dry_run" = yes ]; then
        return 0
    fi
    attempt=1
    while :; do
        if report=$(ssh_batch_answer "$CHECK_SECONDS" "$check" 2>"$work/check.err"); then
            installed=$(printf '%s\n' "$report" | sed -n 's/^Installed: //p' | head -n 1)
            if [ "$installed" = "$short" ]; then
                printf 'The Pi now runs release %s. Compare it with: git rev-parse --short %s\n' \
                    "$short" "$(quote "$ref")"
                return 0
            fi
            printf '%s\n' "$report"
            fail "the Pi reports release '$installed' instead of $short. $1"
        fi
        [ "$attempt" -lt 10 ] || break
        attempt=$((attempt + 1))
        sleep 3
    done
    # The last answer says why, for example a login that may not use the helper.
    sed -n '1,5p' "$work/check.err" >&2
    fail "could not read the installed release. On the Pi run: jittersim update check"
}

finish_dry_run() {
    if [ "$dry_run" = yes ]; then
        printf 'Dry run: nothing was built, copied or run on the Pi.\n'
        exit 0
    fi
}

if [ "$use_installer" = yes ]; then
    # Check the copy on the Pi, unpack it as the login user and label it so the installer
    # records the release in /opt/jittersim/.jittersim-release.json. The unpacked folder
    # stays in the home folder like a release unpacked by hand.
    unpack="printf '%s  %s\\n' $(quote "$sha") $(quote "$remote_archive") | sha256sum -c - \
&& rm -rf $(quote "$release") && mkdir $(quote "$release") \
&& tar -xzf $(quote "$remote_archive") -C $(quote "$release") \
&& printf '{\"version\": \"%s\", \"sha256\": \"%s\"}\\n' $(quote "$short") $(quote "$sha") \
> $(quote "$release/.jittersim-release.json") && rm -f $(quote "$remote_archive")"
    set +e
    ssh_batch "$UNPACK_SECONDS" "$unpack"
    status=$?
    set -e
    if [ "$status" -eq 124 ]; then
        unreachable 124 "Unpacking on the Pi" "$UNPACK_SECONDS"
    elif [ "$status" -ne 0 ]; then
        fail "checking or unpacking the archive on the Pi failed"
    fi
    install_command=$(words sudo "./$release/deploy/raspberry-pi/install.sh")
    if [ "$install" = no ]; then
        finish_dry_run
        printf 'Unpacked %s on the Pi. Install it with:\n  %s\n' "$release" \
            "$(words ssh -t -i "$key" -- "$host" "$install_command")"
        exit 0
    fi
    ssh_terminal "$install_command" || fail "the installer on the Pi failed. Read its messages above."
    check_release "Read the installer messages above."
    finish_dry_run
    exit 0
fi

stage="$(words sudo /usr/bin/python3 -I "$MANAGER" stage "$remote_archive" --version "$short" \
    --sha256 "$sha") && $(words rm -f "$remote_archive")"
if [ "$install" = no ]; then
    ssh_terminal "$stage" || fail "staging on the Pi failed. Read the message above."
    finish_dry_run
    printf 'Staged release %s on the Pi. Install it with:\n  %s\n' "$short" \
        "$(words ssh -t -i "$key" -- "$host" "$(words sudo jittersim update install)")"
    printf 'or choose Check for updates and then Install staged release in the dashboard.\n'
    exit 0
fi
# `jittersim update install` queues the one-shot update service. `systemctl start` joins that
# queued run, waits for it and fails when the update fails. If the run already ended the
# unit's condition (a queued request) is false and the start does nothing: the release
# check below then shows what the Pi runs.
ssh_terminal "$stage && $(words sudo jittersim update install --yes) \
&& $(words echo 'Waiting for the update service to install and check the release.') \
&& $(words sudo systemctl start jittersim-update.service)" \
    || fail "staging or installing on the Pi failed. Read the messages above. On the Pi check: jittersim update check and sudo journalctl -u jittersim-update.service -n 80 --no-pager"
check_release "Check: sudo journalctl -u jittersim-update.service -n 80 --no-pager"
finish_dry_run

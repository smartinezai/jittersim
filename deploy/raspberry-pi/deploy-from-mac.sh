#!/bin/sh
# Build the release archive of one commit on this Mac, copy it to the Pi and stage it for the
# verified update service. With --install it also installs it and shows the release the Pi
# runs afterwards. Only git, ssh, scp, tar and shasum are needed: not the Python package.
# The script never reads, stores or passes a password. The steps that need root run in one
# `ssh -t` session so the Pi asks for its sudo password in this terminal. A Pi with signed
# releases and passwordless staging runs them without a password: it installs only archives
# signed by the owner's release signing key.
# It is plain POSIX sh so it runs with the bash 3.2 that macOS ships and with dash.
set -eu

DEFAULT_HOST=jittersim@192.168.0.17
DEFAULT_KEY="$HOME/.ssh/jittersim_ed25519"
DEFAULT_SIGNING_KEY="$HOME/.ssh/jittersim_release_ed25519"
# The update service's own copy of src/jittersim/updates.py. It runs with the system Python.
MANAGER=/usr/local/lib/jittersim-update.py
# Signed releases: the trusted keys on the Pi, the one archive path passwordless staging
# accepts (in the jittersim login's home) and the commands that stage and install it.
SIGNERS=/etc/jittersim/release-signers
INCOMING=incoming/jittersim-release.tar.gz
INCOMING_PATH=/home/jittersim/$INCOMING
COMMAND=/usr/local/bin/jittersim
# Every ssh and scp call: a connection timeout of 8 seconds and a dead connection noticed
# within 45 seconds. Host keys come from the user's known_hosts as they are.
CONNECTION_OPTIONS="-o ConnectTimeout=8 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
-o IdentitiesOnly=yes"
# Whole-call limits in seconds for the calls that never ask anything.
CHECK_SECONDS=60
COPY_SECONDS=600
UNPACK_SECONDS=180
# Passwordless staging and the install that waits for the update service (15 minutes at
# most) run without a terminal so they get a limit too.
STAGE_SECONDS=300
INSTALL_SECONDS=1500
NETWORK_HINT="Deploying needs a connection to the Pi's router network: Ethernet or the \
router's Wi-Fi. A Mac on the Pi's test Wi-Fi cannot reach the Pi over SSH."

usage() {
    cat <<EOF
usage: deploy-from-mac.sh [--ref REF] [--host USER@HOST] [--key PATH] [--signing-key PATH]
                          [--install] [--use-installer] [--skip-network-check] [--dry-run]
                          [--https | --ssh] [--url URL] [--ca-cert FILE] [--save-token]

Runs on your Mac in a JitterSim checkout. It builds the release archive of REF with
build-release.sh, signs it, copies it to the Pi over SSH and stages it for the verified
update service. Staging checks the signature, the release order and every file of the
archive. Then 'sudo jittersim update install' or the dashboard's Install staged release
installs it. Staging needs root on the Pi so the Pi asks for its sudo password in this
terminal unless passwordless staging is on.

  --ref REF          tag, branch or commit to release (default: HEAD). The archive is built
                     from the commit so uncommitted changes are left out.
  --host USER@HOST   the Pi's SSH login on its router network address
                     (default: $DEFAULT_HOST)
  --key PATH         the SSH key for that login (default: ~/.ssh/jittersim_ed25519)
  --signing-key PATH the release signing key (default: ~/.ssh/jittersim_release_ed25519).
                     make-release-key.sh creates it. Without it the archive is not signed
                     and only a Pi without signed releases or --use-installer accepts it.
  --install          install the staged release too and show the release the Pi runs.
                     It runs in the same sudo session as the staging.
  --use-installer    unpack the archive on the Pi and use its full installer instead of the
                     update service. Needed once on a Pi without the update service and for
                     OS package or Wi-Fi changes.
  --skip-network-check
                     do not treat an address in 192.168.50.0/24 as the Pi's test Wi-Fi. On
                     the test Wi-Fi SSH is blocked on purpose so the script deploys over
                     HTTPS instead, or stops early with --ssh or --use-installer. Use this
                     only when that address is not the test Wi-Fi.
  --dry-run          resolve REF and print every command without running any of them.
  --https            deploy through the Pi's HTTPS dashboard instead of SSH (also chosen when
                     this Mac is on the Pi's test Wi-Fi, 192.168.50.0/24, or SSH does not
                     answer). deploy-over-https.sh does it: the same signed archive goes to
                     the dashboard, which accepts only a release the Pi trusts. It needs the
                     admin token (JITTERSIM_TOKEN or the Keychain item jittersim-admin-token)
                     and the Pi's certificate (--ca-cert FILE or 'jittersim connect').
  --ssh              never switch to HTTPS, even when SSH does not answer.
  --url URL          the dashboard address for the HTTPS path (https://PI:8080).
  --ca-cert FILE     the Pi's dashboard certificate for the HTTPS path.
  --save-token       store the admin token in the macOS Keychain and stop.
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
    # Background calls ignore Ctrl-C so it stops them here.
    trap 'kill -TERM "$child" "$watchdog" 2>/dev/null; exit 1' HUP INT TERM
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
    trap 'exit 1' HUP INT TERM
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

# The command that retries this run, for the message of a stop that is not a fault.
retry="deploy/raspberry-pi/deploy-from-mac.sh"
for word in "$@"; do
    retry="$retry $(quote "$word")"
done

ref=HEAD
transport=auto
https_url=
https_cert=
host=$DEFAULT_HOST
key=$DEFAULT_KEY
signing_key=$DEFAULT_SIGNING_KEY
install=no
use_installer=no
dry_run=no
skip_network_check=no
while [ "$#" -gt 0 ]; do
    case $1 in
        --https) transport=https; shift ;;
        --ssh) transport=ssh; shift ;;
        --save-token)
            exec sh "$(dirname -- "$0")/deploy-over-https.sh" --save-token
            ;;
        --ref|--host|--key|--signing-key|--url|--ca-cert)
            [ "$#" -ge 2 ] || fail "$1 needs a value" 2
            case $1 in
                --url) https_url=$2 ;;
                --ca-cert) https_cert=$2 ;;
                --ref) ref=$2 ;;
                --host) host=$2 ;;
                --key) key=$2 ;;
                --signing-key) signing_key=$2 ;;
            esac
            shift 2
            ;;
        --install) install=yes; shift ;;
        --use-installer) use_installer=yes; shift ;;
        --dry-run) dry_run=yes; shift ;;
        --skip-network-check) skip_network_check=yes; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

# Is this Mac on the Pi's test Wi-Fi (192.168.50.0/24, the Pi at 192.168.50.1)? The Pi
# blocks SSH from there on purpose so a connection would only hang until it times out. The
# answer comes from the Mac's own addresses and default route: nothing is sent anywhere.
on_test_wifi() {
    case ${host#*@} in
        192.168.50.1) return 0 ;;
    esac
    if command -v ifconfig >/dev/null 2>&1; then
        addresses=$(ifconfig 2>/dev/null || true)
    elif command -v ip >/dev/null 2>&1; then
        addresses=$(ip -4 addr 2>/dev/null || true)
    else
        addresses=
    fi
    if printf '%s\n' "$addresses" \
        | grep -Eq '(^|[[:space:]])inet (addr:)?192\.168\.50\.[0-9]{1,3}([^0-9]|$)'; then
        return 0
    fi
    if command -v route >/dev/null 2>&1; then
        gateway=$(route -n get default 2>/dev/null || true)
    elif command -v ip >/dev/null 2>&1; then
        gateway=$(ip route show default 2>/dev/null || true)
    else
        gateway=
    fi
    if printf '%s\n' "$gateway" | grep -Eq '(gateway:|via)[[:space:]]+192\.168\.50\.1([^0-9]|$)'; then
        return 0
    fi
    return 1
}
# Choose the way to reach the Pi. On the Pi's test Wi-Fi (192.168.50.0/24) SSH is blocked on
# purpose, so the signed release goes through the HTTPS dashboard. Elsewhere SSH is tried
# first and the HTTPS dashboard is the fallback when SSH does not answer.
over_https() {
    set -- --ref "$ref" --host "$host" --signing-key "$signing_key"
    [ -z "$https_url" ] || set -- "$@" --url "$https_url"
    [ -z "$https_cert" ] || set -- "$@" --ca-cert "$https_cert"
    [ "$install" = no ] || set -- "$@" --install
    [ "$dry_run" = no ] || set -- "$@" --dry-run
    exec sh "$(dirname -- "$0")/deploy-over-https.sh" "$@"
}
if [ "$use_installer" = no ]; then
    if [ "$transport" = https ]; then
        over_https
    elif [ "$transport" = auto ]; then
        if [ "$skip_network_check" = no ] && on_test_wifi; then
            printf 'On the Pi Wi-Fi: SSH is blocked on purpose there, so this deploys over HTTPS instead.\n'
            over_https
        fi
    fi
fi

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
if [ ! -f "$key" ]; then
    if [ "$transport" = auto ] && [ "$use_installer" = no ]; then
        printf 'There is no SSH key %s. Using the HTTPS upload instead.\n' "$key"
        over_https
    fi
    fail "the SSH key $key does not exist" 2
fi
case $signing_key in
    ''|-*) fail "refusing signing key path '$signing_key'" 2 ;;
esac
case $ref in
    ''|-*) fail "refusing ref '$ref'" 2 ;;
esac

if [ "$skip_network_check" = no ] && on_test_wifi; then
    printf 'deploy-from-mac: %s\n' \
        "SSH from the test Wi-Fi is blocked on purpose; join the home network or use Ethernet." >&2
    printf 'Then run the same command again:\n  %s\n' "$retry" >&2
    exit 1
fi

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
# prints the archive's SHA-256 checksum and this script checks that value with shasum. With
# the signing key it also writes ARCHIVE.sig (sign-release.sh refuses a key others can read).
if [ -f "$signing_key" ]; then
    signed=yes
    set -- "$commit" "$archive" "$signing_key"
else
    signed=no
    set -- "$commit" "$archive"
fi
printf '+ %s\n' "$(words sh deploy/raspberry-pi/build-release.sh "$@")"
if [ "$dry_run" = no ]; then
    built=$(cd "$repo" && sh deploy/raspberry-pi/build-release.sh "$@") \
        || fail "building or signing the release archive failed"
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

# One look at the Pi before copying: can it be reached, does it have the update service, does
# it install only signed releases, does it trust a key, may this login stage without a
# password and which release does it run? None of it needs a password: `sudo -n -l` only
# asks sudo whether the exact staging command is allowed.
printf 'Connecting to %s with the key %s.\n' "$host" "$key"
probe="if $(words test -f "$MANAGER"); then echo update-service; fi; \
if $(words grep -q '^SIGNED_RELEASES = True' "$MANAGER") 2>/dev/null; then echo signed-updates; fi; \
if $(words grep -q '^jittersim-release ' "$SIGNERS") 2>/dev/null; then echo trusted-key; fi; \
if $(words sudo -n -l "$COMMAND" update stage "$INCOMING_PATH") >/dev/null 2>&1; then \
echo passwordless; fi; $(words mkdir -p -m 700 incoming) 2>/dev/null; \
$(words jittersim update check) 2>/dev/null || true"
show_ssh_batch "$probe"
if [ "$dry_run" = yes ]; then
    # A dry run cannot ask the Pi. It shows the signed path when there is a signing key.
    answer=update-service
    if [ "$signed" = yes ]; then
        answer="update-service
signed-updates
trusted-key"
    fi
else
    set +e
    answer=$(ssh_batch_answer "$CHECK_SECONDS" "$probe")
    status=$?
    set -e
    if [ "$status" -ne 0 ] && [ "$transport" = auto ] && [ "$use_installer" = no ]; then
        printf 'SSH to the Pi did not answer. Using the HTTPS upload instead.\n'
        over_https
    fi
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

# One line of the probe's answer, matched as a whole so a status message cannot fake it.
marker() {
    printf '%s\n' "$answer" | grep -qx "$1"
}
pi_signed=no
if [ "$use_installer" = no ] && marker signed-updates; then
    pi_signed=yes
    [ "$signed" = yes ] || fail "the Pi installs only signed releases and there is no signing key at $signing_key. Create one with deploy/raspberry-pi/make-release-key.sh and run the command it prints on the Pi."
    marker trusted-key || fail "the Pi trusts no release signing key yet. Run deploy/raspberry-pi/make-release-key.sh and the one command it prints on the Pi."
fi

# shellcheck disable=SC2086
copy() {
    set +e
    run_limited "$COPY_SECONDS" scp -o BatchMode=yes $CONNECTION_OPTIONS -i "$key" -- \
        "$1" "$host:$2"
    status=$?
    set -e
    if [ "$status" -eq 124 ]; then
        unreachable 124 "Copying the archive" "$COPY_SECONDS"
    elif [ "$status" -ne 0 ]; then
        fail "copying the archive to the Pi failed (exit $status). $NETWORK_HINT"
    fi
}
if [ "$pi_signed" = yes ]; then
    copy "$archive" "$INCOMING"
    copy "$archive.sig" "$INCOMING.sig"
else
    copy "$archive" "$remote_archive"
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

if [ "$pi_signed" = yes ]; then
    # The Pi stages only an archive signed by a key it trusts and newer than what it ran
    # before. It checks the signature of its own copy so the passwordless rule alone installs
    # nothing. `install --wait` joins the update service run and fails when the update fails.
    cleanup=$(words rm -f "$INCOMING" "$INCOMING.sig")
    if marker passwordless; then
        printf 'Passwordless staging is on: no sudo password is needed.\n'
        stage="$(words sudo -n "$COMMAND" update stage "$INCOMING_PATH") && $cleanup"
        install_step=$(words sudo -n "$COMMAND" update install --yes --wait)
    else
        stage="$(words sudo "$COMMAND" update stage "$INCOMING") && $cleanup"
        install_step=$(words sudo "$COMMAND" update install --yes --wait)
    fi
    limit=$STAGE_SECONDS
    if [ "$install" = yes ]; then
        stage="$stage && $install_step"
        limit=$INSTALL_SECONDS
    fi
    failed="staging or installing on the Pi failed. Read the messages above. On the Pi check: jittersim update check and sudo journalctl -u jittersim-update.service -n 80 --no-pager"
    if marker passwordless; then
        set +e
        ssh_batch "$limit" "$stage"
        status=$?
        set -e
        if [ "$status" -eq 124 ]; then
            unreachable 124 "Staging on the Pi" "$limit"
        fi
        [ "$status" -eq 0 ] || fail "$failed"
    else
        ssh_terminal "$stage" || fail "$failed"
    fi
    if [ "$install" = no ]; then
        finish_dry_run
        printf 'Staged release %s on the Pi. Install it with:\n  %s\n' "$short" \
            "$(words ssh -t -i "$key" -- "$host" "$install_step")"
        printf 'or choose Check for updates and then Install staged release in the dashboard.\n'
        exit 0
    fi
    check_release "Check: sudo journalctl -u jittersim-update.service -n 80 --no-pager"
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

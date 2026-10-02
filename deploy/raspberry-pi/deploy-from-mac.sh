#!/bin/sh
# Build the release archive of one commit on this Mac, copy it to the Pi and stage it for the
# verified update service. With --install it also installs it and checks the release the Pi
# runs afterwards. Only git, ssh, scp, tar and shasum are needed: not the Python package.
# The script never reads, stores or passes a password. The one sudo step runs through
# `ssh -t` so the Pi asks for its password in this terminal.
set -eu

DEFAULT_HOST=jittersim@192.168.0.17
DEFAULT_KEY="$HOME/.ssh/jittersim_ed25519"
MANAGER=/usr/local/lib/jittersim-update.py

usage() {
    cat <<EOF
usage: deploy-from-mac.sh [--ref REF] [--host USER@HOST] [--key PATH] [--install]
                          [--installer] [--dry-run]

Build the release archive of REF with build-release.sh, copy it to the Pi over SSH and stage
it for the verified update service (the same check as 'sudo jittersim update install' and the
dashboard's Install staged release).

  --ref REF          tag, branch or commit to release (default: HEAD). The archive is built
                     from the commit so uncommitted changes are left out.
  --host USER@HOST   the Pi's SSH login (default: $DEFAULT_HOST)
  --key PATH         the SSH key for that login (default: ~/.ssh/jittersim_ed25519)
  --install          install the staged release too and print the release the Pi runs.
                     The Pi asks for its sudo password in this terminal.
  --installer        use the full installer from the copied archive instead of the update
                     service. Needed once on a Pi that has no update service yet and for
                     OS package or Wi-Fi changes.
  --dry-run          resolve REF and print every command without running any of them.
  -h, --help         show this help

Every ssh and scp call uses your known_hosts as it is. Only the sudo step opens a terminal
on the Pi. The release is named after the short commit: compare it with
'git rev-parse --short REF'.
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

ref=HEAD
host=$DEFAULT_HOST
key=$DEFAULT_KEY
install=no
installer=no
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
        --installer) installer=yes; shift ;;
        --dry-run) dry_run=yes; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

# A host is user@name or name with letters, digits, dots, dashes and underscores only. It
# never starts with a dash so ssh cannot read it as an option.
case $host in
    ''|-*|*@|@*|*@*@*|*@-*|*[!A-Za-z0-9._@-]*)
        fail "refusing host '$host': use USER@HOST with letters, digits, dots and dashes" 2
        ;;
esac
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

ssh_batch() {
    run ssh -o BatchMode=yes -o ConnectTimeout=10 -o IdentitiesOnly=yes -i "$key" -- \
        "$host" "$1"
}

ssh_terminal() {
    run ssh -t -o ConnectTimeout=10 -o IdentitiesOnly=yes -i "$key" -- "$host" "$1"
}

if [ "$dry_run" = yes ]; then
    work="${TMPDIR:-/tmp}/jittersim-deploy.XXXXXX"
    sha='SHA256_OF_THE_ARCHIVE'
else
    work=$(mktemp -d "${TMPDIR:-/tmp}/jittersim-deploy.XXXXXX")
    trap 'rm -rf "$work"' EXIT
    trap 'exit 1' HUP INT TERM
fi
archive="$work/$remote_archive"

# build-release.sh reads the paths relative to the checkout so it runs from its top.
build() {
    (cd "$repo" && sh deploy/raspberry-pi/build-release.sh "$commit" "$archive" >/dev/null)
}
printf '+ %s\n' "$(words sh deploy/raspberry-pi/build-release.sh "$commit" "$archive")"
if [ "$dry_run" = no ]; then
    build || fail "building the release archive failed"
    if command -v shasum >/dev/null 2>&1; then
        sha=$(shasum -a 256 "$archive" | cut -d ' ' -f 1)
    else
        sha=$(sha256sum "$archive" | cut -d ' ' -f 1)
    fi
    case $sha in
        *[!0-9a-f]*|'') fail "could not compute the archive checksum" ;;
    esac
    [ "${#sha}" -eq 64 ] || fail "could not compute the archive checksum"
fi
printf 'Archive: %s\nSHA-256: %s\n' "$remote_archive" "$sha"

if [ "$installer" = no ]; then
    # Staging needs the update service that the full installer adds once.
    set +e
    ssh_batch "$(words test -f "$MANAGER")"
    status=$?
    set -e
    if [ "$status" -eq 1 ]; then
        fail "the Pi has no update service yet. Run this once with --installer."
    elif [ "$status" -ne 0 ]; then
        fail "could not reach $host over SSH (exit $status). Check the address and the key."
    fi
fi

run scp -o BatchMode=yes -o ConnectTimeout=10 -o IdentitiesOnly=yes -i "$key" -- \
    "$archive" "$host:$remote_archive" || fail "copying the archive to the Pi failed"

# Read the release the Pi reports and compare it with this commit. The helper may still be
# starting after an install so the check is tried a few times.
check_release() {
    if [ "$dry_run" = yes ]; then
        ssh_batch "$(words jittersim update check)"
        return 0
    fi
    attempt=1
    while [ "$attempt" -le 10 ]; do
        if report=$(ssh_batch "$(words jittersim update check)" 2>/dev/null); then
            installed=$(printf '%s\n' "$report" | sed -n 's/^Installed: //p' | head -n 1)
            if [ "$installed" = "$short" ]; then
                printf 'The Pi now runs %s. Compare it with: git rev-parse --short %s\n' \
                    "$short" "$(quote "$ref")"
                return 0
            fi
            printf '%s\n' "$report"
            fail "the Pi reports '$installed' instead of $short. Check: sudo journalctl -u jittersim-update.service -n 80 --no-pager"
        fi
        attempt=$((attempt + 1))
        sleep 3
    done
    fail "could not read the installed release. On the Pi run: sudo jittersim update check"
}

if [ "$installer" = yes ]; then
    # Check the copy on the Pi, unpack it as the login user and label it for the installer.
    unpack="printf '%s  %s\\n' $(quote "$sha") $(quote "$remote_archive") | sha256sum -c - \
&& rm -rf $(quote "$release") && mkdir $(quote "$release") \
&& tar -xzf $(quote "$remote_archive") -C $(quote "$release") \
&& printf '{\"version\": \"%s\", \"sha256\": \"%s\"}\\n' $(quote "$short") $(quote "$sha") \
> $(quote "$release/.jittersim-release.json")"
    ssh_batch "$unpack" || fail "checking or unpacking the archive on the Pi failed"
    install_command=$(words sudo "./$release/deploy/raspberry-pi/install.sh")
    if [ "$install" = no ]; then
        printf 'Unpacked %s on the Pi. Install it with:\n  %s\n' "$release" \
            "$(words ssh -t -i "$key" -- "$host" "$install_command")"
        exit 0
    fi
    ssh_terminal "$install_command" || fail "the installer on the Pi failed. Read its messages above."
    check_release
    exit 0
fi

stage="$(words sudo /usr/bin/python3 -I "$MANAGER" stage "$remote_archive" --version "$short" \
    --sha256 "$sha")"
if [ "$install" = no ]; then
    ssh_terminal "$stage" || fail "staging on the Pi failed. Read the message above."
    printf 'Staged %s on the Pi. Install it with:\n  %s\n' "$short" \
        "$(words ssh -t -i "$key" -- "$host" "$(words sudo jittersim update install)")"
    printf 'or choose Check for updates and then Install staged release in the dashboard.\n'
    exit 0
fi
# `systemctl start` waits for the queued one-shot update to finish and fails when it fails.
ssh_terminal "$stage && $(words sudo jittersim update install --yes) \
&& $(words sudo systemctl start jittersim-update.service)" \
    || fail "staging or installing on the Pi failed. Read the messages above."
check_release

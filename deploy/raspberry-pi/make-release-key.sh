#!/bin/sh
# Create the release signing key on the Mac and print the one command that makes the Pi
# trust it. An existing key is never replaced: the command for it is printed again.
# It is plain POSIX sh so it runs with the bash 3.2 that macOS ships and with dash.
set -eu

DEFAULT_HOST=jittersim@192.168.0.17
key="$HOME/.ssh/jittersim_release_ed25519"
label=
label_given=no
passphrase=no
passwordless=yes

usage() {
    printf '%s\n' \
        'usage: make-release-key.sh [--key PATH] [--label LABEL] [--passphrase]' \
        '                           [--no-passwordless]' \
        '' \
        'Creates an ed25519 release signing key on this Mac and prints the one command that' \
        'makes the Pi trust it. The Pi then installs only releases signed with this key.' \
        '' \
        '  --key PATH          where the private key goes' \
        '                      (default: ~/.ssh/jittersim_release_ed25519, public key PATH.pub)' \
        '  --label LABEL       the name the Pi shows for the key (default: mac-YYYYMMDD)' \
        '  --passphrase        protect the key with a passphrase. ssh-keygen asks for it. Add' \
        '                      the key to ssh-agent so signing does not ask every time.' \
        '  --no-passwordless   leave out --enable-passwordless-staging so every deploy still' \
        '                      asks for the sudo password on the Pi' \
        '  -h, --help          show this help' \
        '' \
        'Keep the private key on this Mac only. Anyone with it can sign releases the Pi installs.'
}

fail() {
    printf 'make-release-key: %s\n' "$1" >&2
    exit "${2:-1}"
}

# A label is 1 to 64 ASCII letters, digits, dots, dashes, underscores or @ and starts with a
# letter or a digit, like the Pi requires. The C locale keeps bash from counting other
# letters as letters.
valid_label() (
    LC_ALL=C
    export LC_ALL
    case $1 in
        ''|*[!A-Za-z0-9._@-]*) return 1 ;;
        [A-Za-z0-9]*) ;;
        *) return 1 ;;
    esac
    [ "${#1}" -le 64 ]
)

while [ "$#" -gt 0 ]; do
    case $1 in
        --key|--label)
            [ "$#" -ge 2 ] || fail "$1 needs a value" 2
            case $1 in
                --key) key=$2 ;;
                --label) label=$2; label_given=yes ;;
            esac
            shift 2
            ;;
        --passphrase) passphrase=yes; shift ;;
        --no-passwordless) passwordless=no; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
case $key in
    ''|-*) fail "refusing key path '$key'" 2 ;;
esac
if [ "$label_given" = yes ] && ! valid_label "$label"; then
    fail "refusing label '$label': use 1 to 64 letters, digits, dots, dashes, underscores or @" 2
fi

if [ -e "$key" ] || [ -e "$key.pub" ]; then
    [ -f "$key" ] && [ -f "$key.pub" ] || fail "only half of the key pair $key exists"
    printf 'The release signing key %s already exists. It was not changed.\n' "$key"
    if [ -z "$label" ]; then
        comment=$(cut -d ' ' -f 3- "$key.pub")
        if valid_label "$comment"; then
            label=$comment
        fi
    fi
else
    [ -n "$label" ] || label="mac-$(date +%Y%m%d)"
    folder=$(dirname -- "$key")
    if [ ! -d "$folder" ]; then
        mkdir -p -- "$folder"
        chmod 700 "$folder"
    fi
    if [ "$passphrase" = yes ]; then
        ssh-keygen -q -t ed25519 -a 100 -C "$label" -f "$key" || fail "ssh-keygen failed"
    else
        ssh-keygen -q -t ed25519 -a 100 -C "$label" -N '' -f "$key" </dev/null \
            || fail "ssh-keygen failed"
    fi
    chmod 600 "$key"
    printf 'Created the release signing key %s.\n' "$key"
fi
[ -n "$label" ] || label="mac-$(date +%Y%m%d)"

public=$(cut -d ' ' -f 1,2 "$key.pub")
case $public in
    'ssh-ed25519 '*) ;;
    *) fail "$key.pub is not an ssh-ed25519 public key" ;;
esac
case $public in
    *[!A-Za-z0-9+/=\ -]*) fail "$key.pub holds unexpected characters" ;;
esac

options="--label $label"
if [ "$passwordless" = yes ]; then
    options="$options --enable-passwordless-staging"
fi
command="echo '$public $label' | sudo jittersim update trust-key $options -"

printf 'Fingerprint: %s\n' "$(ssh-keygen -l -f "$key.pub")"
printf '\nRun this one command on the Pi once. It asks for the sudo password once:\n\n'
printf '  %s\n\n' "$command"
printf 'From this Mac the same command runs over SSH with:\n\n'
printf '  ssh -t -i ~/.ssh/jittersim_ed25519 %s "%s"\n\n' "$DEFAULT_HOST" "$command"
if [ "$passwordless" = yes ]; then
    printf '%s\n' 'It also lets the jittersim login stage and install signed releases without a' \
        'password. Run make-release-key.sh --no-passwordless for the command without that.'
fi
printf '%s\n' 'The Pi must run a release with signed updates first: deploy-from-mac.sh installs' \
    'one with the sudo password if it does not.'
if [ "$passphrase" = yes ]; then
    printf 'Add the key to ssh-agent so signing does not ask every time: ssh-add %s\n' "$key"
fi
printf 'Keep %s on this Mac only. Anyone with it can sign releases the Pi installs.\n' "$key"

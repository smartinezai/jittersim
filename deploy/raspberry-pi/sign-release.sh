#!/bin/sh
# Sign a release archive on the Mac so a Pi with signed releases accepts it. It writes
# ARCHIVE.sig next to the archive with an OpenSSH signature in the jittersim-release
# namespace. It is plain POSIX sh so it runs with the bash 3.2 that macOS ships and with dash.
set -eu

NAMESPACE=jittersim-release
key="$HOME/.ssh/jittersim_release_ed25519"

usage() {
    printf '%s\n' \
        'usage: sign-release.sh [--key PATH] ARCHIVE' \
        '' \
        'Signs ARCHIVE with the release signing key and writes ARCHIVE.sig. The Pi installs a' \
        'release only when a key it trusts signed exactly these bytes.' \
        '' \
        '  --key PATH   the private release signing key (default:' \
        '               ~/.ssh/jittersim_release_ed25519). make-release-key.sh creates it.' \
        '               A key held by ssh-agent is used from there.' \
        '  -h, --help   show this help'
}

fail() {
    printf 'sign-release: %s\n' "$1" >&2
    exit "${2:-1}"
}

archive=
while [ "$#" -gt 0 ]; do
    case $1 in
        --key)
            [ "$#" -ge 2 ] || fail "--key needs a value" 2
            key=$2
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        -*) usage >&2; exit 2 ;;
        *)
            [ -z "$archive" ] || { usage >&2; exit 2; }
            archive=$1
            shift
            ;;
    esac
done
[ -n "$archive" ] || { usage >&2; exit 2; }
case $key in
    ''|-*) fail "refusing key path '$key'" 2 ;;
esac
case $archive in
    -*) archive="./$archive" ;;
esac
[ -f "$archive" ] || fail "the archive $archive does not exist" 2
[ -f "$key" ] || fail "no release signing key at $key. Create one with deploy/raspberry-pi/make-release-key.sh" 2
[ -f "$key.pub" ] || fail "the public key $key.pub is missing" 2
# A private key that someone else owns or may read or change could sign releases for them.
[ -O "$key" ] || fail "the signing key $key belongs to another user" 2
permissions=$(ls -lLd "$key" | cut -c 5-10)
[ "$permissions" = '------' ] \
    || fail "the signing key $key may be read or changed by others. Fix it with: chmod 600 $key" 2

# ssh-keygen asks before it replaces a signature so an old one is removed first.
rm -f "$archive.sig"
ssh-keygen -Y sign -f "$key" -n "$NAMESPACE" "$archive" \
    || fail "ssh-keygen could not sign $archive"
# Check the new signature against the public key the way the Pi checks it.
allowed=$(mktemp "${TMPDIR:-/tmp}/jittersim-signers.XXXXXX")
trap 'rm -f "$allowed"' EXIT
printf '%s namespaces="%s" %s\n' "$NAMESPACE" "$NAMESPACE" "$(cut -d ' ' -f 1,2 "$key.pub")" \
    >"$allowed"
ssh-keygen -Y verify -f "$allowed" -I "$NAMESPACE" -n "$NAMESPACE" -s "$archive.sig" \
    <"$archive" >/dev/null 2>&1 \
    || fail "the new signature of $archive does not verify with $key.pub"
printf 'Signed %s with %s\n' "$archive" "$(ssh-keygen -l -f "$key.pub")"

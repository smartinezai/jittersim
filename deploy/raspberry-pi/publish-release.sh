#!/bin/sh
# Build and sign the release of one commit on this Mac and publish it as a GitHub release with
# exactly two files: jittersim-release.tar.gz and jittersim-release.tar.gz.sig. A Pi with the
# online release source (or the owner by hand) fetches them from there. The signature is what
# makes a release installable, not GitHub: the Pi stages only an archive that a key in
# /etc/jittersim/release-signers signed. Plain POSIX sh for the bash 3.2 of macOS and for dash.
set -eu

DEFAULT_SIGNING_KEY="$HOME/.ssh/jittersim_release_ed25519"
ASSET=jittersim-release.tar.gz
REPOSITORY=${JITTERSIM_REPOSITORY:-smartinezai/jittersim}

usage() {
    cat <<USAGE
usage: publish-release.sh [--tag TAG] [--signing-key PATH] [--allow-dirty] [--dry-run] REF

Builds the release archive of REF with build-release.sh, signs it with sign-release.sh and runs
  gh release create TAG $ASSET $ASSET.sig
so the release has exactly those two files.

  --tag TAG          the release tag to create (default: release-SHORTCOMMIT)
  --signing-key PATH the release signing key (default: ~/.ssh/jittersim_release_ed25519)
  --allow-dirty      publish even though tracked files in this checkout have uncommitted
                     changes. They are not part of the archive: it is built from the commit.
  --dry-run          print every command and run nothing
  -h, --help         show this help

Needs git, ssh-keygen and the GitHub CLI (gh, signed in with access to the repository).
USAGE
}

fail() {
    printf 'publish-release: %s\n' "$1" >&2
    exit "${2:-1}"
}

quote() {
    case $1 in
        ''|*[!A-Za-z0-9_./:=@%+,-]*) ;;
        *) printf '%s' "$1"; return 0 ;;
    esac
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

words() {
    line=
    for word in "$@"; do
        line="$line $(quote "$word")"
    done
    printf '%s' "${line# }"
}

tag=
key=$DEFAULT_SIGNING_KEY
allow_dirty=no
dry_run=no
ref=
while [ "$#" -gt 0 ]; do
    case $1 in
        --tag|--signing-key)
            [ "$#" -ge 2 ] || fail "$1 needs a value" 2
            if [ "$1" = --tag ]; then tag=$2; else key=$2; fi
            shift 2
            ;;
        --allow-dirty) allow_dirty=yes; shift ;;
        --dry-run) dry_run=yes; shift ;;
        -h|--help) usage; exit 0 ;;
        -*) usage >&2; exit 2 ;;
        *)
            [ -z "$ref" ] || { usage >&2; exit 2; }
            ref=$1
            shift
            ;;
    esac
done
[ -n "$ref" ] || { usage >&2; exit 2; }
case $ref in -*) fail "refusing ref '$ref'" 2 ;; esac
case $key in ''|-*) fail "refusing key path '$key'" 2 ;; esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(git -C "$script_dir" rev-parse --show-toplevel) || fail "run this from a JitterSim checkout"
commit=$(git -C "$repo" rev-parse --verify --quiet "$ref^{commit}") || fail "unknown ref '$ref'" 2
short=$(git -C "$repo" rev-parse --short "$commit")
case $short in *[!0-9a-f]*|'') fail "unexpected commit name '$short'" ;; esac
[ -n "$tag" ] || tag="release-$short"
case $tag in
    ''|-*|*[!A-Za-z0-9._-]*) fail "refusing tag '$tag': use letters, digits, dots, dashes and underscores" 2 ;;
esac

if [ -n "$(git -C "$repo" status --porcelain --untracked-files=no)" ]; then
    if [ "$allow_dirty" = no ]; then
        fail "this checkout has uncommitted changes. Commit or stash them, or run again with --allow-dirty (the archive is built from the commit, not from these changes)."
    fi
    printf 'Uncommitted changes in this checkout are not part of the archive.\n'
fi

printf 'Commit: %s (%s)\nTag: %s\n' "$commit" "$short" "$tag"
if [ "$dry_run" = yes ]; then
    work="${TMPDIR:-/tmp}/jittersim-publish.XXXXXX"
else
    [ -f "$key" ] || fail "no release signing key at $key. Create one with deploy/raspberry-pi/make-release-key.sh" 2
    command -v gh >/dev/null 2>&1 || fail "the GitHub CLI (gh) is not installed. Install it and run: gh auth login" 2
    work=$(mktemp -d "${TMPDIR:-/tmp}/jittersim-publish.XXXXXX")
    trap 'rm -rf "$work"' EXIT
    trap 'exit 1' HUP INT TERM
fi
archive="$work/$ASSET"

printf '+ %s\n' "$(words sh deploy/raspberry-pi/build-release.sh "$commit" "$archive" "$key")"
if [ "$dry_run" = no ]; then
    (cd "$repo" && sh deploy/raspberry-pi/build-release.sh "$commit" "$archive" "$key") \
        || fail "building or signing the release failed"
    [ -f "$archive" ] && [ -f "$archive.sig" ] || fail "the release archive or its signature is missing"
fi
# gh uploads the files under their own names, so they are named as the Pi expects.
printf '+ %s\n' "$(words gh release create "$tag" "$ASSET" "$ASSET.sig" --repo "$REPOSITORY" \
    --target "$commit" --title "JitterSim $short" --notes "Signed release of commit $short")"
if [ "$dry_run" = yes ]; then
    printf 'Dry run: nothing was built or published.\n'
    exit 0
fi
(cd "$work" && gh release create "$tag" "$ASSET" "$ASSET.sig" --repo "$REPOSITORY" \
    --target "$commit" --title "JitterSim $short" --notes "Signed release of commit $short") \
    || fail "gh could not create the release. Check: gh auth status, and that commit $short is pushed to GitHub (the release is made from that commit)."
printf 'Published release %s with %s and %s.sig\n' "$tag" "$ASSET" "$ASSET"

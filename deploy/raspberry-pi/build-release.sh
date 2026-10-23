#!/bin/sh
set -eu
# Build only committed source. The final output line is the archive checksum.
# A matching vMAJOR.MINOR.PATCH tag names a release. Other commits are development builds.
key=
if [ "$#" -eq 2 ] && [ "$1" = --version ]; then
    print_version=yes
    ref=$2
    output=
elif [ "$#" -ge 2 ] && [ "$#" -le 3 ]; then
    print_version=no
    ref=$1
    output=$2
    key=${3:-}
else
    echo 'usage: build-release.sh REF OUTPUT_DIR|OUTPUT.tar.gz [SIGNING_KEY]' >&2
    echo '       build-release.sh --version REF' >&2
    exit 1
fi
commit=$(git rev-parse --verify "$ref^{commit}")
number=$(git show "$commit:pyproject.toml" | sed -n 's/^version = "\(.*\)"$/\1/p' | head -n 1)
package=$(git show "$commit:src/jittersim/__init__.py" \
    | sed -n 's/^__version__ = "\(.*\)"$/\1/p' | head -n 1)
if [ "$package" != "$number" ]; then
    echo 'build-release.sh: the package version does not match pyproject.toml' >&2
    exit 1
fi
if [ "${#number}" -gt 40 ] || ! printf '%s\n' "$number" \
    | LC_ALL=C grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'; then
    echo 'build-release.sh: pyproject.toml must contain a MAJOR.MINOR.PATCH version' >&2
    exit 1
fi
tag=$(git tag --points-at "$commit" --list 'v[0-9]*')
if [ -n "$tag" ]; then
    if [ "$tag" != "v$number" ]; then
        echo "tag $tag does not match the version $number in pyproject.toml" >&2
        exit 1
    fi
    version=$number
else
    version="$number.dev+$(printf %s "$commit" | cut -c1-7)"
fi
if [ "$print_version" = yes ]; then
    printf '%s\n' "$version"
    exit 0
fi
case $output in
    *.tar.gz) archive=$output ;;
    *) archive="$output/jittersim-$version.tar.gz" ;;
esac
# The signed manifest binds the label and source to an increasing release order.
sequence=${JITTERSIM_RELEASE_SEQUENCE:-$(date +%s)}
case $sequence in
    ''|*[!0-9]*|0*)
        echo 'build-release.sh: the release sequence must be a positive whole number' >&2
        exit 1
        ;;
esac
# The same limit as check-release-manifest.sh and MAX_SEQUENCE in updates.py: a larger number
# builds and signs, then the Pi refuses the release. The length test comes first so the numeric
# test never sees a number that is too large for the shell.
if [ "${#sequence}" -gt 16 ] || [ "$sequence" -ge 9007199254740992 ]; then
    echo 'build-release.sh: the release sequence must be below 9007199254740992 (2 to the power of 53)' >&2
    exit 1
fi
manifest=$(printf '{"format": 1, "commit": "%s", "version": "%s", "sequence": %s}' \
    "$commit" "$version" "$sequence")
printf 'Release version: %s\n' "$version" >&2
# A release carries its own CHANGELOG.md section as deploy/RELEASE-NOTES.md so the Pi can
# show notes that the same signature covers. At most 16 KB. Development builds carry none.
notes=
if [ "$version" = "$number" ]; then
    notes=$(git show "$commit:CHANGELOG.md" 2>/dev/null | awk -v head="## v$number" '
        index($0, head) == 1 && (length($0) == length(head) || substr($0, length(head) + 1, 1) == " ") {
            found = 1; print; next
        }
        found && /^## / { exit }
        found { print }') || notes=
    notes_size=$(printf '%s\n' "$notes" | wc -c | tr -d ' ')
    if [ "$notes_size" -gt 16384 ]; then
        echo 'build-release.sh: release notes exceed 16384 bytes. Shorten the release section before building' >&2
        exit 1
    fi
fi
if [ -n "$notes" ]; then
    git archive --format=tar.gz --add-virtual-file="deploy/RELEASE:$manifest
" --add-virtual-file="deploy/RELEASE-NOTES.md:$notes
" --output="$archive" "$commit" pyproject.toml README.md src deploy
else
    git archive --format=tar.gz --add-virtual-file="deploy/RELEASE:$manifest
" --output="$archive" "$commit" pyproject.toml README.md src deploy
fi
if [ -n "$key" ]; then
    sh "$(dirname -- "$0")/sign-release.sh" --key "$key" "$archive" >&2
fi
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$archive"
else
    shasum -a 256 "$archive"
fi

#!/bin/sh
set -eu
# Choose a reviewed tag or commit. The output stays outside the source tree. With a signing
# key the archive is signed too (OUTPUT.sig) so a Pi with signed releases accepts it. The
# last line printed is always the archive's SHA-256 checksum.
if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
    echo 'usage: build-release.sh REF OUTPUT.tar.gz [SIGNING_KEY]' >&2
    exit 1
fi
ref=$1
output=$2
key=${3:-}
commit=$(git rev-parse --verify "$ref^{commit}")
short=$(git rev-parse --short "$commit")
# The signed manifest deploy/RELEASE names the commit and a release sequence. The Pi installs
# only a sequence newer than every release it installed before. The build time is the
# sequence so every new build is newer than the archives built before it. It sits in deploy/
# because update managers from before signed releases accept no other new top-level name.
sequence=${JITTERSIM_RELEASE_SEQUENCE:-$(date +%s)}
case $sequence in
    ''|*[!0-9]*|0*)
        echo 'build-release.sh: the release sequence must be a positive whole number' >&2
        exit 1
        ;;
esac
manifest=$(printf '{"format": 1, "commit": "%s", "version": "%s", "sequence": %s}' \
    "$commit" "$short" "$sequence")
git archive --format=tar.gz --add-virtual-file="deploy/RELEASE:$manifest
" --output="$output" "$commit" pyproject.toml README.md src deploy
if [ -n "$key" ]; then
    sh "$(dirname -- "$0")/sign-release.sh" --key "$key" "$output" >&2
fi
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$output"
else
    shasum -a 256 "$output"
fi

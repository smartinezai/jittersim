#!/bin/sh
set -eu
# Choose a reviewed tag or commit. The output stays outside the source tree.
if [ "$#" -ne 2 ]; then
    echo 'usage: build-release.sh REF OUTPUT.tar.gz' >&2
    exit 1
fi
ref=$1
output=$2
git rev-parse --verify "$ref^{commit}" >/dev/null
git archive --format=tar.gz --output="$output" "$ref" pyproject.toml README.md src deploy
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$output"
else
    shasum -a 256 "$output"
fi

#!/bin/sh
set -eu
# Choose a reviewed tag or commit. The output stays outside the source tree.
# A commit with a tag vMAJOR.MINOR.PATCH builds release MAJOR.MINOR.PATCH and the number must
# match the version in pyproject.toml. Any other commit builds NUMBER.dev+COMMIT.
# OUTPUT is a folder (the file is named after the version) or a name that ends in .tar.gz.
if [ "$#" -ne 2 ]; then
    echo 'usage: build-release.sh REF OUTPUT_DIR|OUTPUT.tar.gz' >&2
    exit 1
fi
ref=$1
output=$2
commit=$(git rev-parse --verify "$ref^{commit}")
number=$(git show "$commit:pyproject.toml" | sed -n 's/^version = "\(.*\)"$/\1/p' | head -n 1)
tag=$(git tag --points-at "$commit" --list 'v[0-9]*' | head -n 1)
if [ -n "$tag" ]; then
    if [ "$tag" != "v$number" ]; then
        echo "tag $tag does not match the version $number in pyproject.toml" >&2
        exit 1
    fi
    version=$number
else
    version="$number.dev+$(printf %s "$commit" | cut -c1-7)"
fi
case "$output" in
    *.tar.gz) archive=$output ;;
    *) archive="$output/jittersim-$version.tar.gz" ;;
esac
echo "Release version: $version" >&2
git archive --format=tar.gz --output="$archive" "$commit" pyproject.toml README.md src deploy
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$archive"
else
    shasum -a 256 "$archive"
fi

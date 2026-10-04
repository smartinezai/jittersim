#!/bin/sh
set -eu
# Check the exact signed-manifest format written by build-release.sh before a transfer.
# Print its release order only. The caller has the expected full commit and version.
refuse() {
    echo 'The built release manifest differs from the requested source or version.' >&2
    exit 1
}
[ "$#" -eq 3 ] || refuse
archive=$1
commit=$2
version=$3
case $commit in ''|*[!0-9a-f]*) refuse ;; esac
[ "${#commit}" -eq 40 ] || refuse
case $version in ''|*[!A-Za-z0-9._+-]*) refuse ;; esac
[ "${#version}" -le 64 ] || refuse
# Limit captured bytes even if a changed build script supplied a different archive.
raw=$(
    tar -xOzf "$archive" deploy/RELEASE | head -c 4097
    printf .
) || refuse
raw=${raw%.}
[ "${#raw}" -le 4096 ] || refuse
sequence=$(printf '%s\n' "$raw" | sed -n 's/.*"sequence": \([0-9]*\)}$/\1/p')
case $sequence in ''|0*|*[!0-9]*) refuse ;; esac
[ "${#sequence}" -le 16 ] || refuse
[ "$sequence" -lt 9007199254740992 ] || refuse
expected=$(printf '{"format": 1, "commit": "%s", "version": "%s", "sequence": %s}' \
    "$commit" "$version" "$sequence")
newline='
'
[ "$raw" = "$expected$newline" ] || refuse
printf '%s\n' "$sequence"

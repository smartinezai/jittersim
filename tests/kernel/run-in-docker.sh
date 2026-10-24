#!/bin/sh
# Run the Linux datapath tests and smoke check in a privileged container, for
# example on a Mac or in CI.
set -eu
repository=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
image_dir=$(mktemp -d "${TMPDIR:-/tmp}/jittersim-kernel-image.XXXXXX")
trap 'rm -rf "$image_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
image_file=$image_dir/image.id
docker build -q -f "$repository/tests/kernel/Dockerfile" --iidfile "$image_file" "$repository" >/dev/null
image_id=$(cat "$image_file")
digest=${image_id#sha256:}
case "$image_id" in
  sha256:*) case "$digest" in *[!0-9a-f]*) digest= ;; esac ;;
  *) digest= ;;
esac
if [ "${#digest}" -ne 64 ]; then
  printf '%s\n' 'Docker did not write a valid image ID; no kernel container was started.' >&2
  exit 1
fi
docker run --rm --privileged "$image_id"

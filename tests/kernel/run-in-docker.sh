#!/bin/sh
# Run the Linux datapath tests and smoke check in a privileged container, for
# example on a Mac or in CI.
set -eu
repository=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
docker build -q -f "$repository/tests/kernel/Dockerfile" -t jittersim-kernel-tests "$repository" >/dev/null
docker run --rm --privileged jittersim-kernel-tests

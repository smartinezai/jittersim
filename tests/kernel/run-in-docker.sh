#!/bin/sh
# Run the Linux datapath tests in a privileged container, for example on a Mac.
set -eu
repository=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
docker build -q -f "$repository/tests/kernel/Dockerfile" -t jittersim-kernel-tests "$repository" >/dev/null
docker run --rm --privileged jittersim-kernel-tests

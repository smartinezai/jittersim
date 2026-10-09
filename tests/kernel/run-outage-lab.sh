#!/bin/sh
# Run the Pi-only cutover lab in one privileged container, for example on a Mac.
# Results (JSON) and per-scenario logs and captures land in ${1:-./outage-lab-output}.
# Extra arguments select tests, for example:
#   tests/kernel/run-outage-lab.sh /tmp/out tests.kernel.test_outage_lab.CleanPiOnlyTests
set -eu
repository=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output=${1:-"$PWD/outage-lab-output"}
[ "$#" -gt 0 ] && shift
mkdir -p "$output"
output=$(CDPATH= cd -- "$output" && pwd)
docker build -q -f "$repository/tests/kernel/Dockerfile.outage-lab" -t jittersim-lab-outage \
    "$repository" >/dev/null
selection=${*:-tests.kernel.test_outage_lab}
# shellcheck disable=SC2086
docker run --rm --privileged --name "jittersim-lab-run-$$" \
    -e JITTERSIM_LAB_RESULTS=/out/results.json -e JITTERSIM_LAB_LOGS=/out/logs \
    -v "$output:/out" jittersim-lab-outage python -m unittest -v $selection

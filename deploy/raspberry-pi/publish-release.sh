#!/bin/sh
set -eu
# Stable publication always uses the retained package and signed owner approval.
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_dir/release-system.py" publish "$@"

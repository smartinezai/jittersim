## Local CI run local-ci-20261025-103907-rerun

- Commit: `80029b962370c26b646ac68cc74f2ce44e3a9c2c` (branch `codex/router-ci-fixture-repairs`): Preserve router action visibility and focused verification evidence
- Tree: `c62a65605ce2011c0eb34f2be0affae09bc1bad8`
- Working tree: DIRTY (allowed with --allow-dirty): 5 changed paths
- Base: origin/main b061d2d84439bf906f1da8ca9ae306cfa0b27f52 (4 commits ahead, 4 behind)
- Command: scripts/local-ci.sh rerun --allow-dirty --python 3.12 --results-dir test-results/router-ci-fixture-repairs/native-final --echo tests/unit/test_floating_credentials.py tests/unit/test_connection_check_mutation.py tests/unit/test_connection_check.py tests/unit/test_connection_check_api.py
- Scope: full
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/Users/samantha.martinez/Documents/Codex/2026-10-24/task/router-ci-fixture-repairs/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-25 10:39:07 +0100
- Finished: 2026-10-25 10:39:15 +0100
- Results: /Users/samantha.martinez/Documents/Codex/2026-10-24/task/router-ci-fixture-repairs/test-results/router-ci-fixture-repairs/native-final/local-ci-20261025-103907-rerun
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 2 | 1 passed | setup-venv-py3.12.log |
| rerun:tests/unit/test_floating_credentials.py | PASS | 0 | 3 | 64 passed, 0 failed, 0 errors, 0 skipped | rerun_tests_unit_test_floating_credentials.py.log |
| rerun:tests/unit/test_connection_check_mutation.py | PASS | 0 | 1 | 22 passed, 0 failed, 0 errors, 0 skipped | rerun_tests_unit_test_connection_check_mutation.py.log |
| rerun:tests/unit/test_connection_check.py | PASS | 0 | 0 | 63 passed, 0 failed, 0 errors, 0 skipped | rerun_tests_unit_test_connection_check.py.log |
| rerun:tests/unit/test_connection_check_api.py | PASS | 0 | 1 | 62 passed, 0 failed, 0 errors, 0 skipped | rerun_tests_unit_test_connection_check_api.py.log |

Totals: 5 steps, 5 passed, 0 not passed (none).

## Local CI run local-ci-20261025-142353

- Commit: `8c3aa11ff4da91e357f20ab2d998b6c7a29e3225` (branch `codex/pr255-hosted-recovery`): Handle terminal request events and exited fixture children safely
- Tree: `fbb51e8373a1040f36bf339aa8a7434c51004902`
- Working tree: clean
- Base: origin/main 00f5cb0c206e6588612bd16d5cbd8a04a9843e08 (42 commits ahead, 2 behind)
- Command: scripts/local-ci.sh browser --only observation_confirmation --step-timeout 120 --results-dir test-results/hosted-recovery-final
- Scope: subset: --only observation_confirmation (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/private/tmp/js-pr255-recovery/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-25 14:23:53 +0100
- Finished: 2026-10-25 14:24:20 +0100
- Results: /private/tmp/js-pr255-recovery/test-results/hosted-recovery-final/local-ci-20261025-142353
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 1 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 0 | 1 passed | setup-chromium.log |
| browser:observation_confirmation | PASS | 0 | 26 | 1 passed | browser_observation_confirmation.log |

Totals: 3 steps, 3 passed, 0 not passed (none).

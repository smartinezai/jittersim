# Observed-site confirmation verification

The stale dashboard defect is repaired on `codex/allowlist-confirmation`. Production source, tests and CI wiring are frozen at `95573dbdcbb905f87d964365a1331c20975e00a3` based on combined foundation `0c1f4f73708f23c33c53cca7dfb08e9a0a733b1f`. The required plan is in [implementation-plan-allowlist-confirmation-2026-10-03.md](implementation-plan-allowlist-confirmation-2026-10-03.md).

The server now requires an explicit boolean acknowledgment before adding observed names can change a currently unrestricted profile into an allow list. The current profile read, check and save share one SQLite write transaction. A stale action returns 409 with the current profile and changes no saved configuration. Both single-name and group actions update the page cache from that response. The next deliberate click asks for confirmation. Declining sends nothing and the page never automatically repeats a mutation.

A real database contention test also exposed a failed commit that left the connection holding unsaved restrictions. The transaction helper now rolls back a failed outer commit. A later unrelated save cannot publish the rejected change.

## Local results

| Check | Result |
| --- | --- |
| Affected API, storage, dashboard, authentication and workflow tests | 655 passed in 17.90 seconds |
| Real commit failure cases after adding outer/nested coverage | 3 passed in 0.14 seconds |
| Real Chromium single/group actions at 1920 and 390 pixels | 24 checks passed |
| Required Ruff selection `E9,F63,F7,F82,F811` | Passed |
| Whitespace check | Passed |
| Independent transaction and complete patch review | No source blockers found |

The affected command was:

```sh
PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' -q   tests/unit/test_api.py tests/unit/test_storage.py   tests/unit/test_dashboard_traffic_groups.py tests/unit/test_dashboard_block_state.py   tests/unit/test_dashboard_auth_state.py   tests/unit/test_observation_profile_confirmation.py   tests/unit/test_observation_transaction_failure.py tests/integration/test_workflows.py
```

The extra nested case was added after that affected run. The focused commit-failure file then passed all three cases. Production source did not change between those runs.

The browser command was `PYTHONPATH=src .venv/bin/python -m tests.browser.observation_confirmation`. It used the disposable authenticated local fixture with two real tabs. It verified rejection without saved changes, page cache recovery, a second-click question, decline without a request, accepted acknowledgment, existing allow-list compatibility and transport failure without replay. The journey is registered in the existing browser job in `.github/workflows/wifi-recovery.yml`. No dependencies or build settings changed.

## Evidence that the regressions catch the old defect

On an archive of immutable foundation `0c1f4f7`:

- 33 selected API regressions failed. Missing or false acknowledgment still returned 200. Invalid acknowledgment values were accepted. A second database connection could save between the checked read and write.
- Both original real commit-failure cases failed. The connection retained the rejected profile and the API failure could escape through the later history checkpoint.
- Actual single-name and group browser actions each returned 200 without a question after the other tab saved the profile as unrestricted. The saved mode became `allow_only`.

All 55 new API cases pass on the repair. They preserve profile fields, normalized names, duplicate removal, batch and saved-list limits, profile/version/history invariance on rejection, missing-profile handling, post-write rollback and real writer exclusion.

Local logs and browser evidence are under `/private/tmp/jittersim-allowlist-verified.log`, `/private/tmp/jittersim-observation-confirmation-2026-10-03.log` and `/private/tmp/jittersim-observation-confirmation-2026-10-03.json`. Old browser failure logs end in `baseline-single-2026-10-03.log` and `baseline-group-2026-10-03.log`. Dialog screenshots are under `test-results/observation-confirmation-{single,group}-{1920,390}.png` in this worktree.

## Release gates

This repair makes no schema, update, signing or physical Pi change. No original branch or remote branch was changed by this repair. The release coordinator must carry this exact source into the combined candidate and run the final full Mac/Linux suites plus installed-package browser checks. Complete those checks and actual green CI on the exact candidate before merge. Current GitHub jobs are rejected before their steps by account billing. That supplies no code result and remains an external merge blocker.

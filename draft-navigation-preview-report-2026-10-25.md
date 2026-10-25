# Navigation preview recorder repair

The unfinished-form navigation check recorded every non-GET request as a mutation. Its legitimate debounced POST to `/api/profiles/ranges` only computes a preview, so it could arrive between navigation assertions and falsely fail the no-write check.

The sole harness change adds an exact method and parsed-path exemption. Every existing assertion remains in place. Real profile saves, device Apply, other methods, trailing slashes and lookalike paths remain recorded. No dashboard, server or runtime source changed.

Base: `0b8cbf9341a7b51497882686bd41b47ccbeade79`. Branch: `codex/draft-navigation-preview-repair`. Isolated checkout: `/Users/samantha.martinez/Documents/Codex/2026-10-24/task/draft-navigation-preview-repair`.

The fake-page/request probe at `/tmp/draft-navigation-recorder-probe.py` exercises the actual nested listener installed by `check_draft_navigation`. Before the edit it failed because the exact preview POST was recorded. After the edit all 12 classification cases pass. The probe covers preview queries, ordinary GET, genuine profile/device mutations, path prefixes/suffixes, trailing slash, query lookalikes and PUT/DELETE on the preview path. Gating Ruff and `git diff --check` pass.

The coordinator independently reviewed frozen source commit `78d2380844d05a3834bc2df1a0edd6a7e2d0dabb` and cleared the exact four-line change. After slot admission, one affected `dashboard_navigation` module passed in 21 seconds through `scripts/local-ci.sh --python 3.12 --only dashboard_navigation --step-timeout 180 browser`. The wrapper's setup steps also passed. This is a focused local result, not a full browser run or hosted CI.

The run tested that exact commit with a clean working tree. Receipt: `test-results/local-ci-20261025-105710/summary.md`. Browser log: `test-results/local-ci-20261025-105710/browser_dashboard_navigation.log`. A copy-on-write clone of an existing private virtual environment and cached Chromium was used in this checkout. Package indexes and build isolation were disabled. No dependency downloads, active-validator changes, network publication or remote action were performed. This report update changes documentation only after the validated source freeze.

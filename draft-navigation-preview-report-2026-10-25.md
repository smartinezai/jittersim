# Navigation preview recorder repair

The unfinished-form navigation check recorded every non-GET request as a mutation. Its legitimate debounced POST to `/api/profiles/ranges` only computes a preview, so it could arrive between navigation assertions and falsely fail the no-write check.

The sole harness change adds an exact method and parsed-path exemption. Every existing assertion remains in place. Real profile saves, device Apply, other methods, trailing slashes and lookalike paths remain recorded. No dashboard, server or runtime source changed.

Base: `0b8cbf9341a7b51497882686bd41b47ccbeade79`. Branch: `codex/draft-navigation-preview-repair`. Isolated checkout: `/Users/samantha.martinez/Documents/Codex/2026-10-24/task/draft-navigation-preview-repair`.

The fake-page/request probe at `/tmp/draft-navigation-recorder-probe.py` exercises the actual nested listener installed by `check_draft_navigation`. Before the edit it failed because the exact preview POST was recorded. After the edit all 12 classification cases pass. The probe covers preview queries, ordinary GET, genuine profile/device mutations, path prefixes/suffixes, trailing slash, query lookalikes and PUT/DELETE on the preview path. Gating Ruff and `git diff --check` pass.

Source is frozen for independent review before one affected `dashboard_navigation` browser run through the shared wrapper slot. The coordinator has allowed admission once review completes because three browser shards are already terminal and only shard A remains active. Browser acceptance is pending and is not claimed by this report yet. No network publication or remote action was performed.

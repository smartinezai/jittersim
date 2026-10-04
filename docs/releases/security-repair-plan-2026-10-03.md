# Release security repairs

Three issues block integration of release source `b9b4bbcd9e9008ddb87de3defc55c7eb5f2be407`.
Commands can leave descendants running, tar extension metadata can escape the
archive limits and tag pushes can reach an unintended repository. Fix each issue
with a focused local commit and regression tests. Do not push or publish while
making these repairs. The existing implementation takeover covers this work.

The independent review is dated 2026-10-03 from chat
`01a0fec3`. Its exact source is
`/private/tmp/jittersim-stable-independent-review-2026-10-03.md` with probe results
in `/private/tmp/jittersim-stable-independent-probes-2026-10-03.json`.
The review blocks source approval until corrected behavior is independently checked.

## Repair and verification

1. Keep a command's group leader owned until every group member has stopped.
   Preserve output limits and deadlines. Use a small parent process that reports
   the command result while retaining ownership. Test descendants with inherited
   pipes and with closed pipes on both successful and failed command exits.
2. Read archives through regular-file descriptors without following links.
   Scan bounded gzip output and every physical tar header before a tar parser can
   process extensions. Limit expanded bytes, header count, extension size and
   extension chains. Verify signatures before parsing in preparation and approval.
   Test oversized PAX, long names, sparse entries, truncated input and file changes.
3. Resolve every effective origin push URL before a tag push. Require one accepted
   official GitHub destination and disable extra tag/mirror pushing. Test alternate
   push URLs, both Git rewrite settings, multiple destinations and local mirrors.
   Tests must never contact a real remote.

Keep newer installer ownership, certificate and Panic changes in the coordinated
release worktree. These repairs modify the release tooling in its own worktree.
Run release gates, affected update tests, lint and syntax checks after repairs.
Container jobs are not needed for these stdlib boundary changes.

## Source checks and stable publication

This tool does not decide whether a source change may be merged. A separately
approved source merge may use actual recorded local workflow checks when hosted
checks are unavailable. Local results must be described as local results.
Automatic candidates and this stable publisher currently require successful hosted
main workflow evidence. A future local-evidence approval route would need a
separate reviewed contract. Physical acceptance and exact-package stable approval
remain required regardless of how source integration is authorized.

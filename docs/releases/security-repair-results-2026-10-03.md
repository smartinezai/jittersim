# Release boundary repair results

The three blockers reported against `b9b4bbcd9e9008ddb87de3defc55c7eb5f2be407`
are repaired locally. Independent correction approval and final combined-source
workflow checks remain pending. No tag push, GitHub publication, Pi change or
Docker job was performed during the repairs.

The review source is dated 2026-10-03:
`/private/tmp/jittersim-stable-independent-review-2026-10-03.md`.
The original probe receipt is
`/private/tmp/jittersim-stable-independent-probes-2026-10-03.json`.
The review findings are separate from this repair's internal checks.

## Corrected behavior

The command runner retains an owned parent while the actual command runs and
reports its result. A successful command returns only after all live group members
end. Failure or timeout kills the owned group then confirms termination before
reaping its parent. Closed output streams no longer hide a descendant. Inspection,
output, input and deadlines are bounded. An unconfirmed group refuses further
progress with an inspection message and retains its owned parent reference.

Archives are read through regular no-follow descriptors. Changed file identity or
metadata refuses the read. Compressed inputs have a fixed size limit. A raw scan
bounds gzip expansion, physical header count, extension size, extension chains and
fields before the general tar parser runs. PAX size overrides and sparse entries
are refused. Ordinary long paths and Git's small metadata remain supported.
Preparation and approval verify signatures before parsing and use the same held
bytes for signature, hash and metadata checks.

Publication asks Git for every effective push URL after its rewrite rules apply.
Only one official repository destination is accepted. Forks, local mirrors and
multiple destinations refuse before a tag push or any GitHub release mutation.
The explicit tag push also disables following extra tags and mirror behavior.
All remote tests use local configuration or the existing GitHub stand-in.

## Evidence

Eight regression cases first failed against the original source in
`/private/tmp/jittersim-stable-security-before.xml`.
They cover both closed-stream command exits, three oversized tar extensions and
three unintended push destination configurations. The final full gate run passed
75 tests in `/private/tmp/jittersim-stable-security-fixed-final.xml`.

The command-only repair passed 50 gates in
`/private/tmp/jittersim-stable-repair-command-commit.xml`.
The archive repair passed 64 gates in
`/private/tmp/jittersim-stable-repair-archive-commit.xml`.
The final affected update test run is recorded separately in
`/private/tmp/jittersim-stable-security-affected-final.xml`.
Actual Linux command-group checks and the complete workflow inventory must run
on the final combined source. The local real-process tests exercised macOS.
The prior Linux dependency builds do not prove these new process boundaries.

The existing dependency cache and locks are unchanged. Their manifest is
`/private/tmp/jittersim-stable-dependency-cache-manifest-2026-10-03.json` with
SHA-256 `a6f5f54c633b27942042b2843debda48d37c9908bfa35e95c40526f8cf3fdd27`.
Future final-source checks must use its actual fixed input versions and hashes.

## Release authority

Source merge approval, hosted checks, physical acceptance and stable publication
are distinct decisions. Approved local source checks can support source integration
when hosted jobs are unavailable. The current publisher still requires successful
hosted main evidence and has no manual local-evidence override. No physical
acceptance, Pi order floor initialization or exact-package approval is inferred
from these repairs.

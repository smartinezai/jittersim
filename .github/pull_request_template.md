<!--
Fill in every section. Write "none" or "not applicable" and one reason instead of deleting one.
Hosted CI cannot start jobs (billing limit), so the evidence section below is the gate.
-->

## What changes and why

<!-- One short paragraph. Name the problem first, then the change. -->

## Risk

Risk class (tick one):

- [ ] **A**: docs, tests or tooling only. Nothing the Pi runs changes.
- [ ] **B**: the dashboard page or its HTTP API. Network rules, the helper and the update path are untouched.
- [ ] **C**: network rules, the scheduler, the engine, installation or the update path.
- [ ] **D**: the privileged helper or a hash-pinned Panic file (see the next section).

Household risk (the chance a mistake disturbs the home network): none / low / medium / high.
One line on why:

## Hash-pinned files

Does this change any of `src/jittersim/panic_guard.py`, `src/jittersim/networking/helper.py` or
`src/jittersim/networking/access.py`?

- [ ] No.
- [ ] Yes. A new entry in `PANIC_APPROVED_SOURCES` (`src/jittersim/updates.py`) lists the new
  hashes, the independent Panic-consumer review of that exact bundle is linked here, and this pull
  request says it waits for that review. Without the approved bundle the updater refuses the code.

## User-visible change

<!-- What a person sees or can now do differently, or "none". -->

## README and CHANGELOG

- [ ] README, `docs/` or the Pi guide updated, or no user-facing change.
- [ ] The title reads as a release note. `scripts/release.sh` builds `CHANGELOG.md` from the pull
  request titles. Add a hand-written `## Unreleased` entry only when the title cannot say it.

## Evidence on the head commit

Paste the `summary.md` of `scripts/local-ci.sh` for the exact head commit (see `docs/local-ci.md`).
Name the commit hash. A run on another commit, or with a separate fix applied, does not count.

- Commit:
- Steps run and results with counts (for example `unit`, `lint`, `browser`, `release-checks`):
- Hosted CI: did not run (billing limit), or the link if it ran.
- Not covered, or not run (and why):

## Pre-existing problems

Say which old problem this pull request fixed because it sits in the same code and the fix is small
and safe, and which one it deliberately left. A pull request that leaves an old problem alone can
still be approved.

- Fixed:
- Left:

## Rollback

How to undo this and what state it leaves behind (settings, saved profiles, files on the Pi), or
"revert the commit".

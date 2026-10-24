# Operate user guide update — 24 October 2026

## Scope and ownership

Sole docs writer for `docs/user-guide.md` and this plan. Prepare outside the
repository until the coordinator confirms runtime verification is finished;
then apply both files together. No runtime, test, shared planning, release or
production changes; no browser capture, Pi/router contact or commit.

## Bounded update

- Replace the old overview and sidebar utility images with the committed
  desktop Devices, phone Devices and desktop utilities screenshots. Inspect
  their pixels and provenance; label them as synthetic fixtures.
- Describe the full-width header/page links, compact desktop profile/Start/Reset
  row, persistent Panic/read-only router bar, Controls utilities, and expandable
  status strip at all sizes. Keep phone local link scrolling and 44px targets.
- Correct nearby sidebar references made false by this layout (update/version,
  credentials, Stop and address-change status). Preserve functional, security,
  network and physical acceptance statements.

## Verification

Compare only the bounded prose/image changes against frozen source and inspected
screenshots. Check relative file links and image paths for existence, audit
remaining sidebar wording and run `git diff --check`. Report exact changed paths
and link results. Do not run browser/source gates or modify production.

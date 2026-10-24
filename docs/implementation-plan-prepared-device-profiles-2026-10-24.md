# Prepared device profiles — allocation plan

24 October 2026. PLAN ONLY; no source edits, test execution, browser loads or PR writes.

## Evidence and base

Read API head `47a1fc1ddc2a6cd77f1420356b4bdfa8c0d062cf` in
`/Users/samantha.martinez/Documents/Codex/2026-10-24/task/pending-device-api`:
`src/jittersim/api/pending_devices.py`, pending call sites and final transaction in
`server.py`, `config/pending_devices.py`, and `tests/unit/test_pending_device_api.py`.
Read `task-9/stage2-locked-activation-review.md`; this plan consumes its decisions,
does not repeat API review. UI reference is immutable PR240 head
`b44b38ccb000e7cd9ad6c4056e0f994663e0f60e`, existing Operate DESIGN/surface brief.
Coordinator must allocate a new isolated branch from its reviewed composition
containing the API plus Operate UI; do not modify PR240 or assume draft PR242/243
is already merged. Record exact allocated base before implementation.

## Smallest consumer

Add one ordinary card on Devices, immediately after Connected clients and before
Saved devices. Heading **Prepared device profiles**; note: “Save a profile choice
for a Wi-Fi MAC address. Saving does not change the network. Use checks for that
exact address on the Pi’s Wi-Fi.” Preserve the distinction from assigned devices.

- Manual MAC, optional display name, existing saved-profile select; **Save choice**.
  No IP, claimed verification, source/observation entry, protection or import UI.
  Create sends only `mac_address`, `action:"profile"`, `profile_id`, `display_name`;
  rely on server default owner-entered source. Edit keeps MAC immutable and sends
  exact `expected_revision`; changing MAC requires a separate new choice.
- List count and existing card/row components; explicit Refresh and 20 rows per
  page (native Previous/Next), at most the API's 256 records in memory. Stable
  MAC/revision keys, textContent for names, no unbounded DOM or new virtualiser.
  Empty/loading/error/locked states are distinct. Capacity refusal preserves input.
  Existing protect records may be listed as “Prepared protection — use unavailable”
  with explicit CAS delete, but cannot be converted or activated by this profile UI.
- Each profile row offers Edit, Delete and **Review**. Delete confirms, then POSTs
  `/api/pending-devices/delete` with `mac_address`, `expected_revision`. Saving,
  deleting, list refresh and review never invoke Apply or helper discovery.
- Review GET `/api/pending-devices/review?mac=…` opens an inline labelled review
  region, focuses its heading, and presents returned MAC, name, current profile
  ID/name and settings, configuration version, pending revision and prepared time.
  Use existing profile summaries plus expandable complete settings; summary alone
  must not omit consequential website/outage/advanced settings. The review says
  identity has not yet been checked; a matching name is not physical identity.
- A separate **Use profile for this MAC** action requires this current review,
  existing allow-only acknowledgement where applicable, and deliberate confirmation
  naming the exact MAC/profile. POST ONLY `{pending_mac,
  expected_pending_revision, expected_configuration_version}` to existing
  `/api/connected-devices/apply-profile`. Never forward a legacy IP/profile payload,
  trust a cached connected row, or claim Review guarantees live eligibility.
  Server alone resolves fresh associated/reachable unique same-MAC identity.

## State, privacy and safety

Reuse `request`, `adminTokenRevision`, `privateDataLockHooks`, `loadPrivateDataPanel`,
`networkActionBusy`, `actionIntentRevision`, `setActionBusy`, `setButtonBusy`,
`confirmFromButton`, `askAllowOnly`, profile labels/summaries and existing status/
refresh machinery. A local request serial plus token/action ownership guards every
list/review/write completion. Token change/access loss clears inputs, rows, profile
details, errors, review and confirmation; late responses cannot repopulate them.
No local/session storage, new global scheduler, background helper scan or retry.

Invalidate review on row/profile/configuration change, explicit refresh/edit/delete,
Panic/Reset/action-intent change, lock, navigation away and any Use attempt.
Rerender must not replace focused dirty form controls. Disable double submissions;
Panic remains immediately usable and overtakes Use through existing intent guards.
409 requires explicit Refresh/Review; never silently retry even CRUD writes. On a
dropped/timed-out write say outcome unknown, retain prepared record, refresh reads
only, require reconciliation and a new review/click before another Use. Preserve
server message/warning and truthful saved/live result; HTTP 200 alone is not proof
of live traffic, and an error does not prove rollback. Do not fall back to the old
Apply helper's generic “saved and applied” message. Pending stays after success.

Reject non-integer/negative or unsafe JavaScript version/revision values with
`Number.isSafeInteger`; do not round, coerce or widen the API contract. Review
must match the selected normalised MAC/profile action and valid returned profile.
If any required value is missing/malformed, Use stays disabled with refresh guidance.

Use existing native forms, row wrapping, disclosures and confirmation/focus return;
restore focus to surviving row control or card heading after deletion. Status is a
quiet polite region, errors actionable, no repeated full-list announcements.
Verify 44px touch targets, keyboard/Cancel/Escape, sticky safety-bar clearance,
320px large-text wrapping and no page horizontal overflow. No navigation/layout,
router adapter, protected-access or helper protocol changes.

## File ownership and bounded verification

One UI writer owns `src/jittersim/api/dashboard.html` (card), `dashboard.js`
(local state/render/actions/private lifecycle), and only if necessary a few scoped
rules in `refresh.css`. Prefer these existing files over a new module/assembly
registration. No `server.py`, `pending_devices.py`, helper or storage edits.

Add `tests/unit/test_dashboard_prepared_devices.py` with existing offline JS/DOM
harness style: strict payloads/CAS, unsafe revisions, bounded rendering, no implicit
Use, 409/uncertainty, lock/late response, stale review/action invalidation and truthful
saved/live outcomes. Add one `tests/browser/prepared_devices.py` using synthetic
fixture routes: full create/edit/delete/review/explicit Use plus delayed responses,
keyboard/focus, errors and mobile geometry. Narrow registration only in
`scripts/local-ci.sh` / `scripts/local-ci-changed-map.txt` if required; update
`tests/unit/dashboard_ids.txt` only if the existing ID inventory requires it.
Docs writer stays with the same lane: one concise user-guide paragraph and evidence
receipt, not a redesign documentation refresh.

After independent delta review, run new offline tests plus affected auth/concurrency,
Panic and assembly/ID tests, lint and existing static budgets. One shared-wrapper
browser slot: new consumer module and affected existing `device_apply_start` /
`dashboard_access` checks only; batch desktop and 320/375/390 layouts, large text,
dialog/focus and axe on this card. One defect-fix batch and confirm only failures.
Reuse API208+lint and PR240 receipts with explicit source/head provenance; do not
repeat full native/browser/GUI suites. No baseline/budget expansion without applicable
approval; if existing approved measurement rules do not cover new source, report
the measured blocker. Real phone/Safari/keyboard/assistive-tech/hardware acceptance
remains owner-only and unclaimed.

## Size and allocation recommendation

Estimate 35–55 HTML lines, 220–320 JS lines, 0–20 scoped CSS lines; approximately
200–300 targeted test/fixture lines, small registration/docs changes, no dependency.
One source writer; one independent delta reviewer after freeze, with read-only test
preparation parallel only if coordinator allocates it. No duplicate original audit.

Supported usage returned 87% used / **13% remaining**, against **8% reserve**:
five percentage points for implementation, review, focused tests and handoff shared
with other lanes. The consumer is technically feasible as this single bounded slice,
but the margin does not justify starting before coordinator gives a dedicated
allocation and rechecks allowance. No claim that the remaining shared allowance
guarantees completion. Stop before the floor, preserve a clean reviewed snapshot
and exact outstanding checks if margin becomes insufficient. Await root review;
no implementation or test loads have started.

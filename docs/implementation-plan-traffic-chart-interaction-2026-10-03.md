# Implementation plan: clearer traffic lines and inspection

## Human review summary

Make every traffic line easy to identify. The reported orange and yellow dotted
lines are shared profile upload and download limits. The chart draws them in
the shared view but hides their legend entries. Fix that mismatch first. Then
use matching colours and line patterns throughout the graph and legend. Hover
over a line or its legend entry to emphasise it and fade the other lines. Show
its name, value and sample time. Provide the same identification through
keyboard focus and tap.

Keep the existing canvas and traffic API. No chart library, new polling,
database change or packet-handling change is needed. The main risks are
selecting the wrong line when traces overlap, showing an old limit as current
and losing selection when the chart refreshes. The full scale will still
include configured limits. A separate view for small traffic rates can be
considered later.

Status: implemented and locally verified on 3 October 2026.
The owner authorised the reviewed plan and routine decisions for 24 hours,
ending 4 October 2026 at 00:54:56 CEST. Final publication is authorised after
local verification. Merge still requires green CI on the final source. The
separate release coordinator owns combined merges and deployment.

## Evidence and implementation base

- Inspected checkout: `codex/master-continuation` at
  `2fbaff5c8726fce961dc315243085e31dd92ea05` on 3 October 2026.
- In `src/jittersim/api/server.py`, `drawTrafficChart` hides profile legend
  entries when the aggregate device option is selected (lines 2532–2535).
  `trafficReferencesFromResponse` supplies shared profile references for that
  same selection (lines 2646–2655). The lines are still drawn.
- A disposable Node reproduction executed these existing functions with shared
  limits of 10 Mbit/s upload and 4 Mbit/s download. Both dotted lines were
  drawn while both legend entries were hidden. No production files changed.
- The current colour literals do match their corresponding swatches
  (styles at lines 453–458 and series at lines 2600–2606). However the measured
  and ISP colours are close in pairs. Missing profile labels make the visual
  correspondence harder to understand.
- The local integration ref `integrate/main-into-stack` at
  `600c476f2dbf2821a526fd8790eefe68f2897de3` has the same visibility mismatch.
  It uses `@all` for the aggregate option instead of `all` and hashes inline
  scripts and styles for its content security policy. Re-read the actual base
  before implementation. Local refs do not establish the latest remote state.
- Existing GUI drafts already discuss palette changes, chart summaries and
  hover values. This focused plan adds the shared-view regression and line
  emphasis requirements. Coordinate overlapping work before touching their
  files. Preserve all existing untracked drafts and assets.
- No pull request template was found in this checkout. Provisional risk is
  low: dashboard presentation only. Recheck the chosen base for its current
  template and delivery rules before opening a pull request.

## Learning objective

The central reasoning is keeping the drawn lines, their labels and their
interaction tied to the same description of the data. They must remain in
agreement through shared mode, device changes, missing limits and refreshes.
Routine styling and test setup should not distract from that reasoning.
Implementation remains with the owner unless a takeover scope is authorised.

## Proposed appearance

Use one description per line to supply its identity, name, colour, pattern and
scope to rendering, legend generation and inspection. Use a longer swatch so
solid, dashed and dotted styles are visibly different. The proposed palette
keeps the familiar traffic and profile colours while separating the ISP lines.
Review actual screenshots before accepting the exact hues.

| Line | Proposed colour | Pattern | Visible name |
|---|---|---|---|
| Upload measured at the Pi | Blue | Solid | Upload traffic |
| Download measured at the Pi | Green | Solid | Download traffic |
| ISP upload reference | Violet | Dashed | ISP upload plan |
| ISP download reference | Cyan | Dashed | ISP download plan |
| Applied profile upload limit | Orange | Dotted | Shared profile upload limit or Device upload limit |
| Applied profile download limit | Yellow | Dotted | Shared profile download limit or Device download limit |

Show a matching legend entry for every line actually present, including limits
that appear only in retained history. Do not show a configured line for an
unknown limit. Zero measured traffic remains a valid value. Use text and
patterns alongside colour. Keep the explanation that traffic is counted at
the Pi before shaping and that service-specific limits may differ.

## Interaction behaviour

1. Hover close to a drawn line to emphasise it and fade the other lines while
   keeping them visible. Show the same emphasis on its legend entry. Hovering
   or focusing a legend entry identifies that line without precise aiming.
2. Show a readable detail area with the full line name and value with units.
   For traffic use an actual sampled rate and label the interval ending time,
   such as "8 seconds ago". Rates are averages between polls. Do not present
   an interpolated position as another measured sample. For historical limits
   say "Recorded limit 8 seconds ago". Distinguish configured/applied limits
   from measured speed and shared limits from one device's limits. The API
   does not tell us when a setting last changed, so do not invent that age.
3. Inspect the actual visible segments. Reference limits change in steps.
   Dashed gaps should not make a line impossible to select. Empty history and
   missing values must not acquire invented readings or connections.
4. If multiple lines occupy the same position, identify the overlapping
   candidates instead of silently assigning the point to whichever was drawn
   last. Their legend entries provide an explicit way to select one.
5. Use real legend buttons for keyboard and touch access. Focus provides the
   same name and latest available value. Click or tap pins emphasis. Selecting
   the pinned entry again or pressing Escape clears it. Pointer exit clears
   temporary emphasis and leaves a pinned choice intact. The detail area must
   stay readable without hover.
6. Reconcile temporary inspection after each sample or resize using the current
   pointer location. Keep a pinned choice by line identity. Clear it if the
   line disappears. Clear inspection on device/scope changes, history reset,
   sign-out and unavailable data. Keep stale samples explicitly labelled.
7. Keep the legend nodes stable during polling so keyboard focus survives.
   Do not announce every pointer movement or two-second sample. Provide an
   accessible name and concise text values beside the canvas.

## Manageable subtasks

1. Review this plan and its independent review findings. Obtain owner feedback
   on the summary before production code begins. Confirm the coordinated base.
2. Fix legend visibility and scope names using the lines that are actually
   shown. Add a focused regression for the reported shared-view case plus
   missing limits and selected-device mode.
3. Consolidate line identity and appearance. Make canvas strokes and legend
   swatches agree. Verify both a wide graph and a narrow graph with low rates.
4. Add pointer inspection, emphasis and the detail area. Cover overlap, steps,
   gaps and coordinates at different display pixel ratios.
5. Add stable legend buttons, focus inspection and pinned selection. Verify
   refresh, resize, keyboard dismissal and touch without hover.
6. Complete the focused browser journey and review the complete change for
   performance, accessibility and misleading values. Then perform the agreed
   local checks. Publish only after separate authorisation.

## Verification that adds confidence

- Use the existing Node harness pattern for meaningful state and geometry
  cases. Check the shared-view regression, finite versus missing values,
  historical limits, zero traffic, stepped limits, overlap, line disappearance
  and scope changes. Avoid tests that merely repeat colour literals.
- Add a browser journey with fixed fake samples. Exercise real pointer motion
  on the orange and yellow lines plus legend hover, focus and tap. Verify the
  displayed identity and value, selected stroke and faded alternatives.
- Check pointer exit, Escape, a pinned line, empty data, failed refresh,
  changing limits and stable keyboard focus across at least three polls.
- Run the journey at desktop width and 375 CSS pixels with display pixel
  ratios of 1 and 3. Check wrapping, clipped details and closely spaced rates.
  Review greyscale readability and visible focus. Emulated touch is not proof
  of behaviour on a physical phone.
- Verify that the same lines appear in the graph and legend in ordinary
  aggregate mode, shared mode and selected-device mode. Include an unknown ISP
  upload setting. Preserve the distinction between current and historical
  references when a setting is removed.
- Pointer activity must issue no requests or writes. Keep the existing
  two-second sampling interval, 60-second history and 30-point limit. Reuse one
  bounded redraw schedule rather than accumulating work for every pointer
  event. Test against all six lines and a full sample buffer.
- Run the affected tests, existing general-profile and navigation journeys,
  focused Ruff checks, served JavaScript syntax checks and `git diff --check`.
  Inspect the actual assembled page rather than testing an invented copy.
- On the integration base verify that the served security header contains the
  correct hashes for every assembled script and style. The existing code
  computes those hashes automatically after assembly. Exercise inspection
  with that policy enabled and fail on security-policy violations or script
  errors. Do not disable the policy to make the browser test pass.
- Register the new journey in the browser job on the chosen base with a
  finite timeout and failure evidence. Run the normal clean CI checks on the
  final source when publishing is authorised. No new privileged network test
  is needed for a presentation-only change.

## Security, delivery and recovery

Use existing authenticated traffic data and local browser state. Add no
outside requests, telemetry, credentials, persisted preferences or endpoints.
Render names and values as text. Inspection must never submit a form, change
an ISP setting or apply a profile. The controls must be explicit non-submit
buttons.

Keep the existing canvas and inline packaging for this focused change. The
integration base's content security policy must remain active and accept the
assembled page. Do not add style attributes, external scripts or a CDN. If
shared asset/build configuration changes become necessary, revise the plan
and verify a clean package/container before accepting that expansion.

No migration or backfill is required. Ship through the normal reviewed app
release once authorised. The installer already carries the dashboard source.
Coordinate Pi acceptance with its release owner, using management access and
known recovery controls. Verify installed revision, shared-limit labels and
real hover behaviour. Revert the dashboard change through the normal release
path if inspection fails. Packet handling and saved configuration do not need
recovery work for this change.

## Acceptance and review gate

The change is ready only when every drawn line has a matching visible name,
the reported shared orange/yellow case is covered and users can identify a
line with pointer, keyboard or touch. Refreshes must preserve valid focus and
selection without adding requests or unbounded work. Passing tests and owner
comprehension of the shared-view decision are separate checks.

## Plan review

Reviewed on 3 October 2026 against the inspected source and local integration
ref. The review confirmed the shared-view diagnosis and the need for overlap,
history and refresh checks. Two verification details were clarified:

- Explicitly verify the assembled page against its browser security policy.
  Source inspection confirms hashes are computed automatically. A manual hash
  replacement is unnecessary.
- Label measured sample intervals separately from recorded reference limits.
  Do not imply that the reference is measured speed or claim to know when its
  setting changed.

Tap-to-pin remains in the proposed scope because the same legend buttons
serve mouse, keyboard and touch. A physical-phone check remains separate from
browser emulation. There are no remaining blocking plan findings. Production
behaviour and its tests have not been implemented or verified.

The owner accepted the plan and authorised implementation and publication
for 24 hours. Merge requires green CI on the final source and the release
coordinator owns combination checks, merges and deployment.

## Implementation checkpoint

The isolated branch is `codex/traffic-chart-interaction` from published
`origin/integrate/main-into-stack` at
`cf2a6fe44ec245061839a44b09a57e0b28782a6a`. The current chart keeps its axis,
sampling cadence and bounded history. It adds shared/device/everyone-else
limit labels, matching canvas legend swatches and pointer/focus/tap inspection.
An independent complete-change review identified missing ages in overlapping
line details. The corrected readout identifies the kind and age of every
candidate. Device selection changes also clear the previous profile limits
before the next reading. Event setup stays separate from credential handling.

A brief comprehension check was presented during verification: explain why
the graph and legend should use the same visible-line state. The answer is
pending. This does not delay authorised frontend delivery.

## Final local verification

- Complete suite: 2,958 passed and 37 platform-specific checks skipped.
- Fresh wheel built and installed in a disposable Linux container with network
  access disabled: 459 relevant checks passed. The imported server came from
  the installed wheel rather than the source directory.
- Expanded traffic-chart browser journey passed at 1440 and 375 CSS pixels
  with display pixel ratios of 1 and 3 plus touch emulation. Both dotted lines,
  overlaps, keyboard focus and a pinned line across three polls were checked.
  Pointer inspection also passed after changing the viewport width.
- Existing general-profile, navigation, core dashboard and phone-layout browser
  journeys passed. Phone layouts include 320, 360 and 390 pixels with normal
  and wider fonts.
- Served JavaScript syntax, hashes in the browser security policy, focused lint
  and whitespace checks passed. Desktop and phone captures were inspected.
- The complete change was reviewed. The overlap-age finding is fixed and no
  blocking source findings remain. Browser emulation does not establish
  acceptance on a physical phone or the installed Pi.

A normal clean-container build first failed because its package download
could not resolve the package host. The separate successful offline wheel
check establishes packaging and Linux compatibility. It does not replace
published CI or the coordinator's combined-release checks.

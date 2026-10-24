# Keep schedule times together

The backlog reports that End wraps onto a row by itself at 1366 × 768.
First reproduce that on the current dashboard. If it remains visible, change
only the schedule form layout so related times stay together without
shrinking controls or changing how schedules work. Preserve the existing
dark style and phone layout. The main risk is moving the awkward wrap to
another width. There are no open product decisions.

## Scope and evidence

- Base: ec403d0f8f347b430398ee5cc281add1aa309318.
- Read DESIGN.md and the current form before choosing the smallest change.
- The current form has six direct grid items. Its shared grid repeats
  columns with a 12rem minimum, so a five-column container can strand End.
- Use a served local fixture at 1366 × 768 to confirm the reported baseline.
  If it is already fixed, stop without changing production code.
- Own only schedule-specific rules in dashboard.css and a focused geometry
  test reached from the existing schedule journey. Change markup only if a
  coherent relationship cannot be expressed safely in CSS. Leave IDs, labels, handlers, defaults and validation intact.

## Intended layout

Read identity, target and profile first, then days and the time range.
Keep Start and End together at desktop widths. A schedule-local 16rem
column minimum can prevent five-column rows within the current content
width cap. Bound that minimum by 100% for the smallest windows. Keep DOM
and keyboard order unchanged. Use the existing spacing and control sizes. Narrow layouts must
fit 320px and retain normal stacking, wrapping and visible keyboard focus.

## Verification and delivery

1. Record baseline geometry and desktop and phone screenshots using the
   shared browser queue and locks. Inspect the existing width thresholds.
2. Apply one narrow layout change and add a geometry regression assertion
   that fails on the reproduced baseline.
3. Check 1366 × 768, wider desktop, 768px, 375px, 320px and a 200% zoom
   equivalent viewport. Check long target/profile text, time values, day
   selection, form validation and keyboard focus. Reuse existing journeys
   where practical. Limit visual inspection to two batched rounds.
4. Run the layout detector once on the changed CSS. Review its findings in
   context because the stylesheet contains unrelated established rules.
5. Review the narrow diff, record evidence under ignored
   test-results/schedule-form-fit/report.md and make a local atomic commit.
   Do not push or open a pull request.

## Risk and operations

This is a low-risk browser layout change with no API, stored-data,
scheduling, deployment, authentication or network behavior changes. Existing
packaging ships the stylesheet. Reverting the CSS and its focused test
restores the old layout. It adds no work that grows with traffic or data.
No physical router or Pi verification is needed for this visual scope.

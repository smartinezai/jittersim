# Keep device controls usable during partial failures

## Summary for review

Two small failures can stop the device workspace from showing valid information.
A failed traffic reading currently prevents successful timer, usage and timeline
data from rendering. Concurrent workspace requests can also clear a timeline
entry before another request reads it for its response. Keep each successful
result independent of these failures without changing the API or adding another
   cache or service.

Implement the fixes separately after independent review and human feedback.
The proposed production change is risk B under the PR template with low
household risk. It affects dashboard reads and display only. Preserve the
existing controls, page layout, authentication and checks that discard replies
for a previous device or sign-in. No product decision is needed. The exact
unavailable traffic wording remains a small review choice.

## Current evidence

- At main `3d60f8a8`, `src/jittersim/api/workflows.js:422` receives the workspace
  response then line 424 waits for traffic before rendering anything. A source
  extraction check with a successful workspace response and failed traffic read
  reached zero rendering calls. On first open this hides Stop controls for
  existing temporary runs. Later failures leave old details visible.
- `src/jittersim/api/workflows.py:102` checks the timeline cache then line 104
  clears the shared dictionary when it has 16 entries. Line 107 reads the key
  again. `ControlServer` uses request threads and `response_for` has no enclosing
  lock. A deterministic source extraction check paused one cached request at
  line 107 while another request cleared the cache. The first raised `KeyError`.
  Sixteen entries can accumulate from time windows and edits even with one
  device. Concurrent tabs make the failure possible.
- Existing workspace coverage checks immediate response to a reached data
  limit. Shared traffic coverage checks sign-in changes and a fresh graph
  baseline. These checks do not cover the two failures above. Source extraction
  checks are narrow evidence and do not replace the repository test suite or
  browser checks. System Python could not import the full server because its
  `requests` dependency was unavailable.

## Proposed work

1. Keep the retrieved or computed timeline in the request's own local value
   and use that value in its response. Retain the current cache key and small
   retention limit. Clearing retained entries must not remove a value already
   selected for a response. Avoid holding a lock across timeline construction,
   database reads or helper calls. Do not broaden this into new caching tools.
2. Render successful workspace data independently of traffic sampling. A
   traffic-only failure should mark the traffic line unavailable and discard
   its previous rate baseline. The next successful samples must establish a
   new baseline before showing a rate. Preserve the shared traffic reader and
   its sign-in checks. Capture the sign-in identity before the workspace GET
   and validate it before rendering controls and before every delayed traffic
   update. Catching a stale traffic rejection must never allow old private
   data to render. Do not hide Stop controls or erase valid workspace data
   when only counters fail.
3. Keep the two fixes independently reviewable with focused regression checks.
   Add only the tests needed to protect these failure paths. Do not change
   unrelated polling, update controls or endpoint response fields.

## Verification

- Reproduce cache eviction during a second request with explicit barriers.
  Both requests must return their selected timeline without an exception.
  Cover cached and newly computed selections. Check normal cache reuse and
  existing retention behavior as well. The current 16-entry target can briefly
  be exceeded by concurrent misses. Strict synchronization of that target is
  separate from fixing the demonstrated disappearing entry.
- With a successful workspace response and failed traffic read verify that
  temporary runs, their Stop buttons, usage and timeline still render. Verify
  the unavailable traffic text and the next successful sample's fresh baseline.
  A delayed traffic reading must also leave controls promptly usable.
- Delay a reply then switch devices or change the sign-in. Verify that the old
  reply cannot repaint private details or traffic on the new view.
- Run relevant workspace, shared traffic and polling tests plus lint. Inspect
  the normal workspace and failure state in a browser. Compare layout, focus
  and keyboard use. Exercise Stop timer and recovery after counters return.
  Disclose browser or physical-device gaps separately.
- Before publication run the repository's required checks for the exact head
  and review the complete diff. Reuse valid existing environment evidence only
  where the relevant source is unchanged.

## Operating scope and recovery

No schema, migration, build input, new endpoint, stored data or retention change
is needed. Existing private routes remain behind the same authorization. No
new identifiers or readings should be logged. These dashboard changes travel
through the normal build and release path. Physical installation remains with
the release owner. No Pi action is part of this plan.

Keep existing polling intervals and bounded retained results. More open tabs
must not create retries or extra background workers. Existing error reporting
is sufficient. Reverting the relevant commit restores prior dashboard behavior
without changing saved settings or network state.

## Learning checkpoint and approval

The central reasoning is why a valid controls response should survive an
optional measurement failure and why a shared dictionary lookup is not a
whole-request concurrency guarantee. Before readiness review predict the
result when traffic fails after the controls response or when another request
evicts a selected timeline.

Pause before production implementation until a human has considered the
independent review, read this summary and given feedback.

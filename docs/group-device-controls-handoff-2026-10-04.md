# Device group controls handoff

## Result and release boundary

The group form now explains its profile choice and uses labelled device
checkboxes with a selected count. Saved groups offer Edit devices, Save changes
and Cancel. Removing a member keeps its saved device profile. Deleting a group
explains the removal of its shared limit, schedules and running scenarios.

The implementation is isolated at
`/Users/samantha.martinez/Github repos/jittersim.worktrees/group-device-controls`
on `codex/group-device-controls`. Its base is
`04c982d6150d225c3be6cc082573afc64a656842`.
The source plan is `docs/implementation-plan-group-device-controls-2026-10-04.md`.
Only the three dashboard source assets change application behavior.
The existing private-data hooks and profile-loaded callbacks remain in place.
There are no database, helper, network or deployment changes.

Publication and final combined release verification remain with the release
coordinator. No Docker use, push, merge or Pi write was performed for this work.
Integrate with the separate profile-picker and unfinished-form changes before
running final release checks. This branch does not assert a full final-release
Python matrix or Linux acceptance.

## Verified behavior

- Pending profile and member choices survive successful refreshes. Checkbox
  rows are updated in place so keyboard focus survives too.
- A missing selected profile remains selected with a warning. Saving is blocked
  until a valid choice is made.
- A missing selected device remains checked and visibly unavailable. It must
  be removed or verified again before saving. A selected connected client needs
  a group profile if it has no saved profile.
- Saving a new connected client records the chosen group profile as that
  client's saved profile. Removing it later does not clear that profile.
- Membership conflicts remain visible with disabled choices. The 15-member
  limit prevents additional selection without preventing removal.
- Failed saves retain the draft and the error. Failed group reads block saving
  without clearing the chosen profile or checked devices.
- A held save plus a second submit event sends exactly one POST. Inputs and
  Cancel are disabled during the request. Other group edit and delete actions
  are guarded while that save is pending.
- Credential changes clear private group names, member details, choices and
  draft state.

## Final verification

884 affected Python checks passed on final application source in 29.40 seconds.
The command covered the complete API module, affected dashboard test modules,
device-management integration tests and the helper saturation recovery module.
Configured lint, JavaScript syntax and diff checks passed.

A fresh wheel contains all 89 application files with bytes matching final
source. Its SHA256 is
`d001ee437964a56d38a90273f692b74e55b2974a15d0fcb989bffd098963c0dd`.
The wheel is `test-results/group-controls-wheel/jittersim-0.2.0-py3-none-any.whl`.
The browser fixture imported the unpacked wheel rather than source.

The group journey passed at 1920×1000 and 390×844. It exercises creation,
keyboard selection, removal, cancellation, missing profiles, unavailable
devices, failed saves, failed reads, duplicate submission, exclusive membership,
the member limit and credential invalidation. Desktop 200% zoom and horizontal
overflow checks passed. Both captured layouts were visually inspected.

The packaged protected-client and navigation journeys passed. The first final
protected-client run failed its existing saved-row color comparison. An
unchanged-wheel rerun passed at both widths. Preserve this first failure as a
possible timing-sensitive check rather than describing the whole browser run
as failure-free.

## Full diagnostic failures and corrections

The earlier full Python diagnostic collected 5305 checks. It had 17 failures
and one skip. It ran before the final test updates and small source refactor.
It is not a full-suite pass tied to the final commit.

- Two API profile-menu checks lacked the new group-control callback in their
  isolated JavaScript test helpers. Added that callback stub.
- The apply-summary check expected groups to be considered loaded even after
  a failed read. Updated the expectation to require a successful read.
- The device-name check assumed group choices were option elements. It now
  checks use of the existing naming helper by the checkbox-choice builder.
- The protected-client group check assumed a native select and also captured
  unrelated new event registration. It now exercises the separate choice
  builder and keeps the same expected eligible device identities.
- Eleven saved-profile checks lacked the new callback in their shared test
  helper. Added the stub while retaining their existing assertions about
  refresh, missing profiles, seed bounds and overlapping edits.
- The helper saturation recovery check failed with connection refusal. No
  helper source changed. Its isolated rerun and the final affected run passed.

All affected modules above passed in the final 884-check run. The exact failed
test names and original traces are retained in
`test-results/group-controls-verification/full-diagnostic.log`.
Final output is in `final-python.log`. Browser output is retained in
`final-browser-first-run.log`, `protected-navigation-retry.log` and
`group-browser.log`. The wheel build log is `wheel-build.log` in the same
verification directory. Layout captures are
`test-results/group-devices-1920.png` and `test-results/group-devices-390.png`.

## Learning checkpoint

The central concept is keeping pending choices separate from saved membership
and the profile currently applied. A prediction about the profile retained
after removing a newly assigned member was requested. No answer has been
recorded in this chat yet.

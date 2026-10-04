# Make device groups understandable

## Human review summary

Make it clear which devices belong to a group and what profile they will use.
Rename Profile override to Profile for this group. Replace the native multiple
selection list with labelled checkboxes. Show how many devices are selected.
Provide an explicit Edit devices action on saved groups with Save changes and
Cancel controls. Unchecking a member removes only its group membership when
saved. Its saved device assignment remains.

Keep the existing group rules and authenticated endpoints. A group contains
1–15 devices and each device can belong to only one group. New clients without
a saved assignment need a group profile. That profile also becomes their saved
device profile. Removing them later can therefore leave the same profile active.
Explain this consequence without promising that removal makes them unaffected.

Risk is moderate because saving membership can immediately change live traffic
and shared limits. Fix the pending profile choice being lost during refresh as
part of this work. Keep edits separate from background data refresh. No database
change or new dependency is proposed. Confirm the release checkout before
implementation because dashboard files have been split in the release worktree.

Status: implementation authorized on 4 October 2026 with “ok do it” after the
owner considered the proposed controls. Source review for implementation uses
the isolated `codex/group-device-controls` branch based on `04c982d`.
That base already preserves valid profile choices. This change additionally
preserves missing choices with a warning. Its newer group deletion also removes
group schedules and running scenarios. The confirmation covers those effects.

## Evidence from the current source

- At inspection the main checkout is `codex/master-continuation` at `f22d904`
  with unrelated local changes. Preserve them.
- `src/jittersim/api/server.py` contains the form, group rendering and edit
  handlers. `group-devices` is a native multiple selection list with no visible
  instructions for selecting several devices or removing members.
- `updateGroupMemberOptions()` remembers selected options across list refreshes
  but drops selections that no longer appear. Focus protection covers only the
  existing select. A checkbox replacement needs deliberate draft preservation.
- `loadProfiles()` remembers the previous value but restores it only for the
  general profile picker. Refresh therefore resets the group profile choice.
- `_save_connected_group()` verifies connected clients, preserves existing
  saved profiles and assigns the group profile as the saved profile for a new
  client. It writes the proposed group in a transaction and validates the plan.
- Saving a group replaces its membership list. Membership conflicts are rejected
  rather than moving devices silently. The model requires 1–15 unique members.
- Group removal preserves device records. Deleting the group also deletes its
  shared data limit through the existing database relationship.
- `engine/service.py` uses the group profile ahead of the saved device profile.
  `engine/runtime.py` allows active schedules, timers, progressions and reached
  limits to change the result. A general profile also takes priority. Copy must
  not promise that every member always uses the selected group profile.
- The local coordinated release worktree at `04c982d` has the same form in
  `api/dashboard.html` with its behavior in `api/dashboard.js`. It assembles the
  dashboard through `api/dashboard.py`. Choose the actual release target before
  editing instead of changing both versions independently.

## Proposed interaction and wording

Keep the existing dashboard appearance. Give device selection its own full-width
area below the group details so long names and touch controls have room.

1. Label the picker **Profile for this group**. Its default choice is **Keep each
   device's own profile**. Supporting text: “Choose one profile for the group or
   keep each device's saved profile.” With a group profile selected explain:
   “This replaces members' saved profiles while the group applies. Other active
   settings may take priority.” Keep the detailed priority explanation in help.
2. Label the membership area **Devices in this group**. Show one checkbox per
   device with its name and address. Display a count such as **2 of 15 selected**.
   Instruction: “Check devices to add them. Uncheck devices to remove them.
   Changes take effect when you save.” Use real labelled checkboxes that support
   touch, keyboard operation and screen readers without modifier keys.
3. Distinguish saved devices from connected clients with no saved profile.
   Beside the latter show **Needs a profile**. If one is selected while keeping
   individual profiles show: “Choose a group profile to add this device. It will
   also become the device's saved profile.” Keep the selection so the user can
   resolve the requirement without starting again.
4. Devices belonging to another group remain visible with a disabled checkbox
   and “Already in [group name]. Remove it from that group first.” Preserve
   protected saved members if they already belong to the group being edited.
   Explain their protection. Do not make protection removal part of this flow.
   Keep protected unassigned clients ineligible under the existing rule.
5. On saved groups show the member names, count and profile policy. Rename Edit
   to **Edit devices**. Editing prechecks existing members and shows **Editing
   [group name]**, **Save changes** and **Cancel**. Keep the group ID fixed during
   editing to avoid accidentally creating another group. Cancel discards local
   edits. Creating a group continues to use **Save group**.
6. During editing explain: “Removing a device keeps its saved profile. Other
   active settings may still apply.” Removing the final member cannot be saved.
   Show “A group needs at least one device. Delete the group to remove it.”
   Provide **Delete group** as a separate action. Its confirmation names the
   group and explains that members keep their saved profiles while any shared
   data limit is deleted. Do not describe it as making devices unaffected.
7. Confirm a successful save with the final member count. Distinguish saved
   settings from confirmed live application using the existing response fields.
   On validation or application failure retain the draft and show recovery.

## State, failure handling and limits

- Track pending members and the pending profile independently from fetched
  device and profile lists. Refresh must retain both selections and focus.
  Opening another group must not silently discard unsaved edits.
- If a selected connected client disappears retain it visibly as unavailable.
  Prevent saving until it is removed or verified again. Do not silently omit
  it from the submitted membership list.
- If a selected profile disappears retain a clear missing-profile warning and
  require a new choice. Never silently replace it with individual profiles.
- Distinguish initial loading, no eligible devices, read failure and expired
  authorization. Prevent saves while necessary data is unavailable. Clear
  private device details and draft state when authorization is invalidated.
- Prevent duplicate submissions. Disable extra unchecked devices at the
  15-member limit while allowing checked members to be removed. Keep server
  validation authoritative for conflicts and changed connection state.
- Reuse existing list requests and refresh timing. Build lookup maps once per
  refresh instead of scanning every group for each checkbox. Introduce no
  per-device request or additional polling. Use a bounded scrolling region for
  long lists. If device inventory grows substantially add shared search and
  paging across device pickers rather than independent fetching here.

## Manageable work steps

1. Review the plan against the chosen release source. Check membership changes,
   profile priority, protected members, shared limits and lost draft risks.
   Classify the change as moderate risk. No PR template exists in this checkout.
   Request a reviewer familiar with group traffic and shared limits. Pause for
   human feedback after the separate review and summary have been considered.
2. Update the wording and membership layout using the existing style. Keep group
   creation separate from editing through explicit form state and actions.
3. Connect the controls to the existing group requests. Preserve draft choices
   across refresh and failure. Update the existing form-navigation integration
   so checkbox changes count as unsaved work.
4. Add focused behavioral checks for draft preservation, missing selections,
   membership removal and limit enforcement. Extend an existing browser journey
   or add a group journey to the browser CI job. Avoid tests that only check
   whether the new labels appear in source text.
5. Review the complete diff for unintended profile or membership changes. Run
   the affected checks and inspect desktop and mobile layouts together. Resolve
   defects in one batch then confirm once before release readiness review.

## Verification and release

- Create a group with two devices using touch and keyboard. Reload and confirm
  saved membership. Edit it, remove one device and confirm its saved assignment
  remains. Cancel a pending removal and confirm nothing was written.
- Check group profile versus individual profiles with differently configured
  members. Add a new connected client and verify its saved profile. Remove that
  client and verify the retained profile is explained accurately.
- Refresh while changing members or profile. Check a disappearing client,
  deleted profile, membership conflict, authorization loss and failed save.
  Confirm no partial change or silently altered draft is submitted.
- Check one member, the final-member removal and 15 members. Check a protected
  existing member and a protected unassigned client. Verify deleting a group
  removes its shared limit while retaining device assignments.
- Reuse `tests/integration/test_device_management.py` and the existing API,
  storage, runtime, credential and form-navigation tests. Extend browser coverage
  served from the actual dashboard package. Exercise helper failure separately
  from successful application. Check long names, narrow widths, 200% zoom,
  labelled controls, focus retention and accessible status messages.
- CI currently installs the package on Python 3.9, 3.11 and 3.13. Its browser job
  installs the package separately. The Linux Dockerfile copies source into a
  clean build. Confirm the updated dashboard assets ship through those paths.
  No shared build change or migration is expected.
- The Pi installer replaces source, installs the package then restarts services.
  Any later authorized release must use the exact verified revision and normal
  installation path. If shared packaging changes verify a clean container build.
- Rollback restores the prior application. Group edits already saved remain in
  the database. Restore membership or profiles explicitly if those need undoing.
  Confirm restored controls read the same saved settings. Do not imply that
  rolling back code undoes a user's group edit.
- Read and write only existing authenticated local device, group and profile
  data. Add no endpoint, external transfer, logging or retention. Render names
  as text. Preserve access checks and verified device identity on the server.

## Learning objective and open decisions

The central design problem is distinguishing a user's pending choices from
saved settings and the profile currently affecting traffic. Before implementing
the state handling predict what should happen when a selected client disappears
or a background refresh returns the old group. Treat correctness and that
prediction as separate checks. Labels and routine layout changes support this
reasoning.

Confirm the intended release checkout and whether this proposed editing flow
matches the owner's expectations. Automatic group IDs and assigning separate
profiles to new clients without a group profile are broader changes outside
this plan.

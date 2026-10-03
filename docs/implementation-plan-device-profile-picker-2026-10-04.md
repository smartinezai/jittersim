# Choose a saved profile when assigning a device

## Human review summary

Replace the Profile ID text field in Assign a device with a native dropdown
labelled Profile. Follow the existing menus by showing profile names and adding their IDs in
parentheses when names match. Save the selected ID through
the existing device endpoint. Reuse the profile list already loaded by the page.

Risk is low. The main risks are silently selecting the first profile after a
refresh and allowing a save while the list is unavailable. Require an explicit
choice and preserve a valid choice across refreshes. No database change or new
dependency is needed. Implementation was approved on 4 October 2026 after the plan was presented.
The work stays local until release coordination accepts the reviewed change.

## Current evidence

- Implementation uses `codex/device-profile-picker` based on reviewed revision
  `04c982d6150d225c3be6cc082573afc64a656842` in an isolated checkout.
- The original plan remains in the primary checkout. Its older application
  files were restored after moving this work into the release checkout.
- The release source has separate `dashboard.html`, `dashboard.js` and
  `workflows.js` assets. The connected-device picker and device editing remain
  part of the form.
- `loadProfiles()` supplies existing dropdowns. The new field uses that same
  request and the existing labels that distinguish identical names.
- Device submission still saves the selected value as `profile_id` through
  `/api/devices`. Existing checks for concurrent device edits remain intact.
- Draft restoration normally adds a missing option. This field instead
  requires an available option so a deleted profile cannot be restored as a
  valid choice.
- The release workflow packages the HTML, JavaScript and style assets and
  already runs the saved-profile browser journey. No build configuration or
  workflow change is required.
- No pull request template was found under `.github`. The low-risk assessment
  above is specific to this small interface change.

## Proposed behavior

1. Use a required native select with the existing `device-profile` ID. Keep the
   visible label connected to the control. Update help text to explain choosing
   a saved profile rather than typing an ID.
2. Start with an empty choice reading Choose a saved profile. Never select a
   real profile automatically. Show each saved profile by name with its ID when names match. Use
   text content so names cannot become markup.
3. Populate the control from the existing successful profile request. Preserve
   the selected ID when it still exists. If it disappears return to the empty
   choice and explain that a new selection is required.
4. While profiles are loading prevent saving. If the list is empty show
   No saved profiles and explain that the user must save a profile first.
   If loading fails show a failure state with a Refresh profiles action as
   recovery. Keep these states distinct.
5. Clear profile options when credentials are invalidated. Restore options
   only after an authorized load. Prevent submitting an empty or unavailable
   selection even through the form's submit handler.
6. Keep the existing device fields and save behavior. Other profile dropdowns
   should retain their current behavior during this focused change.

## Manageable work steps

1. Review this plan against the intended release checkout. Have a separate
   review consider refresh behavior, access loss and the affected test helpers.
   Pause until the human reviewer has read the summary and given feedback.
2. Update the field and help text. Connect its options and availability to the
   existing profile-loading and credential-lock paths.
3. Update existing tests that assume a text input. Add focused behavioral
   coverage for preserving a choice and clearing a missing choice. Extend an
   existing browser scenario to choose a profile by its visible name and
   verify the saved device uses its ID.
4. Review the complete change and run relevant checks before any release work.

## Verification

- Check two profiles with different IDs and identical names. Confirm the
  selected option saves the intended ID without manual entry.
- Check initial empty selection, no profiles, a failed load, recovery and
  credential loss. Confirm none can submit an accidental assignment.
- Refresh after changing or adding a profile. Confirm the pending choice and
  other device fields survive. Simulate a missing selected profile.
- Run affected help-text, saved-profile, sidebar-input and authentication tests
  plus the existing device-management integration tests. Run the existing
  saved-profile browser scenario after extending it and the repository's
  focused lint checks.
- Inspect desktop and narrow layouts together. Check keyboard selection,
  required-field validation, visible labels and long names. Address defects
  together then confirm once.

## Deployment, recovery and growth

The dashboard is embedded in the Python package. The existing Pi installer
replaces source and installs that package. CI installs the package on supported
Python versions and runs browser checks separately. No shared build settings,
container inputs or migrations change. Confirm a clean package installation
serves the updated form before considering release readiness.

The dropdown reads the same authenticated saved-profile data as existing
controls. It introduces no logging, retention, copying to an external service
or new access permission. It adds one pass over the existing list with no
per-profile requests or polling. The current endpoint returns the whole list.
If profile counts grow enough to need search or paging address all profile
pickers together rather than adding another independent fetch here.

No Pi deployment, network change or remote push is included in this plan.
When release work is separately authorized use the tested revision and the
normal deployment path. Rolling back the application restores the text field
without changing saved assignments or the database.

## Learning objective

The central reasoning is keeping a user's pending selection distinct from a
refreshed list of available profiles. Before implementation predict what should
happen when a chosen profile is renamed or no longer appears. Updating the label
and existing test assumptions is routine supporting work.

## Completion

The approved change is implemented locally. The final review checked explicit
selection, preserved device-edit checks, access clearing, late responses and
draft restoration. Verification results are recorded in
`device-profile-picker-verification-2026-10-04.md`. Integration into the composed
release and Pi deployment are outside this implementation checkpoint.

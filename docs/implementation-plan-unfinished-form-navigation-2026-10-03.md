# Implementation plan: find unfinished forms

## Human review summary

The unfinished-form notice gives a count but does not identify the forms or
provide a way back to them. Show the name of each unfinished form as a clearly
labelled action. Selecting one opens its dashboard page and any collapsed
section then scrolls to the form and moves keyboard focus there. Keep every
unsaved value intact.

For example, one unfinished profile would show "Unsaved edits: Profile" with
"Review Profile" as the action. Several unfinished forms would each have their
own action. Keep the saved-settings explanation and the separate discard
action. The notice background itself should not become a clickable container.

Use the existing draft tracking and navigation. The important risks are losing
edits while revealing a form, sending someone to the wrong editor and losing
keyboard focus during background updates. Opening a form must not save,
discard or apply anything.

Status: the owner approved the behaviour and authorised this task on
4 October 2026. Implementation is isolated on
`codex/unfinished-form-navigation` from the inspected release base
`04c982d6150d225c3be6cc082573afc64a656842`. The current chat checkout predates
the notice shown in the screenshot.

## Evidence and scope

- The screenshot matches `renderDraftNotice` and `installDraftControls` in
  `src/jittersim/api/workflows.js` in the inspected release checkout
  `codex/remaining-release-repairs` at
  `04c982d6150d225c3be6cc082573afc64a656842` on 3 October 2026.
- That code tracks drafts by form ID in a page-local map. The notice currently
  renders only the count and explanatory text. Drafts can exist on different
  dashboard pages at once.
- `revealElement` in `src/jittersim/api/dashboard-navigation.js` already opens
  the containing page and collapsed card then scrolls to the target. It
  respects reduced motion. It does not move keyboard focus or close the phone
  controls sheet itself.
- Forms also appear dynamically in `workflows.js` and `access.js`. Device
  workspace forms share one current device and test notes share one current
  session. Returning to those drafts must not select another record.
- The working checkout is `codex/master-continuation` at
  `f22d9041d40aedfa39fd32658e4b857e8845c180`. It does not contain the matching
  notice. This plan is stored alongside the other focused plans in this
  repository. Re-read the chosen implementation base before changing code.
- No pull request template was found in the inspected release checkout.
  Provisional risk is low: dashboard navigation and presentation with no
  changes to saved settings or network effects. Recheck the template on the
  chosen base before opening a pull request.

## Learning objective

Keep the draft's identity, its visible name and its navigation destination in
agreement through page switches and background updates. Styling and fixture
setup are routine work. The owner authorised implementation of this task.

## Proposed behaviour

1. Show a friendly name for every tracked form. Distinguish forms that share a
   card, such as starting a test session and editing test notes. Cover both
   static forms and forms installed after page load. Do not expose internal
   form IDs as user-facing names.
2. Use a separate ordinary button for each destination, styled as a clear
   navigation action. A single draft goes straight to its form. Multiple drafts
   show all names and destinations without a chooser or an arbitrary first
   destination. Keep their order stable while someone types.
3. Where the existing editor already exposes a profile, device or session name,
   include that context when it helps identify the draft. Use the current
   editor context rather than loading a saved record which could replace
   unsaved values. Give unnamed new records a useful form label.
4. Reveal the existing form in this page. Open its page and collapsed card.
   Close an obstructing phone controls sheet. Scroll it into view and focus a
   suitable editable control or the form heading without changing the value.
   A visible focus indicator should make the destination clear.
5. Preserve the existing page-address behaviour. Do not reload the document or
   open another browser tab because drafts live only in the current page.
   A missing target gives a clear message while retaining the draft.
6. Keep navigation separate from the discard button. Retain the existing
   confirmation before discarding. Saving or discarding removes the relevant
   entry through the existing draft lifecycle. A failed save keeps the entry.
7. Retain concise copy explaining that Apply uses saved settings. Only show
   the notice while drafts exist. Make form names and actions readable at
   narrow widths and when names are long.

## Manageable subtasks

1. Review this plan and its review findings. Obtain owner feedback on the
   summary then agree on the release base before production implementation.
2. Inventory every form covered by the current tracking and give each a clear
   name. Review the two forms in the device workspace and the two test-session
   forms separately.
3. Add the named destination actions to the notice. Preserve action nodes
   through background updates so a focused button is not replaced while
   someone is using it.
4. Connect actions to existing reveal behaviour and deliberate focus placement.
   Verify collapsed sections, the phone controls sheet and dynamic editors.
5. Extend existing browser coverage for navigation and draft preservation.
   Review the complete change then run the relevant local checks.

## Verification

- One draft: edit a profile, move to another page then use its notice action.
  Check the page, expanded card, visible form, focused destination and exact
  unsaved value. Confirm no save, discard or Apply request was sent.
- Multiple drafts: edit forms on different pages. Each name takes the user to
  its matching form. Saving one removes only that entry. Confirm typing does
  not reorder the remaining actions or lose focus on a notice action.
- Background updates: allow natural polls while reviewing a draft and while a
  notice action has keyboard focus. Values and focus remain intact.
- Dynamic forms: cover a device workspace draft and test notes. Returning
  keeps the selected device or session and does not fetch another saved record
  into the editor.
- Recovery: a failed save keeps both edits and its notice entry. A missing
  target does not throw or silently discard anything. Cancelling discard
  leaves all entries and values intact. Confirmed discard hides the notice.
- Accessibility and layout: use keyboard and touch at desktop and phone widths
  including 320 CSS pixels. Check long names, an open phone controls sheet,
  collapsed cards, visible focus and reduced motion. Names must remain useful
  without colour or hover.
- Privacy: render names as text. Never include password fields, tokens or
  unrelated form values in notice text, URLs, browser storage or logs.

Prefer extending `tests/browser/journeys.py` and
`tests/browser/dashboard_navigation.py` so the checks run in existing CI jobs.
Run the affected phone and Apply journeys as well. Existing draft tests in
`tests/unit/test_dashboard_access_point_draft.py` use a small DOM substitute.
Update that fixture only as needed and retain its independent checks for
credential exclusion and Wi-Fi-name preservation. Avoid tests that merely
repeat the new labels.

## Delivery and recovery

The inspected release assembles dashboard files in
`src/jittersim/api/dashboard.py`. `pyproject.toml` includes HTML, JavaScript and
CSS in the installed package. The server computes its script and style
allowances from the assembled page. Keep the change within the existing asset
files and event listeners. No new library, build tool or server endpoint is
needed.

Run the affected browser journeys against the disposable fixture then verify
the installed package includes and serves the change. The browser Dockerfile
installs the package into a clean environment and runs the existing journeys.
Shared build configuration need not change. Record the tested source revision
and any unavailable CI or clean-build check explicitly.

Drafts remain in browser memory. Form names and the selected device or session
are kept with those drafts. Navigation sends no form data to the server and
adds no indexing, logging or browser storage. Names are hidden while access is
locked. Changing credentials clears the drafts through the existing private
state hooks. Rechecking the same token preserves remaining drafts.
Authentication and dashboard access rules remain in the existing paths. No
migration or backfill is needed.

The amount of notice content is bounded by the finite set of dashboard forms.
Keep one action per draft and update existing entries instead of adding
duplicates or new polling. Long labels must wrap without growing the page
sideways.

Rollback restores the previous notice code through the normal release process.
It does not require changing saved settings. Refreshing a page can lose its
page-local edits, so let the user save or discard those edits before a release
reload. Coordinate any Pi acceptance with the release owner and use the safe
management connection. Implementation, remote publication and deployment are
separate steps requiring their applicable authorisation.

## Implementation and local verification: 4 October 2026

Implemented the named review actions for all 24 settings forms. The buttons
retain their identity during background updates. Opening a draft reveals the
existing editor and moves focus to a visible editable control. Device controls
can reopen after being closed without selecting another device. Test notes
stay associated with their current session. Names are rendered as text and
long names are shortened and wrapped.

Local review checked the complete change for edit loss, unexpected writes,
private-name exposure, focus loss and repeated work. Notice updates use one
pass over the existing buttons and tracked forms. No new polling or external
dependency was added. Existing response ownership checks remain intact.

- 262 relevant Python tests passed. These cover drafts, navigation, packaged
  source, phone layout, credentials, neutral sign-in and refresh after saves.
- JavaScript syntax checking, the repository's selected Python lint rules and
  the whitespace check passed. The focused design detector reported no issues.
- The browser navigation journey passed against the final installed package.
  It covers one and multiple drafts, focus during normal polling, collapsed
  cards, a failed save, saving one draft, closed device controls, test notes,
  a missing form, discard confirmation and credential changes. It checks that
  reviewing a form sends no save or Apply request.
- The existing browser save and Apply journey passed against the final
  installed package. The existing Apply-help and phone-layout journeys also
  passed before the final credential-state refinement.
- A fresh package build and installation passed without network access using
  cached pinned build dependencies. The installed package contains the
  dashboard assets and retains the computed content policy.
- Desktop and 320-pixel screenshots were inspected. Longer labels fit at
  320 pixels. No physical Pi, deployment, container build, remote publication
  or hosted CI was performed for this isolated change.

Screenshots and the final source receipt are retained under `test-results` in
the isolated worktree. The complete configured release checks remain the
release owner's responsibility after composing this change with other work.

# Preserve service-list focus during status refresh

## Human review summary

The Schedules page reads service lists every five seconds to show current random outages. Each successful read rebuilds the service catalogue checkboxes even when nothing changed. The selected values survive but the focused checkbox disappears and keyboard focus falls back to the page body.

Keep the existing polling interval and requests. Update catalogue controls by their service key so surviving checkboxes retain their identity and current checked state. Update names, domain hints and order when the response changes. If the focused service disappears, move focus to the nearest remaining checkbox or the Extra hostnames field when none remain. Never move focus when it belongs outside the changed catalogue. Keep refreshing saved-list outage status independently.

The main risk is preserving stale catalogue data or accidentally changing an unsaved choice while fixing focus. Browser checks must cover both unchanged responses and real catalogue changes through the normal timer. This is a small dashboard reliability change with no API, data migration or runtime dependency change. The 3 October approval allows the GUI plan to proceed without another permission checkpoint. Independent review precedes implementation. The proposed fallback on removal is the main behavior to review.

## Current evidence

Read from commit `9312bb381d76f8a0aff45d311f5240337dfa8886` on `codex/gui-focus-refresh`:

- `src/jittersim/api/server.py:5796`: `renderServiceCatalogue` collects checked keys then calls `replaceChildren` and creates new inputs. Retaining values does not retain the focused DOM element.
- `src/jittersim/api/server.py:5818`: the observed-name select also rebuilds its options on every service-list read. Its selected value is restored only while the name still exists.
- `src/jittersim/api/server.py:5836`: saved-list rows are rebuilt separately. Their detail includes `outage_now`. This renderer must still run when only outage status changes.
- `src/jittersim/api/server.py:5892`: explicit Edit loads the saved choices into the form. A status read must not imitate this action.
- `src/jittersim/api/server.py:6225`: `loadServiceLists` replaces catalogue data then invokes both catalogue and saved-list rendering. It refreshes observed names after success or failure.
- `src/jittersim/api/server.py:9843` and `:10049`: Schedules polling includes service lists and runs every five seconds. Hidden pages, missing authentication and concurrent dashboard refreshes are already guarded.
- `src/jittersim/api/server.py:7711`: only explicit form submission sends the service-list write. Preserve this behavior and its existing handlers.
- `src/jittersim/api/server.py:10729` and `:11375`: the response contains catalogue keys, names and ordered domains alongside saved-list outage state. Catalogue identity alone is insufficient to decide whether display content changed.

The coordinator supplied a Chromium reproduction: unfinished ID, name and hostnames plus a checked focused catalogue checkbox survive in value after 6.2 seconds but focus becomes BODY. This planning task inspected the owning code and did not rerun that reproduction.

## Proposed change

1. Keep the fieldset and its legend. Match existing catalogue labels and inputs by service key. Retain surviving input nodes and their checked values. Create unchecked controls for newly added services and remove controls absent from the latest successful response.
2. Update visible names and domain hints without inserting response text as HTML. Apply the response's order. Avoid touching nodes when their position and displayed content are already correct. A comparison must include keys, names, ordered domains and catalogue order rather than keys alone.
3. Record focus only when the active element is a catalogue input. Preserve the same focused input when its key survives, including rename, domain change or reorder. Reordering may affect focus in browsers even when nodes survive, so verify the active element after rendering and restore only that previously focused surviving control with no scroll jump.
4. If that active key was removed, focus the control now occupying its previous position or the final remaining control when the old position no longer exists. If no services remain, focus Extra hostnames. Removed checked services are removed from the current form selection rather than submitted as invisible stale keys. Do not restore focus after an asynchronous operation when the person has already moved to another field.
5. Leave ID, name, profile, Enabled and hostnames unchanged during reads. Preserve the observed-name select and unchanged option nodes too. On changed observations retain the selected name if it survives and fall back to the empty choice if it disappears. Keep focus on that select and do not add names to hostnames automatically.
6. Continue `renderServiceLists` on every successful response so an unchanged catalogue cannot suppress outage updates or saved-list changes. Do not change Edit, Save, Delete or confirmation handlers. Focus on saved-list action buttons is an adjacent issue caused by their separate row replacement. Keep it outside this form-focused fix and report it separately if a regression check exposes it.
7. Keep only current catalogue/option nodes and current response data. Use work proportional to the response size with no history cache, added retries, timers or requests. Read failures retain the last successfully displayed catalogue and editable state while existing error reporting continues.

An unchanged-catalogue shortcut alone fixes the reproduced case with fewer changes but would still lose focus when a legitimate catalogue change arrives. Full reconstruction followed by focus restoration is smaller than retaining nodes but discards native control identity every poll. The proposed limited node retention covers changed responses without adding a general dashboard rendering framework.

## Browser verification

Add a focused check to the existing Chromium journey after review and authorization. Use the real fixture, normal authentication and the actual Schedules page. Open Service lists through the page controls. Observe completed service-list GET responses rather than manually calling rendering functions or shortening the production timer.

- Fill unfinished ID, name and hostnames. Select a profile and Enabled state. Check a service using the keyboard and leave its input focused. Wait for at least one subsequent natural five-second poll with a bounded 12-second wait. Assert the same input remains active and connected, values and choices remain intact and Space still toggles it. Repeat across a second poll to detect accumulated duplicate controls.
- While natural polling continues, leave focus in a text field and in the observed-name select. Assert the text field's caret/selection and values survive. Assert the select's chosen value and focus survive an unchanged observation response.
- Intercept only the fixture's service-list GET response in the test to supply a bounded changed catalogue while allowing all other requests normally. Cover addition, removal of a different service, rename, domain change and reorder. Assert current labels, hints and order plus surviving selections and focus. Retain realistic saved-list response fields. Do not alter production catalogue data or add a fixture write endpoint.
- Remove the focused service and verify the agreed neighboring-control fallback. Remove all services and verify Extra hostnames receives focus. Verify removed services are absent and new services start unchecked. If focus moved outside the catalogue before the response completed, it must stay there.
- Change only `outage_now` for a saved synthetic list in a GET response. Confirm the visible outage detail updates during a natural poll while unfinished form contents and checkbox focus remain intact.
- Supply one failed read and then a successful read. Assert editable state remains intact and subsequent polling recovers. Observe service-list requests and assert no POST or delete occurs during these read-only cases. Check for page errors and duplicate catalogue keys.

Run the central unchanged-poll and removal cases at 375px and desktop width. Bound each wait and the journey as a whole. Use synthetic credentials and names only. Keep diagnostic screenshots or trace output within the existing test-results retention rather than collecting real user form contents. Browser focus behavior is the decisive check. Source-string assertions or tests that merely repeat DOM construction are insufficient.

## Build, CI and recovery

- `tests/browser/journeys.py:205` starts the synthetic fixture with a seeded repository and scheduler. Its existing final cleanup stops the fixture and browser. Reuse that lifecycle rather than starting a second dashboard server for each case.
- `.github/workflows/tests.yml:31` runs Browser journeys on Python 3.13 with an installed package and Playwright 1.63.0. Put the regression in that existing command so it cannot be skipped by CI wiring. Keep the job's ten-minute limit.
- Verify the changed installed package is served rather than a stale wheel or source checkout. Use a newly built wheel from the reviewed commit in an isolated environment and confirm the imported server identity. Run `python -m tests.browser.journeys` against that install, then the existing phone checks if rendering changes affect layout.
- `tests/browser/Dockerfile:1` installs the dashboard into the clean browser image and copies tests separately. Run the existing fresh container path when the coordinator's Docker slot is available. Cached verification is supplementary evidence and must retain its installed-file identity checks. This planning task does not run Docker or change build configuration.
- Run existing Python tests and the repository's required Ruff checks. No Node build, runtime CDN, paid API, dependency installation in this planning task or deployment is required.
- No migration or backfill is needed. The Pi serves the packaged dashboard as before. After release use a reload to obtain the changed document. Roll back by restoring the prior dashboard package and reloading. Poll interval, authentication, request limits and write behavior remain unchanged.
- Before implementation recheck the owning blocks against any concurrently merged work. Keep this fix separate from confirmation-handler work and avoid touching that owner's files. Record test evidence and perform a complete review before publishing.

## Review decision

Review the removed-service focus fallback and whether preserving observed-name option nodes belongs in this narrow change. Proposed answer: include it because the same loader touches this editable select every poll. Leave saved-list action-row focus for a separate explicit decision. Implement after independent review under the existing approval to proceed with the GUI plan.

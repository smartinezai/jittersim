# Combine the router Wi-Fi copy buttons

Date: 3 October 2026. Status: implemented and verified locally.

## Human review summary

Replace the two sidebar clipboard buttons with **Copy router Wi-Fi to Pi**. The owner confirmed that this should perform the same action as the existing button on the Router and Wi-Fi page. It applies the router name, password and security together to the Pi access point.

Move the existing combined action into the sidebar and remove the separate clipboard buttons. Keep one visible combined action, its security preview and its confirmation before Wi-Fi restarts. Move its progress and result message too so they remain visible from every page. Keep the Router and Wi-Fi page's explanation and manual recovery controls.

The main risks are disconnecting test clients and hiding progress when the Router page is closed. Reuse the existing operation and keep the password out of the browser response and status text. Avoid new dependencies, endpoints or storage. Changing the sidebar also needs keyboard and phone checks.

**Decision confirmed:** apply both settings to the Pi through the existing combined action. On 3 October the owner approved proceeding with agreed plans without repeated confirmation for the next 24 hours. The owner then extended that approval to pushing and merging work that is implemented, reviewed and tested locally with green CI. No further approval question is needed within that scope. Live Pi deployment was not included in this permission.

The latest temporary permission was recorded at 00:54 Berlin time on 3 October and ends at approximately 00:54 on 4 October. It applies to the current requested work and does not assign unrelated projects.

**Risk:** medium for changing the place of a network-changing action. No pull request risk template was found in this checkout. Recheck the selected implementation base for one before opening a pull request.

## Current evidence

- Read local branch `codex/master-continuation` at `c922427`. This establishes local behavior only. The installed Pi version was not checked.
- Implementation uses the isolated `codex/combined-router-wifi` branch from the newer local commit `2fbaff5`. Unrelated working files are preserved.
- `src/jittersim/api/server.py` contains `copy-wifi-name` and `copy-wifi-password` in the sidebar. Their handlers use `/api/router/wifi-credentials` and the existing clipboard helper.
- The existing `sync-router-ap` action checks `/api/router/preview-ap` then confirms before `/api/router/sync-ap`. It carries the preview fingerprint and any required legacy-security confirmation.
- Router actions share busy and password-rejection checks. `routerRequest` checks for credential changes and stops waiting after a bounded deadline.
- Existing checks cover clipboard restrictions and secret retention, combined Pi configuration, security matching, failed configuration restoration and credential state.

## Proposed work

1. Have this plan independently reviewed and obtain human feedback on the summary and review before implementation. The destination is confirmed as the Pi access point.
2. Recheck the selected branch and overlapping dashboard work. Use a separate `codex/` branch for code changes. Preserve unrelated untracked files.
3. Move the existing `sync-router-ap` button under the router switch and retain its ID. Remove the old clipboard buttons and their frontend-only state and handlers after checking their uses. The browser clipboard helper is currently used only by those buttons and can also be removed. Keep the existing combined configuration operation and its preview, confirmation, busy state and credential checks. Move the shared `router-action-message` status into the sidebar so progress, cancellation and errors remain visible while the Router page is hidden. Keep accessible live feedback.
4. Update help text, README instructions and Raspberry Pi deployment instructions. Replace the sidebar's two-column copy-button layout with a layout suited to one button. Update existing source-based checks and button lists that reference the removed controls. Do not remove the credentials API or helper permission as part of this UI change.
5. Run focused checks and review the complete change before proposing a pull request. Within the temporary permission, publishing and merging do not need repeated confirmation. Verify the final branch against its current target and require green CI before merging. Failed checks or jobs that cannot start must not be bypassed. Live deployment remains a separate action.

## Data, safety and limits

- Require the existing dashboard authentication and device access checks. Preserve router password validation and rejection behavior.
- Pi configuration reads the router settings and writes the Pi access point. It must preserve security matching and restoration on failure. Keep the warning about client disconnection and the need for a separate management connection. It must not turn router Wi-Fi off as part of copying.
- The combined action must not fetch the Wi-Fi password into the browser, copy it to the clipboard or store it in operation results. Preserve the existing response filtering. Keep credentials out of status text and logs. Leave the separate credentials API's password-retention protections intact.
- Use the existing combined operation with its current limits. Keep repeated clicks blocked while work is pending and do not add automatic retries of a network change.
- Preserve existing bounded router waiting and helper behavior. The change adds no background polling, queues, database work, external service calls or new build tools.

## Verification and release

- Adapt `tests/unit/test_dashboard_router_copy.py` and `tests/browser/router_copy.py` for the sidebar combined action. Remove checks specific to deleted clipboard UI code. Run the relevant credentials and combined-configuration cases in `tests/unit/test_api.py` to check authentication, input validation and secret filtering. Check `tests/browser/apply_clarity.py`, `tests/unit/test_dashboard_apply_summary.py` and `tests/unit/dashboard_ids.txt` for references to the removed IDs.
- Reuse `tests/unit/test_floating_credentials.py`, `tests/unit/test_wifi_security_copy.py` and `tests/browser/wifi_recovery.py`. Update the floating-credentials harness because it currently uses the clipboard helper as a script boundary. Check cancellation makes no configuration request, confirmation uses the preview fingerprint, wrong passwords prevent repeated attempts and success applies the name, password and security together. Keep the Wi-Fi password out of preview and status text. Check no credentials-read or clipboard operation occurs when the new button is used.
- Check the one visible sidebar button with keyboard input and at 375 px and desktop widths. Focus should return to a usable control after credential entry or a cancelled confirmation. Success must only be shown after the selected action finishes.
- Run the relevant Python checks and browser journeys then the existing lint check. `.github/workflows/wifi-recovery.yml` provides the full Python, clean Linux container and browser jobs. If shared build configuration changes, verify a clean container build. No such change is expected.
- No migration, backfill, new package or deployment command is needed for this frontend change. Verify the packaged dashboard with the existing release checks. Roll back by restoring the prior application release. Restoring a release does not undo Pi Wi-Fi settings already applied, so keep the existing manual recovery route available.
- A live Pi copy, if later authorized, must record the prior settings privately and confirm both settings with a test client through a separate management connection. Fixture checks do not establish physical reconnection.

## Review and learning checkpoint

The central design question is how moving a control affects busy state, focus and visible progress while reusing a proven operation. Updating labels and test selectors is routine work. Before implementation, the human reviewer should consider the independent review and provide feedback. During implementation, the owner should be able to explain what happens when configuration fails and why timeout does not prove that the operation stopped.

The separate plan review on 3 October found no planning blocker beyond destination clarification and human feedback. The owner then confirmed the Pi action and authorized proceeding. Review refinements are included above: keep passwords out of cached operation results, keep Pi progress visible in the sidebar and update the single-button layout and affected inventories.

## Local result

The sidebar now contains one **Copy router Wi-Fi to Pi** button and the action's progress message. The old clipboard controls and their unused frontend code are removed. The existing preview, confirmation, combined configuration and recovery behavior are retained. The password prompt clears after a password edit while active operation feedback remains visible.

The full Python suite and the existing CI lint rules passed. Browser checks for router copying, Wi-Fi recovery and action reachability passed. The final status correction then passed the focused credential checks and a fresh router-copy browser run at 1920 px and 375 px. Desktop and phone screenshots were inspected. Cancellation, a pending request, failed login, a missing helper and secret handling were checked with the disposable fixture.

The final separate review found no blocking issue. There are no new build inputs or deployment changes. No remote push or live Pi change was performed. Physical reconnection after an installed update remains a release check.

## Publication gate

Fresh GitHub checks on 3 October show recent CI jobs failed before any steps ran. The checked Linux job reports that recent account payments failed or the spending limit needs to be increased. This does not provide a test result. The green-CI condition is therefore still unmet. No merge was performed and no branch protection or CI check was bypassed. Before publication, prepare a focused branch against the current agreed target so unrelated continuation commits are not included.

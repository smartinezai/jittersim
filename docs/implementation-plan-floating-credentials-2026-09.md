# Floating credential controls

## Human review summary

Let the dashboard owner enter the admin token or router password from any
scroll position. Add two labeled buttons on the right that open a compact
credential panel. Move the buttons to the bottom on narrow screens. Keep the
current dark appearance and token retrieval help.

Use one field for each credential throughout the page. The main risks are
covering dashboard controls, showing stale token status and losing a newly
entered password when an earlier router request finishes. Credentials stay
masked and remain in this page only. No database change is needed.

Proposed default: one panel containing both fields. Each floating button opens
it and focuses its matching field. Confirm this direction before implementation.

## Outcome and scope

- The owner can enter either credential without scrolling away from their task.
- Buttons show useful states such as token needed, checking, accepted or rejected.
  The router button distinguishes password entered from password verified. Entry
  alone cannot prove the router accepted it.
- Reuse the existing token check and router actions. Opening or closing the panel
  does not submit a request or change the network.
- Keep the existing router actions beside the upstream connection details. When
  an action needs a missing credential, open the matching field and focus it.
- Replace the distant duplicate entry points with controls that open this panel.
  Preserve the token command help beside its field.

## Interaction and layout

- Keep both button labels visible. Match existing buttons and focus indicators.
- Place the buttons beside the content where there is room. At intermediate and
  narrow widths use a bottom toolbar with enough reserved space for page content.
- Keep the panel inside the viewport. Long token help must wrap and the panel
  must remain usable with zoom, a short viewport or the phone keyboard open.
- Use a non-modal panel so the page remains usable. Associate each shortcut
  with the panel and its expanded state. Closed contents leave keyboard navigation.
  Provide a labeled close button and Escape dismissal. Return focus to the opener
  without moving page scroll. Opening the panel preserves unfinished dashboard
  forms and selections. Provide token commands as accessible text rather than
  relying only on CSS-generated tooltip content.
- Closing the panel preserves entered values. Explicit clearing and page reload
  remove credentials. A completed router operation clears the password it used.
- A token edit stops private polling and invalidates its accepted status. Older
  checks and private-data responses must not validate a newer value or repopulate
  cleared views. Cover edits during verification, refresh and periodic polling.
- Allow only one router operation at a time across all router action buttons. An
  earlier operation must not erase a replacement password entered while it was
  running, even when the replacement text is identical. Report the result of the
  operation without implying that the current password field was verified.
- Communicate states with text. Errors remain visible and retry controls recover
  after failure. Keep status messages visible while the panel is open.

## Work and review

1. Review this plan and collect the owner's feedback before changing production
   code, as required by AGENTS.md.
2. Update the embedded dashboard markup, styles and credential interactions in
   `src/jittersim/api/server.py`. Keep one authoritative field for each secret.
3. Cover meaningful state changes in the existing served-JavaScript checks.
   Exercise in-flight edits, failure recovery and clearing. Avoid tests that only
   compare copied labels or repeat simple event wiring.
4. Inspect desktop, intermediate width and mobile together. Check keyboard access,
   screen-reader labels, Escape, focus return, scrolling, zoom and overlap with
   the sticky network controls, feedback messages and the on-screen keyboard.
5. Review the full change. Run the relevant suite, focused lint and JavaScript
   syntax checks before the previously authorized push and Pi deployment.

## Delivery and recovery

This is an interface and credential-state change. Treat it as security-sensitive
because it handles secrets, although endpoint permissions remain the same.
No values may reach browser storage, URLs, logs, screenshots or feedback text.
Retain the current per-request router password handling and existing API limits.

Build and install the exact tested commit using the current release process.
Verify the served controls and authenticated behavior on the Pi. The existing
Pi sudo password step still applies. Rollback is a code revert and redeployment
with a browser reload. Neither deployment nor rollback changes saved settings.

## Open question

Is the shared panel with two floating shortcuts the desired layout? The proposed
default avoids extra credential copies and keeps the page visible while editing.

## Plan review

The review confirmed the shared-panel direction. Its requirements for stale
private responses, concurrent router actions, same-value password replacement
and accessible non-modal behavior are included above. The owner approved the shared panel on 30 September 2026 with
“Shared panel (Recommended)”.

## Validation

The served panel was inspected at desktop, intermediate and phone widths plus a short phone viewport. Credential entry, accepted status, help wrapping, deep-scroll shortcut access, close preservation and Escape focus return passed. The short viewport check simulates reduced space rather than a real phone keyboard.

The intermediate check found an existing device selector that extended beyond its grid cell. Constrain selectors to their available width. This correction is covered by the browser layout check. Credentials remain page-only and the protocol/state tests passed on Mac and a fresh Linux installation.

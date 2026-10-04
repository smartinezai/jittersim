# Router Wi-Fi switch and dashboard buttons

## Human review summary

Replace the separate router Wi-Fi on and off buttons with one switch in the
floating Quick actions panel on the right. Show the router's confirmed state
and refresh it while the dashboard is visible. Make the shared dashboard
buttons calmer and more consistent using the Impeccable design guidance.

The switch must distinguish a confirmed state from an action still running or
a router that cannot be reached. Checking status must work when Wi-Fi is off.
Routine status checks must not freeze the dashboard or flood the router with
sign-in requests. Turning Wi-Fi off keeps the existing connection warning
and confirmation because it can disconnect household devices.

Risk is high for the router action because it changes network access. Styling
alone has low risk. The main open decisions are the refresh interval and how
the floating panel fits smaller laptop screens. Recommended defaults are a
15-second refresh with shared request limits and a compact right-hand panel
on desktop. Narrow phone screens use a compact bottom panel.

The owner approved implementation, testing, publication and Pi deployment
on 1 October 2026 after the plan review. The default refresh and layout choices
above apply. Existing script callers keep their synchronous response shape.
The dashboard explicitly requests background operation responses.

## Intended outcome

- One labelled Router Wi-Fi switch is visible in Quick actions while scrolling.
- The switch follows verified router state rather than the last button clicked.
- On and Off states are readable without relying on color alone.
- Checking, Changing and Status unavailable explain intermediate states.
- Shared buttons have consistent size, weight, spacing and clear focus states.
- The existing copy, credential, panic and Wi-Fi name reset actions remain usable.

The central learning objective is separating requested state from confirmed
hardware state while managing delayed or failed replies. Visual spacing and
routine packaging are supporting work.

## Evidence from the current checkout

The inspected branch is `feat/service-profiles-and-pi-hardening` at `9fd7fa7`.
The existing untracked unaffected-devices plan belongs to separate work.

- `src/jittersim/api/server.py` contains the two separate buttons and their
  listeners. The floating panel already contains credentials, panic and the
  Wi-Fi name reset. At widths below 1500 pixels it moves to the bottom.
- The browser prevents overlapping local router actions and tracks credential
  edits. Successful writes return confirmed `enabled` state. There is no
  read-only router status route or recurring router state check.
- `src/jittersim/networking/router.py` already reads the master state from the
  router page when it is absent from the encrypted response. Its Wi-Fi import
  reader also requires usable Wi-Fi credentials. That reader can fail when
  both bands are off so it cannot be the switch's status reader unchanged.
- Router writes preserve settings and already have bounded confirmation after
  restarts. A change can take up to 120 seconds with a 150-second helper wait.
- `ControlServer` serves requests on one thread. A status check that waits for
  a slow router would delay other dashboard requests. Adding general request
  threads would also affect the settings database's thread ownership.
- The helper has bounded request capacity and operation locks. Router copy
  and router writes currently pass through different lock paths. A status
  reader must not open a competing router session during either operation.
- `.github/workflows/wifi-recovery.yml` runs Python checks, a clean Linux
  package build and served-dashboard browser journeys. No pull request risk
  template, PRODUCT.md or DESIGN.md was found in this checkout.

## Proposed behavior and design

### Floating controls and button styling

Use the existing dark dashboard as the visual basis. Treat this as a focused
refinement of shared buttons and Quick actions rather than a replacement of
the whole dashboard layout. Keep the copy action with the upstream router
details and remove both old on/off controls from that section.

Place the switch in its own labelled row within Quick actions with a short
state message. Keep the panel on the right at normal desktop and laptop
widths by reserving content space. Use the available height to avoid covering
content or losing controls below the screen. On narrow screens collapse to a
compact bottom presentation rather than the current tall stack. Check short
screens, browser zoom, the software keyboard and safe-area spacing.

Reduce heavy borders and excessive bold text. Use one clear primary button
style, quieter secondary buttons and a distinct danger style. Keep useful
touch targets even when buttons look smaller. Provide visible focus, hover,
pressed, disabled and working states. Avoid translating every button on hover.
Honor reduced motion. No new font service or frontend framework is needed.

Use accessible switch behavior with a stable label and keyboard operation.
Only a known state can supply its checked value. Unknown status displays a
separate explanation and disables the switch rather than implying Off.
Explain missing credentials near the switch with access to the existing
credential panel. Do not introduce another password field or automatic
credential persistence.

### Read-only status and updates

Add a dedicated router state read that uses the existing verified master and
radio parsing. Do not require a Wi-Fi password, importable security mode or
active band to determine whether Wi-Fi is on. Define On as verified effective
activity on at least one supported primary band and Off as verified absence
of primary activity. Unknown or conflicting readings remain unavailable.
Keep existing guest handling and split-band write restrictions. Do not make
the switch claim it measures guest activity independently.

Add an administrator-protected status route and helper action. Pass the router
password in the request body over the existing protected transport. Return
only state, freshness and fixed safe status messages. Reuse the current
gateway discovery and router adapter. Never accept an arbitrary router URL.

Keep router reads and actions off the dashboard request thread through one
bounded background operation for the configured router. This includes Wi-Fi
changes, password checks, previews and copies because their existing waits
would otherwise prevent other tabs from seeing Changing. Return an accepted
operation identifier promptly and let protected status requests report its
progress or completed outcome. Adapt the browser to wait for that outcome
before displaying success. Keep the current confirmation and write behavior.

Retain at most one active operation and one completed outcome with a short
expiry. Reject overlapping work rather than queueing it. The worker must not
access the settings database. Any saved action record stays on the dashboard's
own thread and is recorded once. Retain a password only for the current
operation then release it. Use one total short deadline covering discovery,
sign-in, read and sign-out for status reads. Keep the existing longer allowance
for changes and copies.

Permit at most one router session across status, copy, password checks and
writes. Concurrent requests receive the existing busy behavior rather than
queueing. Share the refresh limit across browser tabs so more tabs do not
multiply router sign-ins. Bind snapshots to the current credential context
and discard results from superseded credentials or operations. Use a private
process key to identify the combination of dashboard administrator, router
gateway and supplied password without saving the password or a plain password
hash. Give each request an opaque identifier that is returned with its result.
The browser's revision counter only rejects replies within that browser. It
cannot identify credentials across tabs.

A shared successful result can mark credentials accepted only when it belongs
to the requesting context. A different password must be checked independently
and must not inherit another tab's accepted state. Keep only the active context
and latest completed result rather than accumulating a map of passwords.
Do not let an older successful read clear a newer authentication failure.

Start with 15-second refreshes while visible and authenticated. Read promptly
after credentials are checked and when returning to the tab. Stop after
credential rejection, removal, expiry or loss of dashboard access. Back off
after connection failures with a bounded retry rate. Show the last checked
time and mark state unavailable when it passes the agreed freshness limit.

A click requests the opposite of the last confirmed state. Turning off keeps
the existing explicit confirmation. Show Changing and prevent another click
until the action settles. Keep the confirmed value until read-back succeeds.
After an uncertain write result, invalidate earlier status and read again
before allowing another change. Never automatically repeat a write.

## Manageable implementation steps

1. Review this plan and resolve the refresh and layout choices. Inspect the
   current base again before starting because other router work may overlap.
2. Add and verify the read-only state path including the all-bands-off case.
3. Add bounded background router operations, status refresh and shared session
   protection. Update router action responses and browser waiting behavior
   together. Prove dashboard responsiveness before automatic browser refreshes.
4. Replace the two buttons with the floating switch and integrate credential,
   visibility, freshness and pending-action behavior.
5. Apply the shared button and panel refinement. Read Impeccable's craft-floor
   reference immediately before UI edits. Inspect desktop and mobile together
   then address the findings in one batch with one confirmation pass.
6. Complete the full change review and appropriate checks. Prepare focused
   local commits. Publish and deploy only when separately authorized.

## Verification

- Router fixtures cover master state present or read from the page, both bands
  off, one band active, unknown flags, conflicting readings, old firmware,
  rejected credentials and unreachable devices. A status read makes no Wi-Fi
  settings write and does not expose Wi-Fi credentials.
- Helper and route checks cover authentication, cross-site request rejection,
  fixed errors, short total deadlines, busy replies, shared session protection
  and old-helper compatibility. Logs and response bodies exclude secrets.
- Delayed-router checks prove status refresh and slow Wi-Fi changes do not
  block health or other dashboard reads. A second tab receives Changing while
  the first tab's write is running. Several tabs cannot create extra workers
  or an unbounded queue. Check cleanup and deadline behavior on shutdown.
- Two tabs with different passwords cannot share credential acceptance. Check
  replies after password replacement, old operation results, result expiry
  and repeated result reads without duplicate saved action records.
- Browser journeys cover initial On and Off, unavailable state, cancelled
  disable, slow change, confirmed success, lost reply, external router changes,
  hidden tabs, return to tab, password edits and remembered credential expiry.
  Confirm the Pi Wi-Fi copy, panic and reset controls still work.
- Inspect the served dashboard at phone, laptop and desktop widths including
  widths near panel layout changes, a short viewport and zoom. Check keyboard
  focus, switch announcements, readable contrast and no covered controls.
- Run the full Python suite, existing focused lint and served JavaScript syntax
  check. Use the current CI Linux container and browser jobs for release
  verification. Do not add unit tests that only repeat simple button styling.

## Release, privacy and recovery

This is a single-router lab app with no new tenant or user data. Status is
temporary process memory. There are no database migrations or backfills and
no new credentials saved in settings, logs or browser storage. Preserve the
existing dashboard token, saved-device access and helper socket restrictions.

Package the new helper and dashboard together with the existing installer.
An older helper must produce a clear unavailable status rather than a guessed
Off state. Use the existing CI checks and release backup process. Do not alter
shared build settings for this UI work.

Before any separately authorized hardware acceptance, verify the installed
source and working management connection. Confirm live status without changing
Wi-Fi first. Actual off/on acceptance requires a working wired connection and
independent restoration. Local fixtures cannot prove physical radio state.

Rollback restores the previous release and service configuration using the
existing backups. Code rollback cannot restore router Wi-Fi state. Keep the
router's wired local controls available for recovery after a failed change.

## Review status

The plan review identified two material gaps: synchronous router actions would
still block other tabs and browser-local password revisions cannot separate
credential contexts across tabs. The design above now covers background router
actions and explicit result ownership. The focused follow-up confirmed both
gaps are resolved with no remaining plan blocker. The owner then authorized implementation, tests, push and deployment.

## Verification result

All 478 tests pass on macOS and in a clean Linux package image. Focused lint,
served JavaScript syntax and whitespace checks pass. Served browser journeys
cover confirmed off/on, cancelled disable, a delayed change, working health
reads, saved credentials and desktop, laptop and phone layouts. Existing saved
device access and Wi-Fi recovery journeys pass.

Linux network smoke, device protection packets, dashboard access packets,
requesting-device identity, management name isolation and fresh installer
permissions checks pass. Network checks run with only the container's network
capabilities rather than privileged access. The final source review found no
remaining blocker. Physical router off/on acceptance still requires the wired
management and restoration conditions described above.

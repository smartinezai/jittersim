# Dashboard overhaul plan (2026-10-02)

Inputs: `ux-audit.md` (9 pages, 234 controls, 15 ranked problems), 47 screenshots, PR 53 (phone layout, read, not edited), and a read of `src/jittersim/api/server.py` for pollers and endpoints. Clickable prototype: `prototype.html` (same folder).

What I did not verify: the click counts marked "est." in the audit, and real latency of the existing buttons. New-step counts below are design targets, not measurements. Measure them in the Phase 3 browser tests.

## 1. Principles

1. Task first. Navigation is built from what the owner wants to do (make this phone slow, stop everything, check it works), not from the code's feature list.
2. One primary action per screen. Now has Start/Stop. A device drawer has the profile picker. A profile editor has the sliders. Everything else is smaller and quieter.
3. Changes apply at once, with Undo. No hidden Apply steps. A toast says what changed and offers Undo for 6 seconds. Only two things ask first: deleting a saved item, and anything that can lock the owner out (router Wi-Fi off while the Pi has no cable, removing the last allowed device). These show one plain sentence and a confirm button.
4. One name per action. Start, Stop, Panic, Apply to (profile), Protect, Block. The words Unaffected, Unaffected by profiles, Make unaffected, Remove protection, Re-apply saved settings and Reset network disappear (see section 6, glossary).
5. Advanced options are folded. IDs are generated from names. Raw JSON, "compiled plan", reserved IPv4, configuration version live behind "Details" in More.
6. State is pushed, not polled in five places. One state stream drives the whole page. The page redraws only what changed and never steals focus.
7. No product name before sign-in. Title "Sign in", heading "Sign in", one step that also registers the browser.
8. Dark only. No theme switch.

## 2. New information architecture

Top bar on every page: status pill (Running with profile name, Stopped, or Stopped by Panic), router Wi-Fi switch, Panic, and a Search (Cmd-K) button on desktop. Nothing else is persistent. The sidebar status box, tiles, Re-apply and Reset network are removed.

| Place | Job | Contains |
|---|---|---|
| Now | See and control what is happening | Start/Stop, profile switcher (the general profile), affected device cards with live sparkline, first-run checklist, Panic banner with Start |
| Devices | Work with one device | Search and filter, one-line rows, "Everyone" strip, device drawer (Overview, Traffic, History) |
| Profiles | Define conditions | Preset cards with plain names, editor with sliders and live preview, tabs Settings, Services, Schedule |
| Network | The setup around the test | Router Wi-Fi, Pi Wi-Fi name, access rules (known devices only, MAC list), DNS failures, recovery |
| More | Rare things | Updates, Diagnostics, Activity, Settings |

Phone: the same five places become a bottom tab bar (Now, Devices, Profiles, Network, More). The top bar stays (compact: logo mark, status pill, router switch, Panic). The device drawer becomes a bottom sheet. This replaces PR 53's bottom Panic/router bar and its controls sheet. Keep PR 53's 44 px touch targets, safe-area padding and `phone.css` rules.

### Where every existing page and control goes

| Today | New home | Notes |
|---|---|---|
| Sidebar: nav (9 items) | Rail (desktop) / tab bar (phone), 5 items | |
| Sidebar: status box | Top bar status pill | Removes the third copy of every message |
| Sidebar: general profile select | Now: profile switcher | Same data (`/api/general-profile`) |
| Sidebar: Start / "Start with saved settings" | Now: Start (one name) | After Panic the banner also has Start |
| Sidebar: Panic | Top bar: Panic | Single click, no confirm, Undo toast restores |
| Sidebar: Reset network | Removed as a button. Command palette "Reset network rules" under More > Diagnostics > Details | It overlaps Panic. Keep the endpoint (`/api/reset`) |
| Sidebar: Re-apply saved settings | Removed. Start does this | |
| Sidebar: router Wi-Fi switch | Top bar: router switch. If no password saved, clicking it opens a small password prompt (not a dead disabled switch) | |
| Sidebar: copy buttons / Router password tile | Network > Router Wi-Fi card | |
| Sidebar: 4 tiles | Removed (all repeat page content) | Updates badge becomes a dot on More |
| Sidebar: toast copy + #message line | One toast only | |
| Devices: row Apply profile / Assign profile (x6) | Device drawer > Overview > Profile select (applies on change) | "Same as everyone" is the first option, so the general-vs-device conflict has one sentence |
| Devices: Apply to all | Devices: "Everyone" strip at top, and Now profile switcher | |
| Devices: "Saved device rules paused" | Drawer sentence: "Follows Busy café Wi-Fi from Everyone" | |
| Devices: Edit name and type fold | Drawer header: tap the name to rename | |
| Devices: Add to group / Create group hint | Drawer > Overview > Group (folded) ; group management stays on Profiles > Groups (More list) | Disabled-in-5-of-6 problem goes away: show only when groups exist |
| Devices: Make unaffected / Remove protection | Drawer: Protect switch ("Protected from all profiles") | Protected rows show no profile controls |
| Devices: Open device controls (dead) | Row click opens drawer | |
| Profiles: saved profile list + Apply to all | Profiles: preset cards; "Use for everyone" on card menu | |
| Profiles: 19-field form + 16 help icons | Profiles: editor, 4 sliders up front; rest under "More options" (loss burst, reorder, duplicate, outages) with the help text as inline hint | IDs generated |
| Profiles: Assign a device form (Device ID, IPv4, Profile ID) | Removed. Drawer profile select replaces it. Offline devices: Devices > "Add a device by address" (folded) | `/api/devices` kept |
| Profiles: Group devices | Devices > filter "Groups", or More > Groups | |
| Profiles: random website access | Profiles editor > Services tab | |
| Schedules and limits: Schedules | Profile editor > Schedule tab (per profile) and Now "Next up" line | `/api/schedules` |
| Schedules and limits: Timers | Now: "Stop in 30 min" menu on Start/Stop button | `/api/timers` |
| Schedules and limits: Scenarios, Progressions | Profile editor > Schedule tab, section "Changes over time" | |
| Schedules and limits: device data limit, group data limit, time quota, service quota, service time limit | Drawer > Overview > "Limits" (folded) for device; Profile editor > Settings > More options for per-profile | `/api/data-limits`, `/api/time-quotas`, `/api/service-quotas` |
| Schedules and limits: Service lists, browser service profile | Profile editor > Services tab; per-device block in drawer > Traffic | Needs scope check, see Phase 5 |
| Router and Wi-Fi: router setup, password | Network > Router Wi-Fi | |
| Router and Wi-Fi: Pi access point, Wi-Fi name reset | Network > Pi Wi-Fi (reset is inside "Recovery", folded) | Not first any more |
| Router and Wi-Fi: manual fallback card | Network > Recovery (folded, with the Pi cable check shown above the router switch) | |
| Router and Wi-Fi: Updates and recovery | More > Updates | Message when none staged: "You are on the newest approved version. An admin can stage a newer one." |
| Traffic | Device drawer > Traffic (per device) and Now cards (rates). Global rates view under More > Activity | `/api/traffic` |
| Measurements | Drawer > Overview > "Check now" for the quick test. Before/after phone-side test under More > Diagnostics > "Measure from the phone" | `/api/test-sessions/save` kept |
| Activity | More > Activity | `/api/activity`, `/api/action-history`; also feeds the History tab per device |
| Access and rules: dashboard access | More > Settings > Dashboard access | |
| Access and rules: MAC filtering | Network > Access rules | |
| Access and rules: website filtering (5 textareas) | Network > Access rules > "Blocked sites" (one list, one box) | |
| Access and rules: DNS failure chance | Network > DNS | |
| Diagnostics: Impact | Drawer > Overview > Live impact; summary on Now cards | `/api/impact` |
| Diagnostics: system health, raw JSON, compiled plan | More > Diagnostics > Details (folded) | |
| Sign-in dialogs (token, credentials, Allow this device) | One sign-in screen, token field, "Sign in" also registers the device | `/api/dashboard-access/register-self` |
| Help icons (16) | Inline one-line hints under fields | Removes hover-only help, which fails on touch |
| Toast + status + message line | One toast, with Undo | |
| Group-level pages/controls | More > Groups (small list) | Rarely used; keep, do not feature |

Removed outright: Reset network button, Re-apply button, Assign a device form, sidebar tiles, duplicate message line, Open device controls, theme toggle (none existed).

## 3. Top tasks: old vs new

| Task | Old (audit) | New |
|---|---|---|
| a. Bad 3G on one phone | 3 to 4 clicks, 0 to 1 page switches, 3 to 8 screens of scrolling on a phone, may do nothing because of the general profile | Devices, tap the phone, pick "Slow 3G" in the drawer. 3 taps, 0 page switches. The sentence under the picker says who wins. Toast: "Pixel 8 now on Slow 3G. Undo" |
| b. Everyone to another profile | 1 to 2 clicks, devices with their own rules not covered | Now: tap a profile chip. 1 tap. Devices with their own profile keep it and the toast says "2 devices keep their own profile" with a "Reset those too" link |
| c. Panic | 1 click, long banner, Reset network confusion | 1 click, same. Banner: "Everything is stopped" plus Start. Toast with Undo |
| d. Does it work on that phone | est. 2 to 4 clicks, 1 to 3 page switches | Open the device drawer: live sparkline and numbers already there. "Check now" gives before/after. 1 to 2 taps, 0 switches |
| e. Block one app for one device | est. 12+ clicks, 2 to 3 switches, no direct path | Devices, device, Traffic tab, tap Block next to the name. 4 taps, 0 switches. Needs Phase 5 backend check |
| f. New profile | est. 6+ clicks, 19 fields, typed ID | Profiles, "New profile" (or Duplicate a preset), name, move 2 sliders. Auto-saved. 3 to 5 actions. "Use for" menu at the end offers apply |
| g. Router Wi-Fi off, keep control | est. 5 clicks, switch disabled until password | Top bar switch. If no password: prompt right there, then it switches. Cable warning is a visible check ("Pi is on Ethernet: yes"), not text. 2 to 3 taps first time, 1 afterwards, with Undo |
| h. Find and install update | est. 4 clicks, scroll 1400 px, unhelpful message | More, Updates. Shows current version, "Check", and install if staged. 2 taps. Dot on More when one is ready |
| i. Protect my Mac | 1 click plus scroll, disabled controls left on screen | Device drawer, Protect switch. Row shows a Protected tag and nothing else. 2 taps |
| First sign-in | token, close panel, Allow this device | token, Sign in |

## 4. Interaction details

Command palette (Cmd-K or Ctrl-K, also `/` when not typing). Fuzzy search over: go to a place, Start, Stop, Panic, router Wi-Fi on/off, "Use <profile> for everyone", "Open <device>", "Protect <device>", "Check updates". Arrow keys, Enter, Esc. Shows the shortcut next to each command. It is the power-user path for the audit's "5 ways to apply".

Shortcuts: `g n` Now, `g d` Devices, `g p` Profiles, `g w` Network, `g m` More, `?` help, `Esc` close drawer or palette. Panic has no single-key shortcut on purpose (accidental press while typing); it is in the palette and the top bar. All shortcuts ignored inside inputs.

Toasts: one at a time (a new one replaces the old one, so it never builds a pile that covers content), bottom centre (above the tab bar on phone), `role="status"`, 6 s, Undo button. Undo restores the previous server state with a `POST` of the old value (client keeps one snapshot per action). A toast never covers row buttons: row actions live in the drawer.

Empty states: each page says what it is for and offers the next step. Devices with no clients: "Nobody is connected. Join the Pi's Wi-Fi from a phone" plus the Wi-Fi name and a copy button. Profiles with none custom: "These presets are ready to use. Duplicate one to make your own." Filter with no match: "No device matches" plus Clear filter.

First-run checklist on Now (dismissible, remembered per browser): connect a phone, pick a profile, press Start, check the impact. Each item ticks itself from state. A progress count (1 of 4).

Loading: skeleton cards on first load only (no spinner walls). After that the old content stays on screen and updates in place. Buttons that wait on the Pi show an inline "Applying" label and are disabled for that request only.

Wording: sentence case, no IDs on screen, numbers with units ("120 ms", "2% loss", "5 Mbps").

Phone mapping: bottom tab bar = the five places. Drawer = bottom sheet with drag handle. Cmd-K button hidden; search field at the top of Devices. Top bar compact. Everything works at 375 px with 16 px gutters and 44 px targets.

## 5. Responsiveness architecture

Today (measured in code): five independent timers in `server.py`: `pollLiveStatus` 5 s, `pollDashboard` 5 s, `pollTraffic`, `loadImpact` poll, credentials sync 60 s, plus per-page `request("/api/...")` calls. There is already `/api/state`.

Target:
1. One state source. Step 1: `GET /api/state` extended into the single payload (run state, general profile, router state, devices with effective profile and protection, last event id) with a strong `ETag` over the payload. Client sends `If-None-Match`; unchanged returns 304 with no body. Poll every 2 s while visible. Per-device impact/rate samples come from `GET /api/live?since=<n>` (small, only deltas) at 1 s while a sparkline is visible. Step 2 (optional, later): `GET /api/events` as server-sent events pushing "state changed" and live samples. The stdlib `ThreadingHTTPServer` can do SSE (one thread per open page, keep-alive comment every 15 s). Auth: `EventSource` cannot send custom headers, so use a fetch stream with the existing token header, or the session cookie if one exists. I did not check how the token is sent today; confirm before choosing. Keep the poll as fallback when the stream drops.
2. Diff-based rendering. A small keyed renderer (`patch(container, items, keyFn, renderFn)`) updates text, classes and attributes in place and reuses DOM nodes by key. Rules: never replace a node that has focus; never replace an open `<select>`, never touch scroll position; a draft in an input wins over incoming state until blur or Enter.
3. Optimistic updates. A click changes the local state immediately, marks the row "Applying", sends the request, and on error rolls back with an error toast. The server's next state confirms.
4. Request de-duplication. One in-flight `GET` per URL (shared promise). A newer `POST` for the same target (e.g. the same device profile) cancels the older queued one. Slider changes are debounced 250 ms.
5. Pause when hidden. `visibilitychange` stops timers and the stream; on return one immediate `If-None-Match` fetch.
6. Backoff. On network error, retry at 2, 4, 8 s up to 15 s, and show a quiet "Reconnecting" pill in the top bar, never a toast loop.

Backwards compatibility: all current endpoints stay. The new payload is additive.

## 6. Glossary (one word each)

| Use | Not |
|---|---|
| Protected | Unaffected, Unaffected by profiles, Make unaffected, Remove protection |
| Everyone | General profile, base profile, everyone else |
| Same as everyone | Saved device rules paused |
| Profile | Saved profile, condition set, policy |
| Start / Stop | Start with saved settings, Re-apply, Stop general profile |
| Panic | Panic and Reset network (two buttons) |
| Block | Website access, service list entry, DNS failure for one name |
| Check now | Run measurement, diagnostics impact |

## 7. Implementation plan

Each phase is its own PR, branched off main after PR 53 lands (or rebased on it). Sizes: S under 1 day, M 2 to 3 days, L 4 to 6 days. The existing browser journeys live in `tests/browser/` (`journeys.py` runner plus one module per topic). A phase may change only the journeys it names.

| Phase | Content | Size | Browser journeys to update | Risk |
|---|---|---|---|---|
| 0. Quick wins | Sign-in title/heading neutral, one-step sign-in, one toast (drop duplicate message line and sidebar copy), glossary renames (Protected everywhere), strip IDs from profile labels, remove dead "Open device controls". No layout change | S | `dashboard_access`, `token_remember`, `protected_clients`, `saved_profiles`, `sidebar_status` | Low |
| 1. State layer | Extend `/api/state` + ETag, `/api/live` deltas, one scheduler with visibility pause, `patch()` renderer, optimistic helper, de-dup. Old pollers removed one by one, pages unchanged | M | `general_profile`, `impact_check`, `sidebar_status` (timing assertions) | Medium: regressions in focus behaviour; keep the existing "focused row survives a 7 s wait" check and add select-open check |
| 2. Shell | Top bar (status, router switch, Panic), 5-place rail / tab bar, routes `#now #devices #profiles #network #more`; old pages mounted under the new places unchanged inside. Remove sidebar status/tiles/Reset/Re-apply. Folds in PR 53 phone rules | L | `dashboard_navigation`, `sidebar_status`, `wifi_recovery`, `router_copy`, `phone_layout`/`phone` (from PR 53), `journeys.py` page list | High: touches every page; ship behind `?shell=new` for one release, then default |
| 3. Now + device drawer | Now page, device list, drawer with Overview/Traffic/History, Protect switch, "Same as everyone" logic, live sparkline, Check now, toasts with Undo, empty states, skeletons | L | `apply_clarity`, `saved_profiles`, `protected_clients`, `device_names`, `impact_check`, `general_profile`, `phone_layout` | Medium: needs Undo for each POST (store previous value client side) |
| 4. Profiles | Preset cards, editor with sliders and live preview (client-side model of delay/jitter/loss), tabs Settings/Services/Schedule, ID generation, remove Assign form | L | `saved_profiles`, `apply_clarity`, `general_profile`; new `profile_editor` | Medium: field mapping of the 19 old inputs, keep every one under "More options" |
| 5. Block an app per device | Drawer Traffic tab Block button. Check what `/api/service-assignments` and service lists scope to (audit says all unprotected devices); if not per device, add a per-device block list (small server change + unit tests) | M | new `block_app`; `impact_check` | Medium: backend scope unknown, I did not read the planner code |
| 6. Network and More | Network page (router with inline password prompt, Pi Wi-Fi, access rules merged, DNS, recovery folded), More (Updates with better messages, Diagnostics with Details, Activity, Settings) | M | `router_copy`, `wifi_recovery`, `dashboard_access`; new `updates_page` | Medium: Wi-Fi recovery is a safety path; keep every existing text assertion until reviewed |
| 7. Power use and first run | Cmd-K palette, shortcuts, first-run checklist, help overlay | M | new `command_palette`, `first_run` | Low |
| 8. Events stream (optional) | `/api/events` SSE with fallback to poll | M | `sidebar_status`, new `live_stream` | Medium: thread per client on the Pi; test with 5 open tabs |

Order: 0, 1, 2, 3 give the biggest change for the owner's complaint. 4 and 5 follow. 6 and 7 can run in parallel. 8 only if the 2 s poll still feels slow on the Pi.

Total: about 4 L, 4 M, 1 S, roughly 5 to 6 working weeks for one person. Phase 2 is the one to schedule carefully; ship it flagged.

Test notes: each phase keeps `tests/unit/dashboard_ids.txt` in sync (element ids are asserted there). Prefer stable `data-testid` attributes in new code so later redesigns do not break journeys again.

## 8. What the prototype shows (and does not)

Shows: the five places, top bar, device drawer, profile editor with live preview, Cmd-K, shortcuts, toasts with Undo, Panic and Start, first-run checklist, skeleton, empty state, phone tab bar.
Does not show: real networking, the full 19 profile fields (only the four main sliders), groups, schedules in depth, sign-in, or the diff renderer (it redraws on action but keeps live sparklines separate, as the real one would).

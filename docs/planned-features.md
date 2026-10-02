# Planned features

This page lists what JitterSim does not do yet. It is a working list for the owner and for anyone who helps. Add an item when you think of one. Move it to the finished list when it ships.

Last updated: 2026-10-02. The list was checked against three reviews of the earlier work on that day.

Known problems and fixes that are not features live in [planned-fixes.md](planned-fixes.md). Put a bug there and a new capability here.

How to read an entry:

- **Status** is one of: planned (nobody has started) or in progress (a branch exists) or needs a decision (the owner has to choose first) or taken (a separate session owns it so ask the owner before you start).
- **Why** says what problem it solves in plain words.
- **Notes** record what is already known so the next person does not start from zero.
- A short entry under Ideas gives the same three parts in one paragraph: the title then the status and size in brackets then the why then the notes.

Two lines of code exist. The Pi line is what the Pi runs now. The main line has more features. The draft integration pull request (item 7) merges them. An entry says which line has what where it matters.

Please check `git branch -r` and the open pull requests before starting an item. Two people working on the same item cause painful conflicts in the dashboard file. Use one branch for each item.

The things the owner has to try on the Pi are in the [Pi acceptance checklist](pi-acceptance-checklist.md).

## Requested by the owner

### 1. Neutral sign-in page

- **Status:** planned.
- **Why:** the owner asked that nobody on the test Wi-Fi should be able to read the name jittersim before signing in. Today the first page shows it.
- **Notes:**
  - The main line had this. It served a plain sign-in page and used a neutral Wi-Fi name (TestNet) and a neutral certificate name. It also stopped a client after five wrong tries.
  - The integration branch dropped it ("Session sign-in not kept" in the decision list of the draft pull request). The Pi line never had it.
  - Today the address `/` gives the dashboard page itself to anyone on the test Wi-Fi. Its title is jittersim. Only the data behind it needs the token. The guides call it a limited sign-in page but it is the real page with its private parts locked.
  - The name also shows in the default Wi-Fi name `JitterSim` and in the certificate name `jittersim.local`.
  - Plan: serve a plain page without the name at `/` until the token is accepted. Use neutral defaults for new installs. Add a limit on wrong tries. Existing installs keep their Wi-Fi name unless the owner renames it in the dashboard.
  - Size: medium. Build it on the integration branch once that lands.

### 2. Homepage bounce

- **Status:** taken (the design is with a separate session).
- **Why:** the owner asked whether a visit to a page deep inside a site could sometimes send the browser back to the site's home page. This tests how an app copes with a surprise redirect.
- **Notes:**
  - DNS cannot do this. DNS only sees the site name and never the page path.
  - Two ways could work: a browser extension in the test browser or a proxy that opens HTTPS traffic with a certificate that the test device trusts.
  - The simpler first step is the "site can't be reached" error with an adjustable chance. The integration branch brings the DNS failure chance from the main line.
  - Decide first which devices need it. A browser extension covers only that browser.
  - No code exists on any branch.

### 3. Real phones on the Pi

- **Status:** taken (the physical acceptance run is with a separate session).
- **Why:** every network check so far used Linux test machines or the Pi's own tools. The general profile and the protected devices have never been tried together on real phones.
- **Notes:**
  - Acceptance test: two affected phones and one protected phone on the Pi Wi-Fi with the general profile active. Check speed limits are shared by the two affected phones only. Join and leave the Wi-Fi while it runs.
  - Check the Impact card shows loss and speed caps for each phone.
  - Check that the protected phone is never listed in Traffic discovery or the Impact card.
  - The checks nobody has run yet on the Pi are in the [Pi acceptance checklist](pi-acceptance-checklist.md).

### 4. Chrome cannot open the dashboard

- **Status:** done on 2026-10-02: Chrome opens the dashboard once its Local Network permission is on (tested on the Pi at its router address). Kept here so the steps are not lost.
- **Why:** Chrome showed "This site can't be reached" for the Pi's dashboard address while Safari worked.
- **Notes:**
  - The Pi answered this Mac. The most likely cause is the macOS Local Network permission for Chrome. A VPN on the Mac can also hide the local network.
  - Open System Settings then Privacy and Security then Local Network and allow Chrome.
  - If it still fails, check `nc -vz 192.168.0.17 8080` from the Terminal.

### 5. Router Wi-Fi switch checks

- **Status:** taken (the live router checks are with a separate session).
- **Why:** the sidebar switch turns the router Wi-Fi off and on. The owner turned it off once on 2026-10-01 and it worked. The full off and on check from the dashboard is still required.
- **Notes:**
  - The code lives in `networking/router.py` and `api/router_operations.py` and the `/api/router/` routes. Do not change router switching without asking.
  - Keep the Pi on Ethernet during the check. The router's own admin page is the way back.

### Traffic discovery that does not miss devices

- **Status:** planned (built in this order after the weekly usage reset).
- **Why:** Scan now listens for only about 5 seconds, so a scan misses most names unless a device looks something up in that moment. The owner expected the page to show the last 30 days. A device that uses encrypted DNS (Firefox secure DNS or iCloud Private Relay) never shows up at all.
- **Steps:**
  1. **Longer window (small).** Make Scan now listen for about 30 seconds. The page says how long it listens and shows a countdown.
  2. **Background collector (medium).** The small service `jittersim-service-watch` already reads every DNS answer sent to the test Wi-Fi and skips protected devices by hardware address. Let it also record the names into the Traffic discovery list. Batch the writes every few seconds. Keep names 30 days from their last sighting and cap the list size. Add a switch on the Traffic page named Collect names in the background and a Clear list button. The page text must say plainly when collection is on. The exclusions for protected devices stay.
  3. **Secure DNS fallback (small).** The Pi answers "no such name" for `use-application-dns.net` so Firefox turns its secure DNS off on this network. It does the same for `mask.icloud.com` and `mask-h2.icloud.com` for iCloud Private Relay. It drops connections to well known public secure DNS servers so a browser set to one falls back to the Pi.
  4. **Read the site name from the HTTPS handshake (medium).** Most HTTPS connections announce the site name in their first packet. Capture those names on the Pi for devices that skip DNS. Protected devices stay excluded in the capture filter. Sites that use Encrypted Client Hello hide the name, so this is a fallback and not a full answer.
- **Notes:**
  - Until step 3 exists, turn off DNS over HTTPS in Firefox (Settings, Privacy and Security) when you test the scan.
  - Every capture must keep the rule that protected devices are never monitored and never appear in the list.
  - The upgrade that added the hardware address column cleared the old list once. The planned notice for that is in `planned-fixes.md`.

## In progress

### 6. Remote command line and one-command deploy from the Mac

- **Branch:** `feat/cli-remote-and-deploy`. A draft pull request exists against the integration branch.
- **What:** `jittersim status`, `profiles`, `traffic`, `wifi show`, `impact` and `connect` run on a Mac against the Pi's dashboard over verified HTTPS. A deploy script builds a release, copies it to the Pi and stages it. The password prompt for `sudo` stays with the owner.

### 7. Integration of the main line

- **Pull request:** the draft "Integrate main into the Pi line". The branch is `integrate/main-into-stack`.
- **What:** brings scenarios, the activity log, access controls, the DNS failure chance, staged updates and the large command line from the main line into the Pi line. It also brings delete for profiles and devices and schedules and limits and a plan preview that no longer uses up random rerolls. It stays a draft until its own checks are green and the owner has tried it on the Pi.
- **Left:** green checks on its own branch then the owner's Pi test. The extra checks are in part B of the [Pi acceptance checklist](pi-acceptance-checklist.md).

### 8. Entering the admin token again resets the 24 hours

- **Branch:** `feat/token-remember-refresh`. The branch holds one commit and waits for review.
- **What:** the remembered token (and router password) gets a fresh 24 hours when the owner enters it again. Restoring it automatically on page load does not extend it.

### 9. Phone layout of the dashboard

- **Branch:** none pushed yet. Check `git branch -r` before you start.
- **Why:** the owner wants to use the dashboard from a phone while standing next to the test devices. The page fits a narrow window without sideways scrolling but it was designed for a laptop screen. Under 768 pixels the sidebar only stacks above the page. On a 390 pixel phone Panic sits about 400 pixels down and the page content starts about 660 pixels down.
- **Notes:**
  - Build it as one pull request on top of the integration branch. Building it on the Pi line now means a large conflict in the dashboard file.
  - Needs: Panic always in view (a sticky bottom bar). The router switch must stay one tap away. A collapsible menu or a bottom bar with the five most used pages. Apply and Reset and the credential buttons inside that menu so the page starts on the first screen. Touch targets of 44 pixels. Dark only.
  - Scroll to the top of the page after a navigation tap.
  - The browser journeys check 390 pixel width for overflow. Extend them to check that Panic and the router switch stay in view at 390 by 844 and 360 by 640 and 320 by 568.
  - Test with a real phone on the Pi Wi-Fi and on the router network.
  - A pinned footer that hid the router switch was tried and rejected because the router controls matter too.

### 10. Random variation and outages for service lists

- **Branch:** none pushed yet. Check `git branch -r` before you start.
- **Why:** a service list uses its profile's fixed numbers. Random variation in that profile is not rerolled and random outages do not apply to a list.
- **Notes:** reroll each list on the scheduler tick and give each list its own outage window. Size: medium.

### 11. Hardening batch

- **Branch:** none pushed yet. Check `git branch -r` before you start.
- **Why:** the review of the service list work left small gaps that are all in the same few places.
- **Notes:**
  - A service hostname such as `com` is accepted and would match every `.com` name. Require two labels at least.
  - The scheduler retries a failing plan every second and writes a stack trace each time. Add a capped backoff and one clear error state.
  - Website rule lookups have no deadline while the plan is built. Resolve in a bounded pool as the service list resolver does.
  - The general profile check turns a busy helper into "Update the Pi helper". Pass the busy message through as the list check does.
  - Seven id patterns in the forms (nine on the integration branch) use `[a-z0-9_-]` which browsers with the newer `v` regex flag reject and ignore. Escape the hyphen as the service list form does.
  - **Add to profile** switches a profile to "allow only listed domains" when it was not an allow list. Every other site then stops working for devices on that profile. Ask for confirmation first.

## Ideas that look useful

These come from reviews of the recent work. Nobody has asked for them yet unless an entry says so.

### Dashboard

- **Delete a profile** (planned, small). A profile can be used by devices or schedules or service lists so a delete needs a check that shows what would break. The Pi line has no delete call for profiles. The integration branch has `/api/profiles/delete` from the main line so it arrives with the integration. Check what it shows for a profile in use.
- **Rename and regroup traffic names** (planned, medium). The groups come from a built-in table. Let the owner move a name to another group or give an unknown name a label.
- **Other tabs** (planned, small). The Saved line under the Start button reads devices and groups and lists once. A change made in another tab shows after a reload. Poll them like the connected clients.
- **Double announcements** (planned, small). The message line and the pop-up message both announce each message so a screen reader reads it twice.
- **Accessibility audit** (planned, medium). Run axe-core on every page. It could not be run so far.
- **Navigation names clash** (planned, small). The links to Devices and Profiles and Diagnostics use the same names as three text blocks on the Diagnostics page. A link can land on the wrong one. Rename the three blocks or catch the link click. The first-run guide on the main line already does the second.
- **Stale hints** (planned, small). The forms moved to the Profiles page but two hints still say below. They are "Create a group below" in the group picker of a device row and "Create a saved profile below to use this control." under General profile. Point them at the Profiles page.
- **Reduced motion** (planned, small). Smooth scrolling still plays for people who ask their system for less motion. The scroll in `revealElement` asks for smooth and the style rule cannot override it. Use plain scrolling when `prefers-reduced-motion` matches.
- **Bulk selection and remaining capacity** (planned, medium). Moving many clients into a group takes one click each and nothing shows how close the plan is to its limit of 15 queues. Add a way to select several clients and a line such as `N of 15 used`. Today only the inline group move exists.
- **Effective profile and the reason** (planned, medium). The sidebar shows the saved device or group profile only. A timer or schedule or quota can win and nothing says so. The main line has reasons in `engine/conditions.py`. Check that the integrated Devices page covers every client and add the source and reason where it does not.

### Diagnostics

- **History of the Impact card** (planned, medium). Keep a short history per device and draw it as a small graph. Today it shows only the last minute.
- **Delay measurement** (planned, small). The Impact card cannot measure added delay because it reads packet and drop counters only. A short ping test could add it. The owner decided against it for now.
- **Shared answer** (planned, small). `/api/impact` is read again by every open tab. A short server side cache would cut the work.
- **Running state** (planned, small). The "running" flag reflects whether the scheduler is paused and not whether its thread is alive. Make it check the thread.
- **Speed after shaping** (planned, medium). The traffic lines count bytes before the queues so they show what was sent and not what the device received. The Impact card shows kernel counters only. The reference line plan left this out on purpose.
- **Measuring from more devices** (planned, medium). `jittersim measure` refuses Windows and phones have no measure page. A small page that a phone can open would let it measure its own connection.

### Network

- **Duplicate packets** (planned, medium). The kernel refuses a duplicating delay queue next to any other delay queue on the same interface. A device can only duplicate while it is the only one with a delay queue. Look for another way such as separate queues per direction.
- **Fair use of the helper** (planned, small). A write that waits for the helper can in theory lose to a tight loop of reads. A "write waiting" flag would close the gap.
- **Quieter logs** (planned, small). Traffic counters write a full error trace on every failed read.
- **Queue limit** (planned, small). The delay queue keeps the system default length so packets drop silently above about 20 Mbit/s at 600 ms delay. Set the limit from the rate and the delay with a lower bound.
- **Fair sharing between devices** (needs a decision, large). The root queue serves classes in strict priority order and not in fair shares. This was left out on purpose. A change needs another queue type and a redesign of the plan.
- **Incoming shaping with an ifb device** (needs a decision, medium). Only if measurements on the Pi show that the current shaping of downloads falls short. Nothing exists in the code.
- **Household network design** (needs a decision, small). Decide whether Wi-Fi devices need the router's wired printers and TVs and storage. The first trial chose internet only. Tick or settle the open boxes in `ROADMAP.md`.
- **Profiles after a Pi restart** (taken, design). The router outage option decides what happens when the router goes away. What happens after a Pi restart or per profile is not decided. The plan is with a separate session. The build follows once the owner approves it.

### Service lists

- **Device in a random outage loses the Pi DNS** (needs a decision, medium). A device in an outage also loses the Pi's DNS and browser proxy port so a listed service works only from addresses it already knows. This is a documented limit today. A fix lets DNS and the proxy port through for such devices while lists are on.
- **Browser tunnels are not counted** (planned, small). Traffic that a browser sends through the proxy to a listed service is missing from the device's data counters. This is a documented limit. Fix it only if device quotas must count that traffic.

### Reliability and recovery

- **Outage stop marker** (planned, small). The marker that keeps profiles stopped after a router outage lives in `/run/jittersim/gateway-state.json`. The helper unit has `RuntimeDirectoryPreserve=restart` which only protects a restart. A plain stop and start may delete the folder and let profiles resume after an update. Not checked on the Pi. Move the file to `/var/lib/jittersim` or verify it. Integration branch only.
- **Wait for the clock** (planned, small). A Pi has no battery clock. The dashboard does not wait for time sync before it recovers after boot and before schedules run. Order it after `time-sync.target` or check the clock first.
- **Stop cleanly** (planned, small). Only the DNS watcher handles SIGTERM. Stopping the dashboard with `systemctl stop` ends the scheduler in the middle of its work. Add a handler to the dashboard main.
- **Browser proxy start race** (planned, small). The proxy crashed once during an install because the Wi-Fi address was not up yet. The unit binds `192.168.50.1` and starts after NetworkManager only. Bind with `IP_FREEBIND` or order it after the access point.
- **DNS service health** (planned, small). Nothing reports a dead resolver while access rules are on. Show the state of `jittersim-dns` in the status and the diagnostics.
- **One scheduler only** (planned, small). The roadmap wants one scheduler to own all transitions. No lock around the scheduler was found. Check how a second dashboard process is kept out.
- **Byte progressions restart from zero** (planned, medium). A progression that moves by data used loses its progress when the dashboard restarts or after Reset. The starting point is saved but compared with a total in memory that starts at zero. Save the per-device totals and load them at start. Main line and integration branch.
- **A partial save resets the rest** (planned, small). A save of access rules without the MAC part or the website part sets the missing part to off or to no limits. The dashboard always sends both so only scripts and the remote command line are at risk. Keep a missing part as it is. Integration branch.
- **Refuse impossible settings** (planned, small). A time limit longer than its window is accepted. So is a progression with the same start and end profile and an outage chance with a duration of zero. A schedule end stops at 23:59. Add checks with tests. Integration branch.
- **Timers and progressions for groups** (planned, medium). They only target single devices. This was left out on purpose. Do it only if the owner wants it.

### Security

- **Router login throttle** (planned, small). The dashboard does not limit wrong router password tries on the server side. The browser stops after one rejection. A script could keep trying.
- **Clipboard** (planned, small). The Copy Wi-Fi password button leaves the password on the clipboard until something else replaces it. Offer a timer that clears it when the browser allows.
- **Wi-Fi password in the process list** (planned, small). While `nmcli` runs the test Wi-Fi password is one of its arguments so another user on the Pi can read it with `ps`. `networking/wifi.py` does this. Hand it over in a password file that only root can read.
- **Certificate without the test Wi-Fi address** (planned, small). The certificate covers the Pi name and `jittersim.local` and the Ethernet address but not `192.168.50.1`. A browser on the test Wi-Fi that opens that address shows a warning for the wrong reason. Add the address in `install.sh`. An existing Pi needs the installer again and a new trust step.

### Releases and remote access

- **Online release discovery** (needs a decision, medium). The Pi only sees releases that someone staged by hand. Finding new ones online needs a trusted private source for release information. `deploy-from-mac.sh` is the workaround. Decide together with the Mac deploy work.
- **Tailscale script** (planned, small). Remote access is a manual guide only. There is no script and no check that the Pi is enrolled. Add one only if the owner wants it.

### Tests and releases

- **Journey races** (planned, medium). Two browser journeys failed on timing in CI before they were fixed (a message that arrived late and a busy helper). On the integration branch the navigation journey also failed once right after a page reload. The remembered token was probably still being restored. Review every journey for waits that depend on timing and wait for state instead.
- **Browser versions** (planned, small). The local Playwright version differs from the one in CI. Pin both to one version.
- **Pi in the loop** (planned, large). A regular check that installs the release on a spare Pi and runs the journeys would catch what the Linux container cannot.
- **Two flaky tests** (planned, small). `test_the_client_sends_addresses_and_returns_the_count` in `tests/unit/test_service_addresses.py` failed once for an unknown reason. The tests in `tests/unit/test_dashboard_connections.py` are flaky on timing inside Docker. Loop each in Docker and fix the race.
- **Tests that can pass while stuck** (planned, small). `tests/unit/test_dashboard_auth_state.py` and 14 more tests that run the dashboard script have no end marker so a case that hangs can pass without anyone noticing. Add the marker to each runner.
- **No IPv6 service list test** (planned, small). The real-kernel check for service lists covers IPv4 pass and drop only. Add an IPv6 upload drop case.
- **Test gaps in access and outages** (planned, medium). No command line test covers access apply and status or diagnostics or logs or update install and rollback. No runtime or API test runs the outage stop marker as set. No test covers outage races or a number that is not a number. Integration branch.

### Documents and housekeeping

- **Roadmap boxes** (planned, small). "Keep the dashboard exempt from impairment" is done on the Pi line and still unticked. Four more are done on the integration branch only: repeat applies leave the same state and import and export of the configuration and the normal and degraded and offline states and the burst loss presets. Most of the measurement milestone is done there too. Check each box against the code then tick it after the integration lands.
- **Old names in documents** (planned, small). `ROADMAP.md` still says the "floating Quick actions panel" in a ticked history line. The panel now lives in the sidebar. On the integration branch the README screenshots and `DESIGN.md` still show the old tab layout of the main line. Make the screenshots again from the sidebar build.
- **Merged branches** (planned, small). About 15 remote branches from the earlier sessions are fully inside the main line. Several local worktrees are merged too. Delete them after the integration lands. Ask the owner first because the remote branches are visible on GitHub.
- **Small cleanup** (planned, small). The integration branch lists `/api/update-preferences` among the action paths without a need for it.

## Not planned

- **A light theme.** The owner does not want one. The dashboard stays dark.
- **Four design check findings.** Dashed legend swatches and three similar findings of the automated design check were left alone on purpose.

## Finished

For the record. See the pull requests for details.

- Device names instead of IP addresses.
- Protected devices are never monitored and look greyed out.
- Plain language labels for observed names with "seen with" hints.
- Traffic groups with Add all to profile and Block all.
- Block globally shows its state and can be undone.
- Saved profiles list with Edit and Apply to all.
- Help icons on every profile field.
- Sidebar status with the general profile and the affected devices.
- Copy router Wi-Fi name and password.
- Impact card in Diagnostics.
- Clear wording for the Start button and whether JitterSim is running.
- Support for more than eight shaped devices in one plan.
- Left sidebar with one page at a time and a footer that keeps Panic and the router switch in view on a laptop.
- Service lists and the DNS watcher that fills them.
- Remember the admin token and the router password for 24 hours.

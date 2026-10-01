# Device names, traffic clarity, profile list, sidebar status and impact check

Date: 2026-10-01
Base: `feat/service-list-dns-learning` at `75c9b35` (the tip of PR #29)
Status: pieces 1 and 3 and 6 to 10 were approved in chat. Pieces 2 and 4 and 5
and the final sweep were requested afterwards. This written spec is waiting for
the user's review.

## Summary for review

Ten changes. Each ships as its own PR in the order below. Every PR
is built test-first and gets an independent review. Every PR body ends with an
unchecked "On the Pi" item because hardware checks are the user's.

1. Device names instead of bare IP addresses.
2. Protected devices are never monitored (a privacy fix: today their DNS lookups
   are captured and stored).
3. Plain-language labels for the DNS names devices look up.
4. The Block globally button shows its state and can undo.
5. Traffic groups: names that belong to one app collapse into one row.
6. Protected clients look different and their profile controls are greyed out.
7. A saved profiles list with Edit and Apply to all.
8. Sidebar status: the general profile and the devices affected right now.
9. Copy buttons for the router Wi-Fi name and password.
10. An impact check that shows loss and speed caps actually happening.

Not in this stack: the CLI commands and the merge of `main` and a deploy command
and the final branch and PR sweep. See "After these ten".

## Decisions already made with the user

- The copy buttons give the router's own Wi-Fi name and password. The Pi reads
  them from the router with the router password the user already enters.
- The impact check uses kernel queue counters only. A ping test is out of scope.
- Profile delete is out of scope. No delete call exists and a profile can be in
  use by devices or schedules or lists.
- Build on the current branch now. Merge `main` and write the CLI afterwards.
- The stack is linear. Each branch starts from the previous one. If PRs #27 to
  #29 merge first the stack is retargeted.

## What exploration found

- The dashboard is one file (`src/jittersim/api/server.py`) with the HTML and CSS
  and JS in one string. Tests run slices of it in node.
- The Traffic discovery card titles an address with
  `assigned_device_name || ipv4_address`. A client saved without a name is called
  `Client <ip>` (`_connected_device_from_payload`). Clients that are not saved
  show only the IP.
- Each connected client already carries `unaffected` (protected) from the server.
  The profile select and Apply button honor it. The group select and the Add to
  group button do not.
- Observations record `first_seen` and `last_seen` per name.
- `_profile_payload` is `asdict(profile)` so `/api/profiles` returns every field.
  The general profile is applied with `POST /api/general-profile` and exactly
  `{"profile_id": ...}` and stopped with `POST /api/general-profile/stop` and `{}`.
- Every queue is a `QueueSpec` with a device id and a direction and a mark.
  Upload queues sit on the uplink interface and download queues on the test
  interface. The kernel handle is `mark + 10`. Queue ids that are not saved
  devices are `GENERAL_QUEUE_ID` and `quota:<id>` and `assignment:<id>` and
  `servicelist:<id>`.
- `/api/router/status` returns only `{"enabled": bool}`. The router client can
  read the SSID and passphrase (`_settings_from_wifi_data`) but no helper action
  hands the passphrase to the dashboard.

## 1. Device names

**Problem.** Discovery and pickers show `Client 192.168.50.124` or a bare IP.

**Design.**

- New pure function `resolve_device_name` in `src/jittersim/config/device_names.py`.
  Order: the saved label for the MAC then a saved device name that is not the
  automatic placeholder then the hostname the station reported then Unknown
  device. A name the user typed beats a name the device announces.
- `/api/observations` adds `display_name` to each address group. It matches the
  IP to a MAC and hostname with `connected_device_snapshot()`. With no helper or
  a failed helper call it falls back to saved devices and labels and still
  answers 200.
- `/api/devices` adds `display_name` per saved device (label by MAC then saved
  name unless automatic then `Unknown device`). It needs no helper.
- The dashboard shows `display_name` as the title and the IP as small text in
  Traffic discovery and in every picker that builds `name (ip)` text. The raw
  saved `name` stays in the data.
- New saves from a connected client take the saved label and then the station
  hostname. `Client <ip>` remains only when neither exists. Every path that
  creates an assignment from a connected client uses the same rule. This fixes a
  small pre-existing defect in code this piece already touches.

**Tests.** Unit tests for every step of the resolver and the automatic-name
pattern. API tests for observations with and without a helper. A node test for
the card title.

## 2. Protected devices are never monitored

**Problem.** The user wants no traffic monitoring for devices in the Unaffected
list. An audit of every place that reads packets or counters found one gap and
one thing to prove.

- Gap: the DNS scan (`/api/observations/scan`) runs `tcpdump` on the whole test
  interface and the server stores a row per client and name. Nothing skips
  protected clients so Traffic discovery shows their lookups. Progression
  sampling uses the same capture.
- The DNS watcher (PR #29) parses every DNS answer the Pi sends on the test
  interface. That includes answers to protected clients.
- Already fine and kept: the firewall bypass for protected MACs
  (`plan.protected_mac_addresses`) and the browser proxy which treats a protected
  client as unaffected with no hostname classification.
- To prove: per-device and general traffic counters stay at zero for a protected
  MAC. The traffic note says only assigned devices are counted but a kernel test
  has to show it.

**Design.** Not monitored means no capture and no parse and no storage and no
display. One layer is not enough so there are four.

- **Capture.** `observe_dns_queries` takes the protected MAC list. The helper adds
  `not ether host <mac>` per MAC to the `tcpdump` filter so those frames (the
  client's queries and the answers to it) are never captured. The helper
  validates every MAC and caps the list at 64. Both callers (the dashboard scan
  and progression sampling) pass the saved Unaffected list.
- **Server drop.** Before saving a scan the server drops any observation whose IP
  belongs to a protected MAC. The IP comes from the connected-client snapshot and
  from saved devices with that MAC. `/api/observations` also hides rows for those
  IPs so old rows never show.
- **Fail closed.** If protected devices exist and the helper does not accept the
  MAC list (an older helper) the scan refuses to run and says the helper needs
  updating. It never captures protected traffic. The Unaffected list holds up to
  256 devices but the helper filter takes at most 64 so a scan refuses with a
  clear message while more than 64 devices are protected.
- **Purge.** Making a device protected deletes its stored observations (a new
  `delete_domain_observations` in storage keyed by IP) in the same transaction as
  saving the protection. It also clears the totals used as progression baselines
  for those IPs.
- **DNS watcher.** The watcher skips frames whose destination MAC is protected
  before it parses them. It reads the protected MACs from the helper on its
  existing plan check. Nothing is lost because service lists never apply to
  protected devices and other clients' lookups still teach the lists.
- **Interface.** Protected rows say "Traffic is not monitored". Traffic discovery
  notes that protected devices never appear. The traffic graph and the impact
  check never list them.

**Tests.**

- Unit tests: the `tcpdump` argument builder (MAC validation and the cap and the
  filter text). The scan drops protected IPs and fails closed. Observations hide
  them. Protecting a device purges its rows.
- Watcher test with synthetic frames: a frame to a protected MAC never reaches the
  parser (assert the parser is not called).
- Real-kernel Docker test in the style of the existing `tests/integration/linux_*`
  checks: send packets from a protected MAC and from an ordinary one then assert
  that every per-device and general counter and every observation tied to the
  protected MAC is unchanged. Mutation check: removing the filter must fail it.
- The review lists every code path that reads packets or counters (`tcpdump` and
  the `nft` counters and the watcher and the proxy) and says for each why
  protected clients are excluded.

## 3. Traffic labels

**Problem.** `a1051.dscg4.akamai.net` is an Akamai CDN edge name shared by many
companies. The name alone cannot say which app asked for it.

**Design.**

- New `src/jittersim/config/domain_labels.py` with `label_domain(domain)`. It
  returns `kind` (app or system or tracker or iot or cdn or unknown) and a
  `name` and a short `category`. The longest matching suffix wins.
- The table reuses `SERVICE_CATALOGUE` for apps. It adds WhatsApp and Meta and
  Apple and Google and Microsoft system and update names and connectivity checks
  and common trackers and smart-home vendors (for example `dreame.tech`) and the
  big CDNs (Akamai and Cloudflare and Fastly and CloudFront and Azure).
- An unknown name gets its registrable domain as the label so
  `api.header.immobilienscout24.de` reads `immobilienscout24.de`. The registrable
  domain is the last two labels or the last three when the second-to-last label
  is in a small built-in list of second-level suffixes such as `co.uk`. No
  dependency is added.
- `cdn` and `tracker` and `unknown` rows get `seen_with`: up to two identified
  app names whose `first_seen` is within 10 seconds of this name's `first_seen`
  for the same device. Names of one app count once and the nearest app comes
  first. When more than two apps share the window the hint is dropped because
  it would be too vague. System and smart-home names neither get a hint nor
  count as the app in a hint. The row labels it as a guess.
- Each `unknown` or `cdn` row has a "What is this?" link to a web search for the
  domain. It opens in a new tab with `rel="noopener noreferrer"` and sends
  nothing until clicked. The Pi never contacts an outside service.
- `/api/observations` adds `label` and `seen_with` per domain. The card shows the
  label first and the domain in monospace below it.

**Out of scope.** Reverse DNS and IP ownership lookups. They would send
addresses to outside services and still answer "Akamai" for CDN names.

**Tests.** Table integrity (no duplicate suffix and every entry normalized).
Label cases from real names: `mmx-ds.cdn.whatsapp.net` and
`a1051.dscg4.akamai.net` and `10000.mt.eu.iot.dreame.tech` and
`api.header.immobilienscout24.de`. `seen_with` with fixed timestamps including
the 10 second edge. A node test for the rendered row.

## 4. Block globally shows its state

**Problem.** In Traffic discovery the Block globally button never changes. A name
that is already blocked still offers Block globally. A second click changes
nothing and still says "Added". The only way back is to edit the comma list
under Access and rules.

**Design.**

- `/api/observations` adds `globally_blocked` per domain: true when the normalized
  domain is an exact entry in the saved website policy's `blocked_domains`. Exact
  matching is right because the engine resolves each listed name on its own
  (`resolve_domains`) and does not match subdomains.
- A blocked row shows a "Blocked" badge and its button reads **Unblock**. An
  unblocked row is unchanged. The button is not only greyed out because a greyed
  button leaves no way back.
- New `POST /api/observations/remove-global-block` with `{"domain"}`. It removes
  the exact normalized entry and returns the policies like the add call. It is a
  write path like the add call with the same token and busy handling. Removing a
  name that is not listed succeeds and changes nothing. An invalid domain gets 400.
- Both buttons refresh the card and the Access rules list. The message says the
  change is saved. Like Save access rules it takes effect on the next Apply. The
  PR confirms that on the fixture and words the message to match what happens.
- The sibling Add to profile button gets the same treatment. It reads
  "Already in <profile>" and is disabled when the domain is in the selected
  profile's website list. The state follows the profile select and needs no new
  data.
- Pre-existing and flagged for review: Add to profile also switches the profile to
  `allow_only` mode. A profile that was not an allow-list becomes one with only
  that domain so every other site stops working for devices on it. This piece does
  not change that. **Decision for the reviewer:** keep it or add a confirmation.

**Tests.** `globally_blocked` for exact match and normalization and a parent entry
not matching a subdomain. The remove endpoint for a present name and an absent
name and an invalid name and a missing token. A node test of both row states and
their request bodies and the "Already in" state.

## 5. Traffic groups

**Problem.** One app produces many names. The user's screenshot shows seven
`immobilienscout24.de` names and two Netflix names in separate rows. Reading them
one by one hides what the device is doing.

**Design.**

- New `group_domain(domain)` in `domain_labels.py` returns `{key, name}`. A known
  app or system service groups by its label so `nflxvideo.net` and `netflix.com`
  both land in Netflix. Anything else groups by its registrable domain so every
  `*.immobilienscout24.de` name lands in `immobilienscout24.de`. `/api/observations`
  adds `group` per domain. Grouping lives on the server so the CLI can reuse it.
- The card shows one row per group per device with the group name and the number
  of names and the total queries and a chevron (`<details>`). A group of one name
  is a plain row with no chevron. Groups sort by total queries then by name.
- Expanding a group shows its names with the per-name buttons including the
  Block or Unblock state from piece 4.
- The header has group actions: **Add all to profile** (the device card's selected
  profile) and **Block all** or **Unblock all**. The label is **Unblock all** when
  every name is blocked and **Block remaining** when some are. They call the
  existing endpoints with a `domains` list of up to 64 names. The call saves once
  so it is all or nothing and one invalid name rejects the whole call.
- Open groups stay open across the dashboard refresh. The open keys are kept per
  device address and group key the way the connected rows keep open label editors.
- A third-party name with no group of its own (for example `tags-eu.tiqcdn.com`)
  is not merged into an app by guesswork. It keeps its own row and the "seen with"
  hint from piece 3.
- No manual regrouping in this version.

**Tests.** `group_domain` for Netflix across two registrable domains and
ImmoScout24 across several subdomains including `s24-media` and a `co.uk` style
suffix and a single-name group. The endpoints with a `domains` list: the bound of
64 and duplicates and atomicity and one invalid name. A node test of the group
row: collapsed by default and counts and order and open state surviving a
re-render and the header buttons' states and request bodies.

## 6. Protected clients look protected

- A protected row gets the class `protected`: a grey border and dot and dimmed
  text in fixed muted colors (not whole-row opacity so contrast holds) and a
  "Protected" badge by the name. The existing line "Unaffected by all profiles
  and quotas" stays.
- Greyed out and disabled: the profile select and Apply and the group select and
  Add to group (`aria-disabled` as well). Still active: Remove protection and the
  name and type editor.
- Other new views follow the same rule. The sidebar list (piece 8) leaves
  protected clients out and the impact check (piece 10) lists them as "Protected".
- Pre-existing and flagged only: the group buttons were never disabled for
  protected rows. The PR body says whether the group endpoint refuses a
  protected client. If it does not that gets its own follow-up and this PR does
  not widen.

**Tests.** A browser-journey check that a protected row has the class and the
four controls are disabled and Remove protection is enabled and an ordinary row
is unchanged. Mutation check: removing the class or one disable makes it fail.

## 7. Saved profiles list

- A "Saved profiles" card sits above the form on the Profiles page. A row shows
  the name and id and a one-line summary built in JS from `upload` and
  `download` (for example `↑ 50 ms ±10 · 2% loss · 5 Mbit/s`). The profile that
  is the general profile gets a badge.
- Edit fills the form from `/api/profiles` data and scrolls to it. The id field is
  locked while editing so a save updates in place and cannot create a copy. A
  "New profile" button clears the form and unlocks the id. The heading reads
  "Edit profile" while editing.
- Apply to all calls `POST /api/general-profile` with `{"profile_id"}`. For the
  active general profile the button reads Stop and calls the stop path. Both
  reuse the existing success and error messages and busy handling.
- Edit must fill every field including randomness and outages and website mode.
  An unfilled field would silently reset on save. A round-trip test builds a
  profile with every field set then fills the form then reads the payload back
  and requires it to equal the original.

**Tests.** The round-trip test. Row summary cases. Apply to all and Stop request
bodies. The general-profile badge.

## 8. Sidebar status

A compact block between the navigation and the footer.

- **General profile:** `Bad test · active` or `Bad test · paused` or `none`.
- **Affected devices:** a count and a short list of display names with profile
  names (five then "+N more") in a `<details>`. When the runtime state is
  `applied` and the general profile is active the list is every connected client
  that is not protected. When it is applied without a general profile the list is
  the connected clients with their own profile. When the state is `reset` it says
  `Profiles off`. Before the first token check it says nothing.
- **Service lists:** one line `Service lists: A · B (every device)` when lists are
  enabled and the state is applied.
- All data is already polled (`/api/state` and `/api/general-profile` and
  `/api/connected-devices` and `/api/service-lists`). No new server calls.
- Laptop layout first. The phone layout stays on the later follow-up.

**Tests.** A node test that drives the render function through each state:
unknown and reset and applied with general and applied without and protected
clients excluded and lists enabled.

## 9. Copy router Wi-Fi name and password

- New `POST /api/router/wifi-credentials` with `{"admin_password", "include_password"}`.
  It needs the admin token like the other router calls. The name button sends
  `include_password: false` so the password is not even transmitted.
- New helper action `router-wifi-credentials` and `HelperClient` method. It reuses
  the router client's Wi-Fi read and returns `{ssid, password}` validated by
  `_valid_wifi_credentials`. When the helper does not know the action the
  dashboard says the helper needs updating (the same wording as the router status
  check).
- The response carries `Cache-Control: no-store`. The password is never logged and
  never stored in the action history and never written to the DOM or to page state.
- The browser writes the value with `navigator.clipboard.writeText` and falls back
  to a temporary read-only textarea with `document.execCommand("copy")` removed at
  once. It does not clear the clipboard later. The button says Copied.
- Without a router password the buttons open the Router password panel. After a
  rejected password they stay disabled until the password changes so repeated
  wrong guesses cannot lock the router login.
- Sidebar: two small buttons under the router Wi-Fi switch.

**Tests.** The endpoint needs the admin token. Wrong router password gives a
generic error without echoing it. `include_password: false` never reads or returns
the password. A log-capture test shows the password absent from logs and history.
Header test for `no-store`. Node tests for both buttons and the fallback and the
rejected state. The PR gets its own security-focused review.

## 10. Impact check

**What the user sees.** An "Impact" card at the top of Diagnostics with one row per
queue: the device name (piece 1) and direction and what is set and what happened
in the last minute and a verdict.

- Loss: `set 5% · actual 4.7% (31 of 660 packets dropped)`.
- Speed cap: `set 2 Mbit/s · using 1.96 Mbit/s · cap reached`.
- Queue wait: `about 320 ms waiting` only for a queue that has a speed cap and no
  delay (a delay alone also fills the backlog so it would mislead).
- Delay and jitter: `set 100 ms ± 20 · configured, not measured`.
- Verdicts: `affected now` when drops grew or the cap is reached. `set but no
  traffic yet` when no packets passed. `too few packets to judge` under 50 packets
  offered. Protected clients are listed on one muted line as not affected.

**Design.**

- New read-only helper action `queue-stats`: `tc -s -j qdisc show dev <iface>` on
  the uplink and test interfaces. It returns only qdiscs whose parent is the
  project root and whose handle is in the project range:
  `{interface, handle, kind, packets, bytes, drops, overlimits, requeues,
  backlog_bytes, qlen}`. The client validates every field like `traffic_counters`.
- New `GET /api/impact` (admin token). The server joins each counter to the
  compiled plan's `QueueSpec` by `(interface, mark + 10)` and adds the device name
  and the configured delay and jitter and loss and bandwidth. General and service
  list and quota and assignment queues are labelled by kind and id (for example
  `General profile` and `Service list streaming`). In the reset state it answers
  `available: false` with the same message the Traffic card uses.
- The browser polls every 5 seconds while Diagnostics is visible and the tab is
  visible. It keeps 12 samples per queue and works out the last minute from the
  first and last sample. A counter that goes backwards means the queues were
  replaced by an Apply so that queue's history restarts.
- Cap reached means a measured rate of at least 90% of the limit. It does not use
  backlog for queues that also have a delay.
- Old helper: the card says the helper needs updating and shows nothing else.

**Tests.** Parser and validator unit tests including hostile output. The join with
every queue kind. A real-kernel Docker test in the style of the existing
`tests/integration/linux_*` checks: apply 20% loss and send packets then assert
that drops track about 20% and grow. Mutation checks on the join and the counter
restart rule. Node tests for each verdict and the restart case.

## After these ten (separate work)

1. **Merge `main` into this line.** The two lines diverged on 2026-09-25. `main`
   has 203 commits this line lacks and this line has 62 that `main` lacks. A
   dry-run merge conflicts in about 32 files. `main` has the adjustable DNS
   failure chance and router outage persistence and the staged update check and a
   large CLI. This is its own PR with its own review and Pi test.
2. **CLI commands for the new features,** written once on the merged CLI:
   `traffic`, `profiles list|apply-all|stop-all`, `status`, `wifi show` and
   `impact`. They use the verified HTTPS dashboard API with a hidden token prompt
   like `dashboard-access allow-self`.
3. **One-command deploy from the Mac.** After the merge the dashboard has Check
   for updates and Install staged release and the CLI has
   `sudo jittersim update check|install|rollback`. They only see a release already
   staged on the Pi because the Pi holds no GitHub credentials. The missing
   convenience is a Mac-side command that builds the archive for a ref and copies
   it to the Pi and stages it. The sudo password prompt stays the user's.
4. **Home-page bounce** (an adjustable chance of sending the browser back to the
   site's home page). It was only ever planned as "a later browser feature".
   DNS cannot see or change a page path. It needs a browser extension in the test
   browser or an intercepting proxy with a certificate on the test device. It is
   not designed here.
5. **Branch and PR sweep, last.** The user asked for a final check that no PR or
   branch holds features this line lacks. An inventory on 2026-10-01 found:
   - Every branch tip on origin and every local branch is contained in `main`
     or in the stack tip `75c9b35`. No branch holds work that is in neither.
   - There are no stashes and no unpushed commits. The only uncommitted files
     are two untracked Codex plan docs in the main checkout:
     `implementation-plan-switching-test-failures-2026-10-01.md` and
     `implementation-plan-unaffected-devices-2026-09.md`.
   - Open PRs are #27 and #28 and #29. All other PRs are merged. PRs #17 and #18
     and #19 and #21 and #23 and #25 merged into `main` and so are missing from the
     stack until item 1 lands. PRs #20 and #22 and #24 and #26 merged into the
     stack base.
   - The real gap is therefore the divergence in item 1.

   After items 1 to 4 the sweep reruns that inventory and checks the ROADMAP
   items still unchecked and the two plan docs. It then lists anything left. Each
   merge or push or branch removal waits for the user's go-ahead because each
   approval covers one batch only.

## Notes

- The Chrome "This site can't be reached" error for `https://192.168.50.1:8080`
  is not a JitterSim defect. The Pi answers this Mac. Chrome is most likely
  blocked by the macOS Local Network permission. No code changes.
- Pre-existing and left alone: `tests/unit/test_dashboard_auth_state.py` lacks the
  `CASE-COMPLETE` marker the other node runners keep.

## Delivery

Branches (each from the previous): `feat/device-names` then
`feat/protected-not-monitored` then `feat/traffic-labels` then
`feat/block-button-state` then `feat/traffic-groups` then
`feat/protected-device-style` then `feat/saved-profiles-list` then
`feat/sidebar-status` then `feat/copy-router-wifi` then `feat/impact-check`.
Worktrees live under `~/Github repos/jittersim.worktrees/`. Each has its own venv.
Release archive from `git archive` for the stack tip when the user wants to test.

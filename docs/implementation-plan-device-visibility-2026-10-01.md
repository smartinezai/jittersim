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
  validates every MAC and caps the list at 256 which is the size of the
  Unaffected list so every protected device can be skipped. The filter groups the
  DNS test first (`( udp port 53 or tcp port 53 ) and not ether host ...`) so the
  MAC terms only run for DNS packets and 256 terms compile to about 2500 BPF
  instructions against the kernel limit of 4096. Both callers (the dashboard scan
  and progression sampling) pass the saved Unaffected list.
- **Server drop.** Each observation is stored with the hardware address of the
  client that owned its IP when it was scanned (read from the connected-client
  snapshot; an unknown or shared IP stays empty). Before saving a scan the server
  drops any observation whose MAC is protected or whose IP belongs to a protected
  MAC. The IP comes from the connected-client snapshot and from saved devices with
  that MAC. `/api/observations` hides rows by the same two keys so old rows never
  show. A row with no recorded MAC is matched by its IP alone (see Purge for the
  rows that predate the MAC). The MAC is never sent to the browser.
- **Fail closed.** If protected devices exist the client first asks the helper for
  its `protected_dns_capture` capability and refuses to scan when the helper does
  not advertise it or does not know the request (an older helper). The message says
  to restart the JitterSim services or reinstall the release. A helper that is busy
  or cannot be reached also blocks the scan but keeps its own message. No capture
  runs in any of these cases.
  The helper also echoes the MAC list it used and the client checks the echo as a
  second layer. It never captures protected traffic. If the saved list ever held
  more devices than the helper can skip the scan refuses with a clear message
  instead of capturing the rest.
- **Purge.** Making a device protected deletes its stored observations (a new
  `delete_domain_observations` in storage keyed by MAC and by IP) in the same
  transaction as saving the protection. A row goes when its recorded MAC is the
  protected one or when its IP is one the device uses now or had assigned. This
  also clears the totals used as progression baselines for those IPs. Rows saved
  before the MAC was recorded have no MAC. When an existing database gains the MAC
  column while the Unaffected list is not empty the upgrade clears every such row
  once in the same step because a device that was protected already may have moved
  and the rows are a cache that learns again. When the list is empty at upgrade the
  old rows stay and can only be matched by IP so one under an address a device
  later stops using stays until it expires.
- **DNS watcher.** The watcher skips frames whose destination MAC is protected
  before it parses them. It reads the protected MACs from the helper on its
  existing plan check. Nothing is lost because service lists never apply to
  protected devices and other clients' lookups still teach the lists.
- **Limits.** The watcher still receives frames for protected clients because it
  listens on the whole test interface. It skips them in userspace so nothing is
  parsed or stored but the skip follows the saved list with a lag of about one
  second plus the time to apply the plan. Protection is keyed by MAC so a device
  that rotates or randomizes its Wi-Fi address appears as a new unprotected device
  until its new MAC is added to the Unaffected list.
- **Interface.** Protected rows say "Traffic is not monitored". Traffic discovery
  notes that protected devices never appear. The traffic graph and the impact
  check never list them.

**Tests.**

- Unit tests: the `tcpdump` argument builder (MAC validation and the cap and the
  full filter text for the largest list). The scan drops protected IPs and MACs and
  fails closed before any capture with an old helper. Observations hide them.
  Protecting a device purges its rows by MAC and by IP and a failed purge undoes the
  protection.
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

- New `group_domain(domain)` in `domain_labels.py` returns `{key, name, kind}`. A name
  with a known label of kind app or system or iot groups by its label so `nflxvideo.net`
  and `netflix.com` both land in Netflix and every `*.immobilienscout24.de` name lands
  in `ImmoScout24`. A name with any other label (cdn or tracker or unknown) groups by
  its registrable domain (key `domain:<registrable>`) so two sites behind one CDN never
  merge into an app by guesswork. `kind` is the label kind of the name. A group takes
  the kind of its names and a system or cdn name wins when kinds differ.
  `/api/observations` adds `group` per domain. Grouping lives on the server so the CLI
  can reuse it.
- `registrable_domain` keeps the owner's label. It knows the common two-part public
  suffixes (`co.uk` and `co.kr` and `com.ar` and so on) and a list of multi-tenant
  hosting suffixes (`github.io` and `herokuapp.com` and `cloudfront.net` and so on)
  where the tenant label stays so `a.github.io` and `b.github.io` are different groups.
  Only a suffix with the customer label directly left of it belongs in that list so
  `fastly.net` and `edgekey.net` are not in it. Amazon names carry a region so a few
  patterns keep the customer and drop the region (`<bucket>.s3.<region>.amazonaws.com`
  and `<id>.execute-api.<region>.amazonaws.com` and `<lb>.<region>.elb.amazonaws.com`
  and `ec2-1-2-3-4.<region>.compute.amazonaws.com`). Any other Amazon name keeps two
  labels. A name that is only a suffix is its own group.
- The card shows one row per group per device with the group name and the number
  of names and the total queries. A group of one name is a plain row with no
  disclosure. Groups sort by total queries then by name without looking at the case.
- The group header is a row of three sibling buttons. The first is the disclosure
  toggle (`<button type="button">` with `aria-expanded` and `aria-controls`). It holds
  the chevron and the group name and the number of names and the total queries and the
  blocked count. The other two are the group actions. It is not a `<details>` element
  because buttons inside a `<summary>` are nested interactive content. The body is a
  `div` with an id that is unique per device and group and the `hidden` attribute while
  the group is closed.
- Expanding a group shows its names with the per-name buttons including the
  Block or Unblock state from piece 4. A row skips a label name that only repeats the
  group name. It also skips the category when every row of the group has the same one.
  A category that differs from the other rows stays because it tells a user which name
  is safe to block (Windows `Connectivity check` against `Windows services`).
- The header has group actions: **Add all to profile** (the device card's selected
  profile) and **Block all** or **Unblock all**. The label is **Unblock all** when
  every name is blocked and **Block remaining** when some are. They call the
  existing endpoints with a `domains` list. The call saves once so it is all or
  nothing and one invalid name rejects the whole call. Each action button has an
  `aria-label` that starts with the text the button shows and then names the group and
  the count such as `Block all 7 ImmoScout24 names` and `Add all to profile: 7
  ImmoScout24 names to Fast` (WCAG label in name).
- A group of system or cdn names has no **Block all** or **Block remaining**. One click
  would put connectivity checks and push and time sync into the block list and the
  device would show no internet. The button is disabled and the reason is a visible
  line under the header (`Blocking a whole group of system or content delivery names
  could cut the device off the internet. Block single names instead.`) that the
  disabled button names with `aria-describedby` because a disabled button is skipped by
  Tab. When such a group is partly blocked an enabled **Unblock blocked names** button
  removes the blocked names in one call. **Unblock all** and **Add all to profile**
  stay and so do the per-name buttons.
- Open groups stay open across the dashboard refresh. The open keys are kept per
  device address and group key (`<address>|<group key>`) in a module-level set that
  a click on the toggle updates. A render reads the set so a re-rendered group opens
  again.
- The header also says how many of its names are blocked (`All blocked` or `2 blocked`)
  because a collapsed group hides the per-name Blocked badges.
- A call takes at most 64 names. That is only a safety limit because storage returns
  at most 50 names per device so a group never gets near it. When a group would need
  more the header buttons are disabled with a note on the button.
- A list holds at most 100 names (the global block list and a profile's websites). A
  call that would go over is refused whole with `The global block list can hold 100
  names. Remove some first.` or `A profile can list 100 websites. Remove some first.`
  and the dashboard shows that text. Names that are already listed do not count again.
- Each card keeps the profile picked in its select across refreshes (a pre-existing
  defect that reset the select on every re-render). The remembered choice falls back
  to the assigned profile and then the first profile when its profile is deleted.
- A third-party name with no group of its own (for example `tags-eu.tiqcdn.com`)
  is not merged into an app by guesswork. It keeps its own row and the "seen with"
  hint from piece 3.
- No manual regrouping in this version.

**Tests.** `group_domain` for Netflix across two registrable domains and
ImmoScout24 across several subdomains including `s24-media` and a `co.uk` style
suffix and a single-name group. Every entry of the two suffix tables keeps two sites
apart (the tests walk the real tables and a written-out list so dropping an entry
fails). Real Amazon hostnames keep their customer. System and iot names group by their
label and cdn and tracker names by their registrable domain. The endpoints with a `domains` list: the bound of 64 and
duplicates and atomicity and one invalid name and the 100 name caps. Node tests of the
pure group functions and of the real `renderObservations` and click handlers on a small
fake DOM: collapsed by default and counts and order and open state surviving a
re-render and the toggle and the action buttons being siblings in Tab order and their
names and states and request bodies and the visible note for a disabled block button. The fixture seed (`JITTERSIM_BROWSER_TEST_OBSERVATIONS=1`)
holds the seven ImmoScout24 names and the two Netflix names for a look in a browser.
There is no Playwright journey for this piece.

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

A compact block between the navigation and the footer. It is hidden until the
admin token is accepted. Editing or clearing the token hides it and makes it
forget everything it knew so a new check can never show data from before.

**Inputs.** Five inputs that the page already loads: the runtime state from
`/api/state` (with the `running` flag that says whether the scheduler keeps
applying changes) and the general profile and the connected clients and the saved
profiles and the service lists. No new endpoint. Every 5 seconds the page reads
the connected clients and the general profile and the runtime state. The state is
not read while the tab is hidden or a network action is busy. An answer that was
asked for before a network action started is dropped. A failed Apply or Reset
reads the state once so the block and the runtime line catch up. A reset from
another tab or the command line shows on the next poll.

**Loading.** Until all five inputs have arrived the block shows one muted line
`Loading…` instead of the three lines. A request that fails keeps that line and
the failed panel shows its own error.

**General profile line.**

| Situation | Text |
| --- | --- |
| none saved | `General profile: none` |
| running and the state is `applied` and the server says active | `General profile: Bad test · active` (green) |
| running and the state is `unknown` (a saved change is settling) | `General profile: Bad test · active` (green) |
| the state is `reset` | `General profile: Bad test · paused` |
| anything else including a stale active flag | `General profile: Bad test · saved, not applied` |

**Devices line.** The first matching row wins.

| Situation | Text |
| --- | --- |
| the state is `reset` | `Profiles off` |
| the state is `applying` or `resetting` | `Changing…` |
| the state is `error` | `Last network action failed` |
| the scheduler is not running | `Not started yet` |
| the device list is unavailable (the scan failed or the Wi-Fi association status is unavailable) | `Connected devices unavailable` |
| running with the general profile in effect | `N device affected` or `N devices affected` or `No connected device is affected` |
| running without a general profile | `N device affected` or `N devices affected` or `No device has its own profile` or `No connected device is affected` |

With the general profile in effect the list is every connected client that is
verified and not protected. Without it the list is the connected clients that
have a saved device and are not protected. Each shows its group profile or else its
own profile. `No device has its own profile` is used when clients are connected
and none of them has a saved device. `No connected device is affected` is used
when nobody is connected or every client with a saved device is protected.
Protected clients are never listed.

Only a list of devices can be opened. It is a `<details>` that shows five names
with their profile names and `+N more` and scrolls inside the block above 8rem.
Every other text is a plain line with nothing to toggle.

**Service lists line.** `Service lists: A · B (every unprotected device)` when the
scheduler is running and the state is `applied` or `unknown` and a list is enabled.

Laptop layout first. At 1280 by 800 the collapsed block is 49 px high with two
lines. The phone layout stays on the later follow-up.

**Tests.** Node tests run the real loaders with a fake page and check what the
block shows when each input arrives last and while one is missing and when the
device list is unavailable for each of its two causes and after locking. They also
cover the state poll and the guards and the failed action. A browser journey
signs in and applies a general profile and adds a service list and protects the
client and resets from outside the page.

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
queue: the device name (piece 1) and direction (upload first) and a verdict chip and
what is set and what the kernel queue did in the last minute.

- Loss: `set 5% · actual 4.7% (31 of 660 packets dropped)`. A queue with duplicates
  adds `(may read low because duplicated packets are counted)`. A queue that drops
  without any loss setting reads `Dropped (queue full at the speed cap) · actual 10%
  (10 of 100 packets dropped)`.
- Speed cap: `set 2 Mbit/s · using 727 kbit/s · peak 2 Mbit/s · cap reached`. The
  average divides by the time between the first and last sample. The peak is the
  fastest pair of neighbouring samples. The cap counts as reached when any interval
  with traffic reaches 90% of the limit so a burst is never averaged away.
- Queue wait: `Queue wait now: about 320 ms` only for a queue that has a speed cap and
  no delay and no jitter (a delay alone also fills the backlog so it would mislead).
  It reads the backlog of the last sample and is hidden below 10 ms.
- Not measured: delay and jitter and reorder and duplicate and corrupt show as
  `Delay: set 100 ms ± 20 · configured, not measured` and
  `Reorder: set 25% · configured, not measured` and so on.
- Verdict chips:
  - `Affected in the last minute`: drops grew or the cap was reached.
  - `No traffic in the last minute`: no packets were offered.
  - `Too few packets to judge`: under 50 packets offered. For a queue with loss also
    while the packets times the loss percentage is below 3.
  - `No drops or cap hit seen`: the queue has loss or a speed cap and enough packets.
  - `Effect not measured here`: the queue has neither loss nor a cap (only delay or
    jitter or reorder or duplicate or corrupt). The row says how many packets passed
    in the last minute.
  - `Collecting…`: fewer than two samples so far.
- Protected clients are listed on one muted line (`Protected (not affected): <names>`)
  and have no row.

**Design.**

- New read-only helper action `queue-stats`: `tc -s -j qdisc show dev <iface>` on
  the uplink and test interfaces. The helper joins every queue of the plan it last
  applied to the kernel queue with the handle and parent that `queue_handle(mark)`
  and `queue_parent(mark)` of the renderer give (one source for the renderer and the
  join). Each entry holds `{device_id, direction, interface, handle, kind,
  impairment, packets, bytes, drops, overlimits, requeues, backlog_bytes, qlen,
  generation}`. The client validates every field like `traffic_counters`.
- The action takes the helper's operation lock like an apply. An apply in progress
  answers `helper-busy` so the numbers never mix an old plan with new kernel queues.
- `generation` is a boot nonce plus a count of applied plans (also counted when the
  helper adopts the published plan after a restart). It changes on every apply even
  when the settings stay the same.
- New `GET /api/impact` (admin token). The server adds the device name and the kind
  label and the names of protected devices. General and service list and quota and
  assignment queues are labelled by kind and id (for example `General profile` and
  `Service list streaming`). It answers `available: false` with a message for no
  helper and a reset network and an old helper. Any other unreadable answer
  (a busy helper and so on) is `available: false` with `paused: true`. The time is the
  server clock as a float like the Traffic card.
- The browser polls every 5 seconds while Diagnostics is visible and the tab is
  visible. It keeps 13 samples per queue (60 seconds). A queue's history restarts
  when a counter goes backwards or the generation or the device or the setting
  changed or the clock stepped back.
- A paused or failed update keeps the rows and the badge says `Update paused`. The
  rows are cleared after a minute without a good answer. The history is cleared at
  once for a reset network and no helper and an old helper and a cleared token.
- The rows are updated in place by `interface/handle` so a text selection survives. The
  list is not a live region. One hidden `role="status"` line announces a verdict
  change (for example `Fixture phone upload: Affected in the last minute`) and a new
  state message once.
- Old helper: the card says the helper needs updating and shows nothing else.

**Tests.** Parser and validator unit tests including hostile output and every limit.
The join with every queue kind and every mark up to the limit of 15 (including the
hexadecimal bands `10:a` and `10:10`) rendered by `render_command_plan`. A real-kernel
Docker test in the style of the existing `tests/integration/linux_*` checks: apply 20%
loss and send packets then assert that drops track about 20% and grow. The same
script joins a plan with 15 devices against the real `tc -j` output. Mutation checks on the join and
the counter restart rule and the generation. Node tests for each verdict and the
bursty cap and the restart cases and the fake-DOM tests for in-place updates and the
status line and the paused state. One browser journey.

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

# Implementation plan: follow private address changes (2026-10-17)

Status: reviewed plan, implemented on branch `feat/follow-address-changes`. The
"Plan review" section at the end lists what the review changed.

## Summary for review

Phones and laptops use private Wi-Fi addresses (MAC addresses with the
"locally administered" bit set). They can change when a network is forgotten,
when the device rotates it, or when private addressing is switched on. JitterSim
ties a device's profile, name, protection and dashboard access to that address.
After a change the device shows up as a new unknown client and quietly escapes
its profile, or a protected device loses its protection.

This change adds three things:

1. **Detection.** When a new private address joins the test Wi-Fi and a saved
   device is absent, JitterSim compares the evidence the Pi already has: the
   DHCP host name, the saved IPv4 address, the time the old address was last
   seen and the saved name. Every rule is simple and is shown to the owner.
2. **A unique match only.** A suggestion appears only when exactly one saved
   device matches the new address and that saved device matches no other new
   address. Two equal candidates give no suggestion at all.
3. **One setting, three modes.** "Follow private address changes" is Off, Ask
   (the default) or Automatic. Ask shows "This looks like Dad's phone (new
   private address). Move its settings?" with Yes, No and Not this device.
   Automatic moves only the profile assignment and the name, and only on strong
   evidence. Protection and dashboard access are never moved automatically.
   Every move is written to a visible history and can be undone for 7 days.

Nothing changes in the Panic-pinned files (`networking/helper.py`,
`panic_guard.py`, `networking/access.py`). No new helper action is added. The
profile move is the same database change as editing the device on the Devices
page, and the protection move calls the existing `move_device_address` code with
its own undo. Panic and Reset therefore see a moved device exactly as they see
a device the owner edited by hand.

## What already exists

| Piece | Where | What it does |
| --- | --- | --- |
| Verified self-service move | `ControlServer.move_device_address_for_peer`, `/api/dashboard-access/move` | The device itself, on its new address, takes over protection and its saved dashboard grant. The helper proves the address from the socket peer. Undo with `/api/dashboard-access/move-undo`. |
| Protection move core | `ControlServer.move_device_address(old, new, access_moved=...)` and `undo_device_address_move` | Moves protection and ad blocking in one transaction, records an `address_moves` row, validates the plan, syncs protected access, and undoes only what it owns. |
| IP follow | `RuntimeScheduler._follow_device_addresses`, `plan_address_moves` | Every 30 s a saved device follows its MAC to a new IPv4 address. It never changes the MAC. |
| Device edit | `ConfigRepository.save_device(device, expected=...)` | Optimistic update that refuses a stale edit or a MAC that another saved device uses. |
| Names | `connected_device_labels` (by MAC) | Owner-given name and type of a client. |

The gap: nothing notices a changed MAC, and nothing moves a saved device's
profile assignment to a new MAC except a manual edit.

## Decisions (recommended defaults, owner may change)

- **D1 Mode default: Ask.** Off disables detection and stores nothing new.
  Automatic is opt-in.
- **D2 Only a private new address starts a check.** The new MAC must have the
  locally administered bit (second hex digit 2, 6, A or E). The old address may
  be fixed or private, because switching private addressing on is a common
  cause.
- **D3 Signals: only what the Pi already reads** (table below). Client-id
  (option 61), vendor class (60) and the parameter request list (55) are not
  available: the helper returns only the host name of each lease, and the helper
  is Panic-pinned. They are a later follow-up that needs a helper change and a
  new Panic hash.
- **D4 Scoring points and thresholds** as in "Scoring rules". Ask needs 3 points
  with at least one identity signal. Automatic needs a distinctive host name
  plus either the same IPv4 address or matching timing.
- **D5 Automatic moves the profile assignment and the name only.** Data limits,
  groups, website rules and schedules follow because they belong to the saved
  device ID, not the MAC.
- **D6 Ask's Yes also moves protection** (with ad blocking) when the saved
  device was protected, after a confirm dialog that names the risk. The owner
  can already protect any address by hand, so this gives no new power.
  Protection brings dashboard reachability with it, and the dialog says so. A
  saved dashboard access grant is never moved from a suggestion. The card says to open the dashboard
  on that device and use the existing verified move.
- **D7 Undo window: 7 days.** After that the history entry stays readable but
  Undo is gone. Undo refuses when the device was edited since.
- **D8 Rate limit and no flapping.** Automatic moves one saved device at most
  once per 10 minutes and 3 times per 24 hours. A new address must have been
  connected for at least 60 seconds. Beyond the limit the suggestion falls back
  to Ask. Automatic never moves a protected device: its profile is dormant and
  moving only the profile would start shaping it.
- **D9 Absent means not in a current, verified station list.** When the helper
  cannot say which stations are associated (`association_status_available` is
  false) or the list is a stale copy, nothing is suggested and nothing is
  recorded. A saved device last seen more than 30 days ago is not offered by
  timing, and its host name memory is deleted.
- **D10 Dismissals.** No hides that pair for 24 hours. Not this device hides
  that pair for 90 days. At most 256 dismissals are kept.
- **D11 Privacy and retention.** Host names are personal data. The Pi keeps the
  last host name, IPv4 address and last-seen time only for addresses that belong
  to a saved device, for at most 30 days after they were last seen. Pairs seen
  online together are kept as a veto for 30 days (addresses only). History is
  kept for 90 days. Beyond 100 entries only entries whose Undo has run out are
  removed; when 100 entries are still undoable a new move is refused. The
  history keeps reason codes such as "hostname", never the host name itself. Nothing here is in the settings
  export, import or version history (like `address_moves`, it is runtime
  evidence). The database backup copies the whole database and so includes it.
  Off clears the stored host names.
- **D12 No automatic moves while the network is stopped.** The scheduler does
  not run its address check after Reset or Panic. Ask still works, because it
  only edits saved settings, like any other edit while stopped.
- **D13 Where it shows.** A card "Address changes" on the Devices page above
  Connected clients, and one line in the sidebar status when a suggestion waits.

## Evidence that exists in the code

| Signal | Source today | Used | Notes |
| --- | --- | --- | --- |
| DHCP host name (option 12) | dnsmasq lease file via `list_connected_leases`, returned as `hostname` per station by the helper `clients` action | Yes | Main signal. Generic names such as "iPhone" count little. |
| Client-id (61) | 5th lease column, parsed but dropped by the helper | No | Needs a helper change (Panic-pinned). |
| Vendor class (60), request list (55) | Not in the lease file | No | Would need dnsmasq scripting. |
| mDNS or NetBIOS name | Not collected for clients | No | Would need a listener. |
| IPv4 continuity | Saved device's `ipv4_address` and the last seen IPv4 | Yes, only when a reachable neighbour entry confirms the new address and no other station claims it | A DHCP lease or an old neighbour entry can be stale, as the existing IP follow already knows. |
| Association time | `connected_seconds` from `iw station dump` | Yes | Join time is now minus connected seconds. |
| Last seen of the old MAC | New small table, written by the address check and by the dashboard while it is open | Yes | Gives "left shortly before the new one joined". |
| Seen online together | New small table of address pairs | Yes | Two addresses connected at the same time are two devices. The veto survives reconnects. |
| Saved name | Device name, label or protection name | Yes | Compared with the host name after removing punctuation. |
| OUI vendor | MAC prefix | No | A private address has no vendor prefix by definition. |
| Signal strength | `signal_dbm` | No | Changes with position. Too noisy to identify a device. |

## Scoring rules

Points for one saved device (old) and one new private address (new):

| Rule | Points |
| --- | --- |
| H1: the host names are equal (ignoring case) and the name is distinctive | 4 |
| H2: the host names are equal but generic (for example "iPhone", "android-1a2b3c4d", "localhost") | 1 |
| N: the new host name is distinctive and matches the saved name after removing spaces and punctuation | 2 |
| I: the new, confirmed IPv4 address is the old one's saved or last seen IPv4 address | 2 |
| T: the old address was last seen at most 15 minutes before the new one joined | 1 |
| X: the old address was seen more than 90 seconds after the new one joined | rules the pair out |
| V: both addresses were in one station list at some point in the last 30 days | rules the pair out |

- Ask threshold: at least 3 points and at least one of H1, H2, N or I. Timing
  alone never suggests anything. A protected saved device also needs H1 or N,
  because Yes would move its protection.
- Strong (Automatic): H1 and at least one of I or T, the new address connected
  for 60 seconds, the rate limit allows it and the saved device has a profile
  assignment.
- No guess: a suggestion needs exactly one saved device over the threshold for
  that new address, and that saved device must be over the threshold for no
  other new address.

Examples:

| Situation | Points | Result |
| --- | --- | --- |
| "Dads-iPhone" again, same IPv4, joined 2 min after the old one left | 4 + 2 + 1 = 7 | Strong. Automatic moves the profile, Ask asks. |
| "Dads-iPhone" again, new IPv4, no timing data | 4 | Ask. |
| "iPhone" again, same IPv4 | 1 + 2 = 3 | Ask only. Never automatic. |
| No host name, same IPv4, good timing | 2 + 1 = 3 | Ask only. |
| No host name, new IPv4, good timing | 1 | Nothing. |
| Two saved devices both called "iPhone", one new "iPhone" | equal | Nothing (ambiguous). |
| Old address still online | X | Nothing. |

## What may move automatically

| Item | Keyed by | Automatic | Ask (Yes) | Why |
| --- | --- | --- | --- | --- |
| Profile assignment (saved device) | device MAC and IPv4 | Yes, strong evidence only | Yes | A wrong move shapes one stranger and the owner sees it in the history with Undo. |
| Data limits, groups, website rules, schedules | saved device ID | Follow the device | Follow the device | Nothing is copied. They belong to the device ID. |
| Name and type | MAC label | Yes, with the profile | Yes | Wording only. |
| Protection and ad blocking | MAC | Never | Yes, after a confirm dialog | A wrong move exempts a stranger from every profile. A copied host name is enough to fake the evidence. |
| Protection's dashboard access | follows protection | Never | Only through protection, and the confirm dialog says so | Protecting a device also lets it open the dashboard (the admin token is still needed). A wrong move gives a stranger that network path too. |
| Saved dashboard access grant | MAC, in the helper | Never | Never | A wrong move gives a stranger the network path to the admin page. Only the verified self-service move on the device itself may move it. |
| MAC allow or deny list | MAC | Never | Never | It is a household access rule. The card says when the old address is on it. |

## Edge cases

- **The old address comes back.** The pair is ruled out while both are online. If
  a move already happened, the old address is now a new client: when it is
  private and matches, a suggestion to move back appears under the same rules.
  Undo is the quicker way and stays available for 7 days.
- **Two devices change at the same time.** Each new address is matched on its own.
  Distinct host names give two suggestions. Equal host names give none.
- **A device that is really new.** No saved device matches, nothing shows.
- **Guests.** A guest phone with a generic host name can reach the Ask threshold
  only together with the same IPv4 address. It is never moved automatically.
- **A saved device that never comes back.** Suggestions exist only while the new
  address is connected. Host name memory ends 30 days after the old address was
  last seen.
- **Rotation every few minutes.** The 10-minute and 3-per-day limits stop
  automatic moves and fall back to Ask. The history shows each move.
- **No DHCP host name.** Only IPv4 and timing remain, at most 3 points, so only
  Ask.
- **The device was edited meanwhile.** The move uses `save_device(expected=...)`
  and refuses a stale edit. Undo compares the device and the name with the exact
  values saved after the move (the IPv4 address may differ, because the address
  check moves it) and refuses when anything else changed or a later move touched
  the same device or addresses. After an Undo the same pair is not suggested for
  90 days.
- **Settings import or restore.** An import replaces saved devices. A pending
  suggestion is checked again at the moment of Yes, so it cannot act on an old
  device.

## User interface

- Devices page, new card "Address changes" above Connected clients:
  - a select "Follow private address changes" with Off, Ask and Automatic and a
    Save button. Automatic asks for confirmation once and says what it never
    moves;
  - one row per suggestion: the sentence, the evidence in plain words, what Yes
    moves and what stays manual, and the buttons Yes, No and Not this device.
    Yes opens the confirm dialog from PR 141;
  - a history list (newest first) with Undo where it is still allowed.
- Sidebar status: one line "1 device has a new address" that links to the card.
- Results are spoken by the single announcer from PR 146. The card re-renders
  only when its data changed and never while focus is inside it, so focus stays
  put. All code is in a new `address-follow.js` assembled into the page: no
  inline script or style, so the strict CSP stays as it is.

## Data model and migration

New tables, created with `CREATE TABLE IF NOT EXISTS` (an older release ignores
them, a newer one creates them on first use):

- `address_follow_settings (id = 1, mode TEXT CHECK (mode IN ('off', 'ask', 'automatic')))`,
  missing row means Ask.
- `address_sightings (mac_address PRIMARY KEY, hostname, ipv4_address, last_seen)`,
  only for MACs of saved devices, labels or protection.
- `address_follow_history (id, happened_at, mode, device_id, name, old_mac,
  new_mac, device_before, device_after, label_after, protection_move_id,
  evidence, undone_at)`. `device_*` and `label_after` are the exact saved
  values, `protection_move_id` links the `address_moves` row of the existing
  protection move, `evidence` holds reason codes only.
- `address_follow_dismissals (old_mac, new_mac, until, PRIMARY KEY (old_mac, new_mac))`.
- `address_follow_vetoes (old_mac, new_mac, seen_at, PRIMARY KEY (old_mac, new_mac))`,
  at most 1024 rows.

The code lives in new modules: `engine/address_follow.py` (pure scoring and
matching), `config/address_follow.py` (the store), and `api/address_follow.py`
(routes). `server.py` only adds the route list and two dispatch calls.
`runtime.py` calls the check from `_follow_device_addresses` before the IP
follow, so a moved device gets its new IPv4 address in the same tick.

How a move runs:

- The profile move changes only the saved device's MAC address, with
  `save_device(expected=...)`, in one transaction with the name, the history
  entry and the activity log line. The existing IP follow then moves the IPv4
  address with its grouped rules (swaps, traffic progression baselines).
- Yes on a protected device runs the existing `move_device_address` with a hook
  (`also`) that makes the profile move, the name move and the history entry
  inside its own transaction, before it validates the plan. Everything commits
  together or nothing does, and the 15-rule limit is checked on the final state.
  Undo works the same way through `undo_device_address_move`.
- Undo also puts the IPv4 address back (with the existing grouped
  `move_device_addresses`), because rules follow the IPv4 address and the
  rejected device would otherwise keep the profile.
- An automatic move commits inside the scheduler's `publish_if_current` with the
  tick's generation, so a Panic either comes first and stops it or waits the few
  milliseconds until the commit is done.

## API

- `GET /api/address-follow`: mode, suggestions, history.
- `POST /api/address-follow/settings` `{"mode": "off" | "ask" | "automatic"}`.
- `POST /api/address-follow/accept` `{"old_mac", "new_mac", "move_protection": bool}`.
  The server recomputes the suggestion and refuses when it no longer exists.
- `POST /api/address-follow/dismiss` `{"old_mac", "new_mac", "scope": "later" | "never"}`.
- `POST /api/address-follow/undo` `{"id": <history id>}`.

All need the admin token like every other write.

## Tests

- Unit: generic host names, private bit, every scoring rule, the overlap rule,
  ambiguity in both directions, every edge case above, rate limit and the
  60-second stability rule.
- Property tests (hypothesis): for any set of saved devices and arrivals, every
  suggestion is a unique mutual match, and duplicating a saved device's evidence
  removes its suggestions.
- Store and action tests on a real database: profile move, label move, stale
  edit refusal, undo, undo refusal after an edit, retention pruning, Off clears
  host names, dismissals.
- API tests: token required, accept, accept with protection through the
  existing move and its undo, dismiss, settings, a stale suggestion is refused.
- Scheduler test: Automatic moves the profile and the IP follow moves the IPv4
  address in the same tick. Reset stops automatic moves.
- Browser journey `tests/browser/address_follow.py`: suggestion shows, No,
  Yes with the confirm dialog, the history entry and Undo, phone width.
- Kernel test: not needed. No packet rule code changes. A moved device compiles
  through the same plan as an edited one. The kernel gate still runs.

## Risks

- **A copied host name.** Anyone on the test Wi-Fi can send any host name. That
  is why protection and dashboard access never move automatically. A copied
  name could move a profile onto a stranger; the history shows it and Undo
  reverses it.
- **Generic name list.** It is a heuristic. A missed generic name gives 4 points
  where 1 was right. Automatic also needs IPv4 or timing.
- **Clock jumps.** Timing uses the Pi clock. A jump gives or takes one point and
  cannot alone cause a move.
- **Owner decisions:** D1 (default Ask), D6 (Ask moves protection after a
  confirm), D7 (7-day undo), D8 (rate limits).

## Plan review (Sol, 2026-10-17)

One independent review of this plan found ten points. All were taken:

1. Moving protection and the profile must not leave a half move: protection
   moves first, the profile second, both under the exclusive lock, and a failed
   profile move puts protection back with the existing Undo.
2. Unknown observations: a station list without association status or a stale
   copy suggests nothing. The IPv4 point needs a reachable neighbour entry.
3. Panic fence: the automatic commit asks the scheduler's fence again.
4. Undo stores exact before and after values and the protection move ID, and
   refuses stale or superseded moves.
5. IPv4 changes go through the existing grouped IP follow, not `save_device`.
6. A generic host name never also counts as the saved name.
7. Pairs seen online together are vetoed across reconnects.
8. The confirm dialog says that protection brings dashboard reachability.
9. History retention never drops an undoable entry and refuses new moves
   instead. The existing `address_moves` limit of 64 still applies to protection
   moves and is reported as their error.
10. No host name in the history. Observation also runs while the dashboard page
    is open, so Ask works while the network is stopped.

## Diff review (Sol, 2026-10-17)

The full-diff review found eight points. All were fixed in one round: Undo
restores the IPv4 address (S1), protection and profile commit in one
transaction in both directions (S1, and the rule-limit point), the automatic
commit runs under the Panic fence, the live warnings of the protection move are
passed on, Off is checked again inside the observation transaction, expired data
is pruned hourly even without a station list, and every undoable entry stays in
the history list.

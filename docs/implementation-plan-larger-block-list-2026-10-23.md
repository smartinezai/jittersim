# A larger global block list

Date: 23 October 2026. Owner request: "the global block list can only hold 100 names: too
little. Make it much larger (a few thousand) without making nft sets or the dashboard slow.
Measure, keep the validation and the Panic path safe."

A second owner request of the same day ("protected devices should not get affected by globally
blocked sites") is answered in the last section. It is a finding and a design, not part of
this change.

## Decision

- The global block list holds **2000 names** and at most **80000 characters** of names in
  total (40 on average, about 1.5 times the typical blocked name). Both are named constants
  in `src/jittersim/config/models.py` (`MAX_GLOBAL_BLOCKED_DOMAINS`,
  `MAX_GLOBAL_BLOCKED_CHARACTERS`). The API error, the resolver cache size and the tests use
  them. Profile website lists, the global allow list, quotas and service rules keep 100.
- The address layer blocks at most **4096 resolved addresses**
  (`MAX_GLOBAL_BLOCKED_ADDRESSES` in `engine/website.py`). Above that it keeps the lowest
  4096 so the set does not flap and logs a warning. The DNS layer still refuses every listed
  name while household access rules are on.
- The website name cache grows from 512 names to the list limit plus 2048, and a plan uses
  an expired answer (at most 10 minutes past its normal 60 seconds) while a background
  lookup renews it. Without this a long list stalls every scheduler pass (measured below).
- A save of the access rules (`/api/policies`, **Block globally**) now checks the whole
  household access policy before it is stored, so a list that would be refused by the helper
  is refused with its reason at save time instead of "saved but could not be updated".
- `/api/policies` accepts a body up to the access policy size (256 KiB) instead of 64 KiB:
  2000 typical names alone are 63 KiB of JSON.
- No pinned file changes. No helper change is needed (see "Helper frame").
- The dashboard needs no change: the list is one textarea (measured below).

Why 2000 and not 3000 or 5000: the helper frame (256 KiB, pinned) carries the names once and
the resolved addresses once. 2000 typical names leave about 60 KiB of room after the address
cap. The first lookup of every name has to finish inside one 10-second plan deadline with 8
lookup threads: 2000 names at 20 ms each take about 6 seconds, 3000 take 9. And with 2000
names, 8 distinct household DNS views still fit the existing 16384 view limit (below).

## How a blocked name is enforced

1. **Storage and validation.** `WebsitePolicy.blocked_domains` (config/models.py) is
   normalized (`normalize_domain`: lower case, valid labels, at most 253 characters,
   duplicates removed). The API adds names through `/api/policies` (the textarea on the Access
   and rules page) or `/api/observations/add-global-block` (**Block globally**, up to 64 names
   a call). The remote CLI uses the second route.
2. **DNS (main enforcement while household access rules are on).** `build_access_plan`
   (engine/access.py) puts the list into the access plan. The helper (`access-apply` frame, or
   inside the `apply` frame) writes an Unbound configuration (`render_dns` in the pinned
   networking/access.py) with one `local-zone "name." always_nxdomain` per name in each DNS
   view, and redirects test Wi-Fi DNS to it. Names under a listed name are refused too. The
   optional ad-blocking resolver for protected devices reuses the same views.
3. **Addresses (always while JitterSim runs).** Each scheduler pass (about once a second)
   and each Apply resolves every listed name (`resolve_domains` in engine/website.py: a 60 s
   cache, 8 threads, one 10 s deadline per plan) and passes the addresses as
   `global_blocked_addresses`. The renderer writes one rule
   `ip daddr { a, b, ... } drop` in the experiment table (an anonymous nft set). The browser
   proxy also refuses those addresses and checks host names with `permits_host`.

## Limits per layer before this change

| Layer | Limit | Where |
| --- | --- | --- |
| API and model validation | 100 names (shared with every domain list) | `MAX_LISTED_DOMAINS`, models.py |
| Block globally call | 64 names a call | `MAX_BATCH_DOMAINS`, server.py |
| Request body | 64 KiB | `MAX_JSON_BODY_BYTES`, server.py |
| Access policy | 256 KiB of JSON, 256 clients | `MAX_ACCESS_BYTES`, engine/access.py |
| DNS views | 16384 names summed over all distinct views | `MAX_EXPANDED_DOMAINS`, engine/access.py |
| Helper frame | 256 KiB a request line | `MAX_MESSAGE_BYTES`, networking/helper.py (pinned) |
| Name cache | 512 names, 60 s | `_MAX_CACHED_DOMAINS`, engine/website.py |
| Lookups | 8 threads, 32 queued, 10 s per plan | engine/website.py, engine/lookup_pool.py |
| nft set | none (anonymous set in one rule) | renderer.py |
| Dashboard | none (one textarea) | dashboard.html |

## Measurements

Mac (Apple silicon, Python 3.12) unless stated. A Raspberry Pi 3B+ runs Python about 10 times
slower, so multiply the millisecond columns by about 10 for the Pi. A throwaway script
(not committed) used names like `ads12.tag-301.example.com`, 29 characters on
average, 2 addresses a name and 15 devices.

| Names | Validate ms | Access JSON KiB | Access check ms | Unbound config KiB | render_dns ms | Compile ms | Apply frame KiB | Decode ms | Render ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 100 | 0.1 | 4 | 0.2 | 7 | 0.1 | 0.1 | 32 | 0.8 | 0.4 |
| 1000 | 0.7 | 33 | 1.9 | 62 | 1.3 | 1.0 | 85 | 3.8 | 0.9 |
| 2000 | 1.4 | 66 | 4.2 | 124 | 3.0 | 1.8 | 147 | 7.3 | 1.4 |
| 3000 | 2.0 | 98 | 6.7 | 186 | 4.6 | 2.8 | 210 | 11.0 | 2.0 |
| 5000 | 3.4 | 164 | 12.8 | 310 | 9.1 | 4.8 | 335 | 19.7 | 3.2 |
| 10000 | 6.7 | 327 | 26.3 | 620 | 19.6 | 9.9 | 646 | 41.4 | 6.1 |

The apply frame passes the helper's 256 KiB at about 3600 typical names with 2 addresses each.
CPU time is never the problem: even 10000 names cost under half a second on a Pi.

**nft (kernel test image, colima VM).** One rule with an anonymous set of N IPv4 addresses:

| Addresses | First load ms | Reload ms | Delete table ms |
| --- | --- | --- | --- |
| 200 | 34 | 16 | 14 |
| 2000 | 14 | 15 | 12 |
| 4096 | 16 | 17 | 8 |
| 6000 | 23 | 21 | 14 |
| 10000 | 23 | 33 | 10 |
| 20000 | 46 | 42 | 12 |

Kernel memory moved by 0.4 to 4 MB for up to 10000 elements (the reading is noisy). A Pi is
slower but the load stays well under a second and the Panic delete under 0.1 s.

**Unbound (kernel test image).** Configuration from the real `render_dns`:

| Names | Views | Config KiB | unbound-checkconf ms | Resident memory MB |
| --- | --- | --- | --- | --- |
| 100 | 1 | 7 | 151 (first run) | 13 |
| 2000 | 1 | 124 | 10 | 14 |
| 2000 | 8 | 993 | 18 | 20 |
| 16000 | 1 | 1004 | 25 | 20 |
| 2000 | 32 | 3969 | 87 | 41 |

Start-up to the first answer was 17 s for every size, so it does not depend on the list.
`MAX_EXPANDED_DOMAINS` (16384) keeps the resolver near 20 MB. It stays as it is.

**Address lookups.** A throwaway script runs one plan a second against a fake
resolver with 20 ms a lookup and a cache lifetime scaled down to 6 s (20 passes):

| Names | Cache | First pass s | Slowest later pass s | Later passes over 1 s |
| --- | --- | --- | --- | --- |
| 100 | 512 | 0.30 | 0.30 | 0 of 19 |
| 600 | 512 | 1.83 | 1.83 | 19 of 19 (the cache is too small, every pass looks up everything) |
| 1000 | 512 | 3.01 | 2.98 | 19 of 19 |
| 2000 | 512 | 5.91 | 5.97 | 19 of 19 |
| 1000 | 8192 | 2.94 | 2.96 | 3 of 19 (every name expires at once) |
| 2000 | 8192 | 5.91 | 5.93 | 9 of 19 |
| 3000 | 8192 | 8.85 | 8.86 | 19 of 19 |

So a larger cache alone is not enough: when the answers expire together each pass waits for
all of them again. With the stale answer used during renewal (this change) only the first
pass waits. The numbers after the change are in the "After" section.

**Dashboard (Chromium).** The list is one textarea. Filling it and the layout: 5 ms for 2000
names, 24 ms for 10000. Splitting and building the JSON body: under 2 ms. Body size: 63 KiB
for 2000 names (over the old 64 KiB limit with the other fields), 160 KiB for 5000. No
rendering change is needed. The DNS observation list marks blocked names on the server with
a set lookup.

**Browser proxy.** `permits_host` compares a host with every blocked name: 0.37 ms for 2000
names, 1.1 ms for 5000 (about 4 and 11 ms on a Pi) once per new proxied connection.
Acceptable. Not changed.

## Helper frame

The helper limits a request line to 256 KiB (`MAX_MESSAGE_BYTES` in the pinned helper.py).
The `apply` frame carries the access plan (the names) and `global_blocked_addresses`. With
2000 names of at most 80000 characters (88 KiB of JSON), 4096 addresses (70 KiB) and the rest
of a large plan the frame is 174 KiB (measured). A unit test
(tests/unit/test_global_block_list.py) builds that worst case, with 15 devices, 100 allowed
names and 200 denied MAC addresses, and checks it stays under three quarters of
`MAX_MESSAGE_BYTES`. A frame that is still too large is
refused by the dashboard side before it is sent ("helper request is too large"), so nothing
is half applied. Panic and Reset frames are small and do not carry the list. No helper change
is needed.

Pre-existing and left as it is: the access policy alone may be up to 256 KiB (for example 512
site-rule names of 253 characters each) and then the apply frame can pass 256 KiB whatever the
block list holds. This change does not make that worse beyond the 88 KiB the block list may
now take.

## Validation

- Same name rules (`normalize_domain`), duplicates removed, count and character limits with
  messages that state the limits: "blocked_domains must contain no more than 2000 domains",
  "blocked_domains must contain no more than 80000 characters in total", and for **Block
  globally** "The global block list can hold 2000 names. Remove some first."
- The whole household access policy is built (`build_access_plan`) inside the save
  transaction, so a list that makes too many DNS view combinations or too large a policy is
  refused before it is stored.

## Panic and Reset

Panic (`normal-access`) and Reset delete the experiment table (with its set) and release the
household rules with an open policy (no DNS views). Neither carries nor renders the list. A
kernel lab test applies a plan with 2000 names and 4096 blocked addresses, checks that the set
and the DNS views are in the kernel, then runs Panic and Reset and checks that nothing is left
within `PANIC_ANSWER_SECONDS`.

## Risks

- First lookups: a list of 2000 names that are all new takes about 6 s at 20 ms a lookup and
  more on a slow DNS server. Names that miss the 10 s deadline are logged and added on a later
  pass. The DNS layer blocks them meanwhile when household rules are on.
- Stale answers: an address may be blocked up to 10 minutes after the name moved. Only blocks
  stay a little longer. Allow-list and quota names use the same cache and also keep their old
  addresses during a renewal, as they did during the 60 s cache life.
- Renewals share the 32 queued lookups with new names. While many renewals wait, a new
  name can miss one pass and is tried again 30 s later. DNS refuses it meanwhile when
  household rules are on.
- A plan is built while the control lock is held. The first pass over 2000 new names holds
  it for up to the 10 s lookup deadline, as 100 slow names could before.
- More than 4096 addresses: the highest ones are not blocked by address. The log says so.
- Memory on the Pi: the cache holds up to 4048 names (about 1 MB), Unbound about 20 MB.

## After

The same lookup script with the change (20 ms a lookup, 6 s cache life, 20 passes):

| Names | First pass s | Slowest later pass s | Later passes over 1 s |
| --- | --- | --- | --- |
| 100 | 0.33 | 0.00 | 0 of 19 |
| 1000 | 3.11 | 0.00 | 0 of 19 |
| 2000 | 6.28 | 0.01 | 0 of 19 |

Kernel lab (`test_panic_and_reset_remove_a_full_global_block_list_in_time`, the heavy plan
with 2000 names and 4096 blocked addresses): apply 0.3 to 0.4 s, Panic 0.1 s, Reset under
0.1 s, nothing left afterwards, and the last listed name answers NXDOMAIN while applied.

## Protected devices and the global block list

Owner request (23 Oct): protected devices should not be affected by globally blocked sites.
Read: implementation-plan-protected-device-ad-blocking-2026-10-03.md ("Existing global website
and device access rules retain their current meaning") and
implementation-plan-website-modes-2026-10-04.md ("renderer.py applies global access rules
before protected-device exemptions").

**Finding: protected devices are not exempt today, in any layer.**

1. Addresses: `_render_nft_script` (renderer.py) writes the `block global website
   destinations` drop before the protected devices' `unaffected by profiles` return. A packet
   from a protected device to a blocked address is dropped. The global allow-only rule comes
   before the return too.
2. Household DNS: `render_access_firewall` (pinned access.py) redirects DNS from every test
   Wi-Fi client to the filtered resolver, and `render_dns` puts the whole test network in the
   `everyone` view with every blocked name. Protected devices are not access clients, so they
   get that view.
3. Ad blocking for a protected device: `filtering_config` (networking/adblocking.py) starts
   from `render_dns(access)`, so the household views and the global block list apply before
   the ad list ("Household denies take priority").
4. Browser proxy: `permits_host` and the `global_blocked_addresses` check do not look at
   protection.

The unit test `test_global_block_list_reaches_protected_devices_today` in
tests/unit/test_global_block_list.py shows items 1 to 3 on rendered output. It is meant
to be inverted by the follow-up change.

**Smallest safe design (separate PR).** Direction: a wrong exemption can only un-block.
Protection never moves on its own. Nothing is added to the Panic path.

1. renderer.py (not pinned): move the protected `return` rules in the input and forward
   chains above the global block and global allow-only rules. The MAC denylist stays first
   (it is a household device rule, not a site rule). One kernel lab test: a protected MAC
   reaches a blocked address, an ordinary device does not.
2. adblocking.py (not pinned): build `filtering_config` from the access plan with an empty
   `websites.blocked_domains` (and mode `unrestricted`), so a protected device with ad
   blocking gets only the ad list. The ad resolver serves only protected devices, so nobody
   else is un-blocked.
3. Household DNS for a protected device without ad blocking: needs the pinned
   networking/access.py. Smallest form: in `render_access_firewall`, before the DNS redirect,
   `ether saddr { protected MACs } udp/tcp dport 53 return` so their DNS goes to the shared
   network's resolver as with household rules off. The access plan then has to carry the
   protected MACs (engine/access.py: a bounded tuple, at most 256, validated with
   `normalize_mac_address`). This changes access.py and so needs a new reviewed
   PANIC_APPROVED_SOURCES bundle entry, so it goes in its own PR marked "HOLD until after
   v0.2.0" (coordinator rule of 23 October: a changed pinned hash makes the Pi's installed
   update manager refuse the signed release). Items 1, 2 and 4 do not need it and can ship
   first. The release path is not touched: a release already removes the redirect.
4. browser_proxy.py: skip the global checks for a client whose verified MAC is protected.

Owner decision: should the global allow-only mode also stop applying to protected devices?
The design above exempts them from both global rules because both are "global website
rules", but the request only named blocked sites.

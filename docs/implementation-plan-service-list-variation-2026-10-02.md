# Random variation and random outages for service lists

## Summary

A service list gives chosen apps and sites one profile on every affected
device. Until now a list used only the fixed delay, jitter, loss, speed limit,
reordering, duplication and corruption of its profile. This change lets a list
use the profile's random variation (such as `200 ± 30%`) and its random
outages. The profile's website list stays unused for lists.

**Risk: high.** An outage drops traffic. A rule in the wrong place could cut a
protected device or the Pi's DNS or the dashboard. The tests below pin the
rule order and a real-kernel script checks the packets.

## What changes

- `engine/runtime.py`: the scheduler rolls each enabled list with the same
  `_roll` code that devices, everyone else and the general profile use. The roll
  is kept under the key `servicelist:<list id>`, which no device id can take
  because device ids cannot contain a colon. The general profile mode keeps
  these rolls too.
- `engine/service_lists.py`: `build_service_list_rules` takes an optional roll
  function. Without one it uses the saved profile with no outage. That path is
  used by configuration-only mode and by checks of saved settings.
- `engine/plan.py`: `ServiceListRule` gets `outage_active` (default false).
- `engine/compiler.py` and `engine/codec.py`: carry and check the flag. A saved
  or published plan without the field loads with no outage.
- `networking/renderer.py`: while a list is in an outage, three drop rules join
  the list's rules. They sit after the rules that let protected devices through.
- `networking/browser_proxy.py`: refuses a tunnel to a listed hostname while
  that list is in an outage.
- `networking/helper.py`: announces `service_list_outages`. The dashboard checks
  it before it sends a plan with a list outage because an older helper would
  silently ignore the flag.
- Activity log: `Service list <name>: outage started` and `... outage ended`.
- `GET /api/service-lists` says whether a list is in an outage on the network
  right now and the card shows `random outage now`. The **Schedules and
  limits** page reads the lists again with its other five second updates so the
  note does not go stale.
- The Service lists card note, the profile help for outages and the seed and
  the README section explain the behaviour.

## Behaviour

Random variation:

- A list whose profile has **Reroll every** above 0 gets new values at that
  interval (with **Interval variation**). The values come from the same reroll
  code as devices: value variation and enabled chance for each setting.
- The generator is seeded with the profile's **Random seed** and the list id, so
  the same seed and list repeat the same values in the same order. Two lists
  with the same profile follow different sequences like two devices do.
- All affected devices share the list's values because a list has one upload
  queue and one download queue.
- The first apply uses the saved values. The first new values come after one
  interval. Editing the profile or choosing another profile starts a fresh
  roll. Disabling a list forgets its roll. Reset and Panic forget all rolls.
- New values reach the kernel through the normal scheduled apply: one atomic
  nft batch that also refills the list's address set and `tc qdisc replace`
  that changes the list's queue in place. Shaped connections carry on. Browser
  proxy tunnels close so the browser opens them again with the new values,
  which is what every apply already does.

Random outages:

- With **Outage chance** above 0 the list rolls that chance at each **Outage
  check interval** (or each reroll interval when that is 0). A hit starts an
  outage of **Outage length** (with its variation) for the whole list.
- During the outage every affected device loses the listed services:
  - Upload: forwarded traffic from the test Wi-Fi to the list's learned
    addresses is dropped in the forward hook.
  - Download: the forward hook still gives the list's download marker to
    traffic from those addresses and the egress hook of the test Wi-Fi drops
    it. The egress hook sees the client's MAC address so the protected-device
    return rule runs first.
  - Browser proxy: tunnels to a listed hostname get `503`. The output hook also
    drops any proxy packet that carries the list's mark, over IPv4 and IPv6.
- The input hook is not touched. The Pi's DNS, DHCP, the dashboard and the proxy
  port keep working. The Pi keeps learning the list's addresses during the
  outage. Learned addresses never include private or test-network addresses.
- Devices in **Unaffected by profiles** are never touched: their uploads and
  downloads return before the drop rules and the proxy gives them no mark.
- A list outage wins over everything else for the listed services, including a
  device or general profile that would have let them through. A device's own
  outage still does not cut a listed service.
- The test Wi-Fi gives clients no IPv6 and the learned addresses are IPv4, so
  direct traffic is IPv4. The proxy path is the only one that can reach IPv6
  hosts and it is covered.
- Other sites on the same server address as a listed service are cut too.

## Plan format

`service_list_rules[].outage_active` is written as `true` or `false`. A missing
field reads as `false` so plans published by an older dashboard still load.
Any other value is rejected.

## Risks

- Rule order. Mitigated by renderer tests that compare positions and by the
  kernel script with a protected client.
- The output drop matches a mark. Only the browser proxy sets marks on the Pi's
  own sockets and each list's mark is unique in a plan. The kernel script
  checks that unmarked traffic from the Pi still reaches a listed address.
- A short reroll interval means frequent applies. Each one closes affected proxy
  tunnels, as with devices today.
- A reroll that switches a queue's kind (for example duplication turned on by
  its enabled chance) replaces that queue and drops the packets it holds, as
  with devices today.
- An older helper. Guarded by the capability check.

## Tests

- Engine: reroll timing, seed repeatability, shared values, fresh roll after a
  profile edit, outage start and end, no outage for disabled lists, rolls kept
  in general profile mode, previews do not advance rolls, activity entries.
- Compiler and codec: flag carried, wrong type rejected, old payload loads.
- Renderer: drop rules only during an outage, after the protected returns and
  before the list's marking rules, output drop of the list mark, tc values after
  a reroll.
- Proxy: listed host refused during an outage, protected client unaffected,
  other hosts unaffected.
- Helper: capability, reroll and outage applies keep the learned addresses.
- API and storage: outage state in the listing, saved lists keep working.
- Node: the card shows the outage.
- Real kernel: `tests/integration/linux_service_list_variation.py`.

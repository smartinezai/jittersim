# Implementation plan: a profile for everyone else

## Reviewer summary

Today a profile only affects devices saved in the dashboard. Every other client
on the test Wi-Fi gets a normal connection. This adds the reverse: one profile
for everyone else on the test Wi-Fi, and an Unaffected devices list whose
members keep a normal connection. The list matches MAC addresses, so a device
stays on it when it gets a new IP address. Saved devices keep their own
profiles, schedules and limits. The work also fixes MAC filtering, which
enforced the block list even when set to Disabled.

## Who gets what

The first rule that matches a test Wi-Fi client decides its connection:

1. MAC filtering blocks it: no internet.
2. It is a saved device: its own profile, schedules, timers, progressions and
   limits, as today.
3. It is on the Unaffected devices list: a normal connection.
4. Everyone else: the everyone-else profile when one is chosen, otherwise a
   normal connection, as today.

A MAC address can't be on the list and belong to a saved device at the same
time, so rules 2 and 3 only meet for a saved device stored without its MAC
address. The list only matters while everyone else has a profile. Devices on
the router's Wi-Fi never pass through JitterSim.

## Decisions and risks

- **One shared queue.** Everyone else shares one queue in each direction. It
  uses one of the 15 shaping slots however many clients join. A speed limit is
  shared by the whole group. Delay, jitter, loss, reordering, duplication and
  corruption apply to every packet. One large download fills the shared queue
  and slows the rest of the group, like a congested link.
- **Matching by MAC address.** Upload packets are matched by the sender's MAC
  address, as MAC filtering already does. In the forward hook, download
  packets don't have the client's MAC address yet. So the upload packets of a
  connection from an unaffected client set a conntrack mark, and its download
  packets check that mark. Because of NAT, every forwarded connection starts
  at the client. JitterSim writes and clears only its own mark value. After
  the list changes, an open connection is classified again by its next upload
  packet.
- **Saved devices** are excluded from the everyone-else rules by IPv4
  address, so their marks, counters and limits don't change. The Pi's own
  traffic, such as DHCP, DNS and the browser proxy, is not forwarded and stays
  unmarked.
- **What the profile brings.** Everyone else gets the profile's upload and
  download effects, rerolls and outages. An outage drops everyone else's
  traffic, like a saved device's outage. Website allowlists keep restricting
  only saved devices, as today; this covers the profile's allowlist and the
  global allow-only mode. The global block list already covers every client.
  Schedules, timers, progressions and limits stay per saved device.
- **Capacity.** Choosing an everyone-else profile counts as one of the 15
  shaped devices and rules. It is refused when all 15 are in use. Unaffected
  devices use none. The list holds at most 256 addresses.
- **Overlaps.** Adding a saved device's MAC address to the list is refused
  with the device's name. Saving a device with a listed MAC address is refused
  too.
- **Profile deletes.** A profile used for everyone else can't be deleted. The
  refusal names everyone else.
- **Private Wi-Fi addresses.** Phones may use a different MAC address on each
  network, and some change it over time. The dashboard says to add the address
  shown in Connected clients, and to keep the phone's private address fixed for
  the JitterSim network. A phone whose address changes gets the everyone-else
  profile.
- **Unchanged:** the browser proxy still serves only saved devices, and Live
  traffic still shows only saved devices.
- **MAC filtering fix (pre-existing).** The block list was enforced in every
  mode, including Disabled and Allow only. Each mode now uses only its own
  list, and the conditions panel follows the same rule. A Pi that relied on
  Disabled with a block list stops blocking those addresses.

## Data, plan and API

- **Settings:** `everyone_else_profile_id` and `unaffected_devices` (MAC
  address and name). They live in two new tables created on start, and export
  and import carry them. Older exports import with neither. Import refuses
  missing profiles, overlaps and plans over capacity.
- **Plan:** an optional `everyone_else` rule holding:
  - its mark, placed after the service rules;
  - the unaffected MAC addresses;
  - both impairments;
  - whether an outage is active.

  The codec validates it like the other rules. The stored-plan compiler
  includes it without rerolls or outages, as it does for devices.
- **Rules:** when the list has addresses, two conntrack rules classify
  connections first. Everyone else's upload and download traffic then gets
  the mark. During an outage, it is dropped in the input and forward chains
  instead.
- **API:**
  - `GET` and `POST /api/everyone-else` read and set the profile.
  - `POST /api/unaffected-devices` and `/api/unaffected-devices/delete` change
    the list.
  - Changes apply at once, unless the network is reset.
  - `/api/connected-devices` marks unaffected clients.
  - `/api/conditions` gains an everyone-else entry.

## Dashboard

- **Everyone else card,** next to Devices:
  - a profile menu, where None keeps a normal connection;
  - the Unaffected devices table, with Remove;
  - a form to add a MAC address and an optional name, which defaults to the
    address;
  - the note about private addresses.
- **Connected clients** show what each client gets: Unaffected, its saved
  profile, Everyone else with the profile's name, or Normal connection.
  Clients that aren't saved devices get a Keep unaffected button. Unaffected
  clients get Remove from unaffected in place of the profile menu.
- **Device conditions** gain an Everyone else row.
- **README:** the network section describes the new behavior.

## Work slices

1. MAC filtering fix.
2. Settings, storage, export and import.
3. Plan, compiler, codec and rules, checked on a Linux kernel.
4. Scheduler: rerolls and outages for everyone else.
5. API, conditions and capacity.
6. Dashboard and README.

Each slice is committed separately with its tests.

## Verification and recovery

- Unit tests for each slice, the embedded JavaScript syntax check, and a
  browser check of the card and Connected clients.
- Kernel tests in Docker, with a second test client that has its own MAC
  address. They check that:
  - everyone else is delayed while an unaffected client and a saved device
    with a normal profile are not;
  - an unaffected client keeps a normal connection on a new IP address, and
    when only downloads are delayed;
  - a client removed from the list gets the profile;
  - an outage drops only everyone else;
  - Disabled MAC filtering lets a block-listed address through.
- On the Pi: check that a phone and a laptop on the list keep a normal
  connection while another client gets the profile, including after the
  phone reconnects.
- Choosing None for everyone else returns to today's behavior. Each slice can
  be reverted on its own.

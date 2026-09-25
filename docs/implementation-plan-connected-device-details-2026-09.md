# Connected device details

## Human review summary

JitterSim already shows IPv4 address, MAC address and neighbor state for
clients visible on the test network. It reads Linux's neighbor list every five
seconds. A stale neighbor entry can remain after a device disconnects and the
current list cannot say whether a device is a phone, laptop or another kind of
hardware.

Improve the list using the Pi's Wi-Fi association data for current connections
and local address information for names and IP addresses. Suggest a type when
local information gives a useful clue. Let the owner choose or correct a type
and name. Unknown stays a valid result. Device labels must never change
traffic rules. Do not scan devices, send addresses to an outside service or
record browsing activity for this feature.

**Risk: medium.** The feature only reads network information, but names and
MAC addresses can identify people and devices. Auto-detected type can be wrong
or missing. Keep the dashboard's current admin protection and keep all
identification on the Pi.

**Decisions:** Save names and types on the Pi until removed. Key them to the
Wi-Fi address seen by JitterSim. Offer phone, tablet, laptop, desktop, TV or
console, other and unknown. Private Wi-Fi addresses can change, so a saved
label may need to be entered again.

## Current behavior

1. The dashboard already has a **Connected clients** list with IP, MAC and
   neighbor state. It offers profile assignment to clients.
2. The API asks the privileged helper to read `ip -4 neigh` for `wlan0`. The
   parser keeps IPv4 addresses, MAC addresses and neighbor states. It filters
   failed or incomplete entries but can still show an old `STALE` entry.
3. The dashboard refreshes this list every five seconds. It has no device
   name, manufacturer, model or type field.
4. The shared access point is managed by NetworkManager. Debian 13's
   NetworkManager stores shared-mode DHCP leases under
   `/var/lib/NetworkManager/dnsmasq-<interface>.leases`.

## Proposed behavior

1. Read the Wi-Fi station list from the Pi so the dashboard can distinguish a
   client associated with the AP from a recent neighbor entry. Pair station
   MAC addresses with IP information when available. Show a connected client
   without an IP as connected with its IP listed as unknown.
2. Read a DHCP client name only from NetworkManager's local lease data, if
   available. Treat it as a hint and never as trusted HTML or a command value.
3. Suggest a type only from a clear local name clue. Show **Unknown** when
   there is no clear clue. A manufacturer name does not reliably identify a
   phone versus a laptop, so do not use an outside lookup service.
4. Let the owner save a display name and type for a client. Keep a small list
   of supported categories and allow the owner to remove a saved label.
5. Show only currently associated Wi-Fi clients. Keep the existing profile
   assignment action separate from the type selector. Disable it when the
   client has no IPv4 address or when association status cannot be verified.
6. Do not use a guessed or saved type to select profiles, change traffic or
   grant network access.

## Data and deployment

- Add the `iw` package to the Pi installation and allow one fixed station-list
  command. It reports clients currently connected to the access point.
  The root helper already owns a narrow command boundary. It will read the
  interface-specific lease file directly without invoking a shell.
- Bound lease-file size, station count and text fields. Reject malformed
  addresses, expire old leases and join all details by the currently
  associated station MAC.
- Store only the AP MAC, display name and type in a small SQLite table. Add
  the table idempotently during normal startup. Include saved labels in the
  admin-only list and backup and let the owner remove offline labels. Limit the
  table to 256 labels. Do not save DHCP names or client history.
- Keep the API behind the existing admin token. Do not add a new listening
  port or a route that bypasses the helper boundary.
- If the station command is missing, show recent IP and MAC details with an
  unknown connection status. Disable profile assignment for those rows. Missing
  DHCP data removes only the hostname and type suggestion.

## Verification and release

1. Test parsing and joining station, neighbor and DHCP data including missing,
   duplicate, malformed and recently disconnected client entries.
2. Test type hints for known hostname patterns and for generic names. Ensure
   ambiguous names produce **Unknown** and a label edit cannot change profiles.
3. Test the API response and label save, edit and remove through the existing
   authentication path. Check that a SQLite backup snapshot contains saved
   labels and that label changes do not alter profiles.
4. Run a Pi check with one phone and one laptop. Compare the shown connection
   state and type hint to each device's settings. Repeat with private Wi-Fi
   addresses enabled and with one sleeping device.
5. Confirm list refresh remains bounded and does not issue a command per
   client. Confirm helper or name lookup failure does not affect internet
   forwarding or profile application.

The Pi check requires the live JitterSim access point. Run it after installing
the update during a suitable maintenance window.

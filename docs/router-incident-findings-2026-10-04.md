# Vodafone router outage findings

## Current conclusion

The outage cause remains unproven. Two failures left the router's PHONE light
red and internet access unavailable. The user reports that router Wi-Fi clients
could not connect even after the Pi was unplugged. Restarting the Vodafone
router recovered service. The internet is working now.

The user confirmed that the protected MacBook Air and phone also lost internet
and failed to connect to Vodafone Wi-Fi with its password. The INTERNET light
state is uncertain. This supports a household router failure beyond normal
profile effects on selected Pi clients. It does not identify the trigger.

The installed dashboard repeatedly signs in to the router while a visible tab
has its password. A tested containment change makes router checks manual.
This removes repeated management traffic as a possible trigger. It does not
prove that this traffic caused either failure or that router stability is fixed.

The change is local. Publication and installation remain with the release owner.
No router settings, test profiles or live services were changed in this investigation.

## Verified evidence

- The Pi answers at `192.168.0.17` through the Vodafone management network.
  The Mac route uses `en0` and gateway `192.168.0.1`.
- Trusted SSH works as `jittersim`. The account requires a password for sudo.
  Do not send that password through chat.
- Dashboard, helper and NetworkManager services are active. The public health
  endpoint reports that the helper is reachable. A small HTTPS request from
  the Pi to Vodafone returned 200.
- The wired Pi address is `192.168.0.17/24`. Test Wi-Fi is `192.168.50.1/24`
  with client addresses `.100` through `.199`. IPv6 is disabled on test Wi-Fi.
  DNS is configured to listen at the test Wi-Fi address. No bridge appeared
  in the inspected interfaces. This configuration supports isolation but does
  not replace a physical check for DHCP traffic on the wired connection.
- The available kernel log shows the wired link up. Available memory was
  about 669 MiB and swap use was zero. No memory exhaustion appeared in the
  inspected log. Only the current boot journal exists.
- The current helper log includes a busy-helper response. The saved action
  history includes three failed general-profile updates. These responses
  establish separate application failures but do not establish a router fault.

## Router contact and control paths

`RouterOperations.status` starts a router login when a dashboard status request
arrives after its shared 15-second interval. Successful checks continue while
the tab stays visible. Failed checks wait 30 seconds before another attempt.
Entering remembered credentials and returning to a visible tab can start checks.

The router client uses authenticated session requests even for a settings read.
Router Wi-Fi actions submit settings then check the radio state. Confirmation
does not verify telephone registration or continued internet access.

The modern writer submits primary and guest Wi-Fi fields. The older writer
submits the router's settings form. Neither inspected path changes telephone
settings directly. Firmware behavior still needs verification before treating
these controls as safe during this incident.

Vodafone's [device guide](https://www.vodafone.de/downloadarea/KIP_TippsundTricks_Kabelrouter.pdf)
says a solid red PHONE light on the Arris TG3442DE means at least one phone line
failed to register. A red INTERNET light means an internet error. The PHONE
light alone cannot identify why browsing and Wi-Fi association failed.

## Saved sequence and profile

Times below are Berlin time on 4 October. Saved action times are recorded in UTC
and have been converted. They are not exact timestamps of the two router failures.

| Time | Recorded action | Result |
| --- | --- | --- |
| 00:12:50 | Copy router Wi-Fi to the Pi | 200 |
| 00:13:54 | Change router Wi-Fi | 200 |
| 00:25:14 | Update the general profile | 502 |
| 00:26:37 | Update the general profile | 502 |
| 00:27:34 | Reset network | 200 |
| 00:27:45 | Update the general profile | 502 |
| 00:29:53 | Apply settings | 200 |
| 00:39:50 | Reset Pi Wi-Fi name | 200 |

The selected profile is `daily climb manual` with id `low`. Upload settings
include 6% packet corruption, 7% duplication and a 9,672 kbit rate. Download
settings include 2% corruption, 3% duplication and a 3,906 kbit rate. Settings
are rerolled around a 160-second interval. Explicit outages are disabled.

Read-only validation and offline compilation on the Pi succeeded. The saved
profile creates two traffic queues and 13 Pi commands. No router control command
is generated. Upload selection applies to traffic forwarded from test Wi-Fi
to the wired interface. The Pi's own unselected traffic uses an ordinary queue.
This does not establish how the Vodafone firmware handles impaired traffic.

## Containment and verification

- Ordinary status reads now return an existing result without router contact.
- An explicit check can start one router operation. Other tabs share its limits.
- Credential changes and visibility changes do not schedule router checks.
- An explicit check waits for its result with a time limit and a maximum of
  45 local result reads. A failed check schedules no recurring retry.
- Expired state prevents a Wi-Fi change even when the button has not redrawn.
- Changing credentials during a pending check leaves manual recovery available.
- Router passwords stay out of logs and evidence documents.

The original repair on base f22d9041 passed all 2,187 repository tests on Python 3.12.13.
The configured lint selection and the whitespace check passed. An adapted copy
of the actual installed dashboard passed 38 served-browser scenarios, JavaScript
syntax checks and Python compilation. Changed files also passed a Python 3.9
syntax check. This is not a complete run of every CI Python version. Browser tests
run the actual served JavaScript with controlled responses. They do not contact
the household router.

## Installation and remaining investigation

The installed dashboard has additional changes beyond this checkout. Preserve
those changes when integrating. Exact executable source identities and read-only
copies are included in the handoff. The readable `/opt/jittersim/src` copy does
not establish which files the service imports.

The installed dashboard restores its last explicit apply or scheduled action
when it restarts. The inspected saved state was `applied` with last action
`schedule` and configuration version 45. A dashboard restart can therefore
apply the suspect profile again. Resolve this state and establish recovery
before installation. Preserve a recovery copy and verify the source actually
loaded after restart. A source rollback cannot repair a router already stuck.

Until containment is installed, close credential-filled dashboard tabs or clear
their router password. Pause router Wi-Fi actions and household corruption tests.
Use distinct Wi-Fi names while diagnosing connection failures.

The signed-in router inspection below supersedes the initial unauthenticated
page read. The physical router label and wired-client behavior during the
outages remain unknown.
Keep router configuration, upstream connectivity and Mac Wi-Fi association as
separate possible causes. Do not deliberately reproduce a household outage.
Use an isolated test network for further impairment acceptance.

## Read-only router inspection

The user authorized access and signed in directly. No password was copied into
the conversation. Status and event pages were read without applying settings,
running diagnostics or restarting equipment. The session was logged out after
the inspection.

- Product name: Vodafone Docsis 3.1. Hardware type and version: 7. The page does
  not identify the exact model printed on the router label.
- Firmware: 01.05.069.14.EURO.PC20. Full build:
  AR01.05.069.14_070726_735.PC20.10.VF.
- At the router's displayed time 02:03 on 4 October, uptime was 51 minutes.
  Both WPA2 Wi-Fi bands and the firewall were on. The provisioned phone line
  reported Operational. The unused second line had no number and was not registered.
- All 26 downstream cable channels reported lock YES. All four upstream
  channels reported ranging SUCCESS. Downstream power ranged from -9.6 to
  8.1 dBmV and signal readings from 32 to 43 dB. Upstream transmit power ranged
  from 39.3 to 44.8 dBmV. These are current readings after recovery. They do
  not establish the signal quality or registration state during either outage.
- The event table displayed 97 rows, all Wi-Fi events. The newest was
  01:33:20. The earliest current-date entry was 01:14:24. Some earlier entries
  carried 2022 dates. Treat those timestamps as unreliable without further evidence.
- The table included eight previous-authentication-invalid entries, two
  class-two-frame entries, three class-three-frame entries and two inactivity
  disconnections. These are router-reported reasons. They do not prove that
  the user supplied an incorrect password or identify the failure's cause.
- At 01:14:58 the table recorded CLIENTTOOL setting
  Device.WiFi.AccessPoint.2.X_CISCO_COM_KickAssocDevices to true. The sender
  is not identified. No inspected JitterSim client code names that field.

The uptime implies a recovery reboot around 01:12. The valid-date displayed
events appear later. No pre-recovery failure log was available on this page.
The router log therefore does not establish why its PHONE light became red.

## Current release adaptation

The containment change was adapted onto d43f2d1e3f5987dde78d83416c55d3607cc85716
using the separate dashboard HTML and JavaScript files. Existing background
worker limits, request identifiers and recovery cancellation were preserved.
Manual status requests use the existing bounded read mechanism.

The adapted source passed 413 focused tests and the full local suite completed
with 5,274 passed and 40 skipped in 486.19 seconds. Router copy passed at desktop and
375px widths. Phone layout passed at 390px, 360px and 320px with normal and wide
fonts plus 1280px and 1920px desktop checks. Configured lint and whitespace
checks passed. Exact source receipts are kept separately from the original
repair's results. Live installation remains with the release owner.

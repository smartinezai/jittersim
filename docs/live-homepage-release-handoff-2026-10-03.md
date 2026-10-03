# Live homepage release handoff

## Human review summary

The isolated implementation is based on integration
0c8abd4b499900799e0614d2901d2b9eb4078f33 through local merge 151178d.
Samantha directly authorized implementation. The feature is off by default.
It supports only explicit controlled-page actions in a paired extension.

Risk is high because it adds a browser credential and checks live Wi-Fi
identity. Local checks cover applied selection, concurrent protection edits,
revocation, worker limits, verified TLS, the actual extension lifecycle and
conversion with the actual older reader. The installed Chrome trust path,
physical AP identity, packet rules and combined release checks remain open.
No Pi state or remote branch was changed by this implementation.

## Integration ownership

Merge this isolated change normally into the coordinator's current source.
Do not replace surrounding dashboard, identity, polling or update code with
the older branch. Resolve overlaps with the update owner and main reviewer.
Keep reviewed control-handler ordering and authentication fixes.

The update overlap adds a retained compatibility helper and dashboard
ExecStartPre drop-in. Preserve the update owner's stopped-service backup,
current-data rollback and retained trust, signer and manager handling. An old
whole-configuration snapshot is not a substitute for this conversion.
The extension lives outside the Python wheel and must be delivered from the
reviewed release source. The new CI step tests its actual code using the
existing locked Node Playwright dependency.

## Optional listener and trust

The policy listener is disabled unless JITTERSIM_HOMEPAGE_HOST=192.168.50.1
is provided to the dashboard or the matching --homepage-host option is used.
Its fixed production address is 192.168.50.1:8443. Dashboard TLS, an admin
token and a helper client are required. It uses the neutral Wi-Fi certificate
/etc/jittersim/dashboard-wifi.crt with the existing dashboard private key.
Missing certificates, port conflicts or worker creation failure leave the
ordinary dashboard controls available. The installer does not opt in.

The firewall allows TCP 8443 only on the test interface to the AP address.
Household input restrictions keep that narrow exception. The recovery guard
retains only its existing dashboard exception so pairing is unavailable
during recovery. Verify actual Linux rules after combined integration.

Do not enable the listener for use until a real extension fetch verifies the
installed endpoint with normal Chrome trust. No certificate-warning bypass
or OS trust change was used for this implementation. The failed native
chooser receipt is homepage-chrome-trust-failure-2026-10-03.txt.

## Approval and bounds

A policy request uses the successful applied metadata rather than saved
chance. A fresh direct reachable neighbor must also be a current Wi-Fi
station. Routed peers cannot pair. The helper checks current protection
through read-only SQLite then confirms saved version, applied generation,
helper process identity and access state at approval. A changed or busy state
disables the effect. Protected devices never receive a bounce approval.

The browser's one-second lease starts before its request. It does not recall
navigation already issued. Endpoint name resolution checks public addresses
without requesting the page. It does not pin Chrome's subsequent DNS answer.
Active destination quotas and unsupported everyone-else website behavior
disable the effect. Device, group, timer, service-list and general winners
carry their own applied chance. A winning zero remains zero.

Four physical HTTPS workers include TLS handshakes and have a 750 ms input
deadline. Two separate helper workers cannot consume the eight ordinary
control slots. Identity commands share 450 ms and Unix policy requests share
550 ms. Two fixed DNS workers retain admission after a 120 ms caller timeout.
Requests and responses are limited to 8 KiB. There are eight pending codes,
32 credentials and eight pages per code. Policy rate is two requests per
second with a burst of four per session. Interrupted partial thread starts
retain admission until the actual work finishes.

## Compatibility and recovery

Off settings omit the new profile field. Helpers without the new capability
receive ordinary plans without homepage metadata. The effect stays off.

The retained /usr/local/lib/jittersim-profile-compat.py runs with the target
package's Python before its dashboard starts. If that target supports the
field it does nothing. Otherwise it validates every candidate with the target
reader before removing only homepage_bounce_percent. It preserves supported
current settings and every other table. Configuration version advances once.

Before any conversion, the hook makes a consistent SQLite backup at
/var/lib/jittersim/homepage-profile-compat.sqlite with mode 0600. File and
parent-directory writes are synchronized before the current database changes.
A failed backup or failed target validation leaves the database unchanged.
This single fixed backup contains the pre-conversion database and is replaced
only by a later successful conversion attempt. It is a private recovery file.
Conversion is bounded to 10,000 profiles, 64 KiB per settings document and a
15-second backup deadline. The installer and signed updater retain the hook
when restoring an older service unit.

## Release checks still required

- Resolve the current dashboard, helper and update overlap with their owners.
- Run the configured combined Python versions, installed Linux wheel checks,
  browser checks and packet checks on the final source.
- Verify normal installed Chrome trust, real AP peer identity and independent
  management recovery before activation.
- Verify deployment startup, rollback and controlled end-to-end navigation on
  the Pi. Physical three-device acceptance remains with its existing owner.

The central comprehension topic is why current saved protection must be
checked again after peer identity even when the applied plan is unchanged.
It was deferred during the authorized direct implementation and should be
revisited once before the pull request readiness review.

# Current Pi instructions and acceptance limits

The README, Pi deployment guide and acceptance checklist now describe the
combined sidebar Wi-Fi action, neutral address and public certificate authority.
The historical `documentation-status-audit-pr54-pr75-2026-10-03.md` stays pinned
to its original source. This record describes the later reconciliation.

## Sources and decisions

- Trusted HTTPS import: `0245124ebb96013a9d2269a7c0a20161135f3654`.
- Neutral-name import: `a857dd3a76daf07f2c33e5f16736424826863d0d`.
- Reviewed combined restore source: `cc58b78c59807570be0ec5fe005b8756badcaee3`.
- Current certificate and discovery repairs: the exact source copies and hashes
  in the accompanying CA review snapshot. Shared lock integration remains a
  separate final review requirement.

The one sidebar button applies the router's Wi-Fi name and password to the Pi
with preview and confirmation. It does not copy them to the clipboard. Its
physical acceptance needs Ethernet management access and a reconnect check.

The installer prints the public authority fingerprint. A browser's dashboard
certificate has a different fingerprint. The Mac client trusts a verified copy
of the public authority and checks the dashboard certificate against it.
Existing valid authority identity remains through updates and restore.
Damaged or incomplete identity refuses instead of silently replacing it.

Fresh discovery announces the neutral name. Required existing certificate
aliases can stay on Ethernet. Earlier supported home.arpa bookmarks retain DNS
and certificate support without a promise of mDNS for that suffix. Certificates
permit their actual configured network ranges. Those ranges are not all
necessarily private.

The deployment policy permits only a signed release built from the reviewed
official main after required checks finish. The scripts still accept a general
ref option. That option does not prove policy compliance. Local checks cannot
be called passing hosted CI when GitHub has not run them.

A full software restore uses the selected complete recovery point. Later
ordinary profiles, protection settings, saved device access and usage can
revert. Current signing trust, update token, checks against older releases,
certificate identity and Panic state have separate recovery protections.
Back up current ordinary data before testing restore.

## Verification and remaining physical work

The documented SQLite backup command was run against disposable files with a
committed row still in the WAL. The result retained that row, passed quick_check,
created a mode 0600 file and refused an existing destination without changing
its bytes. The accompanying receipt pins the exact tested snippet.

All physical acceptance boxes remain unticked. Automated Chromium checks do
not establish native Chrome or operating-system trust. No live Pi certificate,
OS trust, network, radio or reboot changes were performed for this review.
Physical management recovery is a separate requirement before appliance use.

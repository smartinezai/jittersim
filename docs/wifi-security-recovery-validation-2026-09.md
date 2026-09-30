# Wi-Fi security and name recovery validation

## Review summary

The change requires WPA2 with AES for installation and later Wi-Fi changes.
The dashboard and `sudo jittersim wifi reset` restore the name to `JitterSim`
while preserving the password. Name recovery reads no password and changes only
the installed Pi test connection. Failed activation triggers one restoration
attempt and reports its outcome.

The change is local to `codex/wifi-security-recovery`. It is based on
`94fd4da3cb1f3505248701aea7a95b133b02c73e`. Remote main still matched that
commit at final review on 30 September 2026. The primary checkout was preserved.

## Completed checks

- Complete Python test suite: 760 tests, 29 skipped, passed on Python 3.12.
- Linux network checks: 29 tests passed on Python 3.11 in a clean container.
  The real Linux network smoke check passed.
- Browser journeys passed in headless Chrome. The reset action was checked for
  confirmation, cancellation, pending state, failure, success and unavailable
  hardware mode. Desktop and phone views were inspected.
- A clean container built and installed the package. The installed console
  command and dashboard reset control were checked outside the source folder.
- A disposable Linux container ran real NetworkManager against a harmless
  disconnected fixture profile. Reading a name containing a colon and backslash,
  applying WPA2/AES, keeping the fixture password and restoring the original
  nonsecret settings all passed. No radio activation was claimed from this test.
- Focused recovery tests checked missing profiles, wrong interfaces, repeated
  resets, failed activation, failed restoration, unchanged password and
  unchanged saved experiment state.
- Actual local API and helper sockets checked administrator authentication,
  refused cross-site writes, stopped-dashboard console recovery, unavailable
  helper errors and safe failure messages.
- Concurrent Wi-Fi changes were refused while helper ping remained available.
  Eight idle connections exhausted the worker limit without creating more
  workers. A reset refused to wait behind an active network operation.
- Installer network steps were executed with a stateful NetworkManager fixture.
  Fresh installation and upgrades required WPA2/AES. Upgrades kept existing
  credentials and detected changed security without restarting active Wi-Fi.
  Unsupported security failed without a weaker retry.
- Required lint, focused import and code checks, shell syntax, dashboard
  JavaScript syntax and diff whitespace checks passed.
- Changed Python modules parsed using Python 3.9 syntax. The full Python 3.9
  and 3.13 CI jobs have not run because the branch has not been published.

## Review decisions and remaining limits

Recovery activates the saved profile before checking the resulting settings and
active connection. Every confirmed reset restarts test Wi-Fi. Saved settings
alone do not establish the security of an already-running radio.

Wi-Fi mutations share one guard and refuse competing changes. Helper workers
and request reads are bounded. Other helper operations retain their serialized
handling while ping and Wi-Fi requests can be served independently. Router
requests do not hold the broader network lock.

The new API action uses the existing administrator session, bearer token and
cross-site checks. Failure messages contain no command output or credentials.
Client isolation, management firewall rules and saved access policies remain
outside the reset's scope.

The other feature branch also changes the installer, helper and dashboard.
At final review its remote head was
`daf32e4fc27553fe7c1bdc433779b3f850871478`. Resolve overlapping changes and
verify the combined result before integration or Pi installation.

Physical Pi acceptance remains pending. Check advertised WPA2/AES, client
reconnection, both reset controls, preserved password, reboot persistence and
continued management access. WPA3 is unverified. Keep the router Wi-Fi
available during that test. The dashboard button cannot be reached from the
test network because administration access is intentionally blocked there.

The owner authorized push, merge and Pi deployment after these checks on
30 September 2026. Publish the main change first. Port it separately to
`feat/service-profiles-and-pi-hardening`, verify that combined release and
deploy it with private backups and authenticated acceptance checks.

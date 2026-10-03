# Wi-Fi password repair verification

The configure and router-copy password writes now use private subprocess input.
An exact private read checks the saved password before activation. Name reset
still reads and writes no password. The successful write preserves the profile
UUID, interface, selected security and unrelated settings.

## Source and package

- Base: `0a41c2faf2441c8dadcbd029c8a4458cffd89d9f`.
- Isolated checkout: `jittersim.worktrees/wifi-secret-repair`.
- All 64 files in the built wheel match current source byte for byte.
- Wheel SHA256: `055ef0496c0d0c8353ab256335d32de9ea3fa79da4c30d5ad3d28cfae5fd2023`.
- Proof SHA256: `dfb98be6b4fbb8d09263c55b8f5bcb2d00d274c3d5040ea4976f3aaa91cb0a3e`.

## Real NetworkManager proof

The coordinator allocated one offline disposable Docker fixture at a time.
Retained images provided actual nmcli and NetworkManager 1.42.4 and 1.52.1.
Both versions passed the same `tests/wifi/prove_secret.py` with current source.
The proof refuses to run outside an explicitly enabled Docker container or
when any network interface other than loopback exists. No host networking,
radio, Pi, VM share, DNS setting or unrelated container was changed.

For each of four literal passwords the previous value was different. The actual
production method wrote the new value. A fresh daemon read matched exactly and
a daemon restart retained it. Cases include leading and trailing spaces,
quotes, backslashes, dollar signs, backticks, punctuation and 63 characters.
UUID, interface, autoconnect, AP mode and security settings stayed unchanged.
The single root-owned profile had mode 0600. Process arguments, environment,
editor history and daemon logs contained no password.

Both versions also demonstrated these failure cases:

- A rejected property write followed by save and quit exits zero. The old
  password remains. Exact comparison catches that false success.
- Replacing the fixture's profile directory with a regular file forces a real
  persistent-save failure. A fresh daemon read still returns the old password.
  The actual production method reports a private failure.
- Saving then reaching EOF without quit can persist the new password while
  returning failure. The execution boundary preserves that failure. Consumer
  tests prove the existing backup restores the original settings without
  repeating the secret write.

The initial proof incorrectly rewrote the same value after a raw editor write.
Independent review found that weakness. Final proof removes the raw write and
asserts different old and requested values before every production call.
One earlier setup failed on an overlay directory rename. Copying within the
owned disposable fixture corrected it. EOF was observed as a nonzero exit on
these versions so it is not described as a timeout. Separate real-pipe tests
cover timeout, excessive output and bounded child cleanup.

## Local checks

- Original Wi-Fi source fails nine new regression checks. The reviewed source
  passes the focused tests including editor false success, direct input
  injection, saved-then-interrupted recovery and cleanup failures.
- Focused installed-wheel suite: 40 tests and 45 subtests passed.
- Fatal Python lint checks and whitespace checks passed.
- Source-only full suite: 3245 passed with 39 Linux-only skips and 847 subtests.
  One GUI evidence test failed because the shared environment lacked installed
  package metadata. The final full suite against the fresh installed wheel
  passed 3248 tests and 847 subtests with 39 Linux-only skips in 134.02 seconds.

Raw test logs, real-daemon logs and package evidence are retained under
`test-results/wifi-secret-repair/`. All owned Docker fixtures were removed.

## Review and release boundaries

The main reviewer approved the mechanism before production edits and approved
the final production source, corrected proof and both saved real-daemon logs.
The approved source hashes match the tested wheel. The focused test count and
full installed-wheel pass were read back before freezing the handoff.
The coordinator owns combined-source checks, publication and deployment.
Hosted CI is not labelled passed when GitHub refuses to run it.

Existing clone-based recovery retains its bounded behavior and generates a new
UUID during restoration. The reviewer approved keeping that existing behavior
in this narrow password repair. UUID-preserving restoration is a separate task.

Fresh installation still passes the generated password in nmcli arguments at
`deploy/raspberry-pi/install.sh:132`. That separate path was reported to the
coordinator and is not fixed by this controller repair. No live radio acceptance
or Pi deployment was performed here.

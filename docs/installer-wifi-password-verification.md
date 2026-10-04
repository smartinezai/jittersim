# Initial Wi-Fi setup verification

Initial setup now saves passwords through private input and checks the exact
saved value before accepting the profile. TestNet is the default only for a
missing credentials file and the explicit name reset. Existing credentials,
SSID, profile UUID and security settings survive upgrades.

The source starts from runtime repair `563082b`. Its reviewed password boundary
is unchanged except for the canonical default name. The new installer helpers
use only the standard library from the invoked source directory. They do not
require an importable installed package.

## Safety and recovery

The manual installer takes the existing stable update lock before preflight or
writes. It verifies descriptor 9 against the protected anchor and confirms
exclusive ownership on that inherited open file description. NetworkManager
children receive neither that descriptor nor known password environment
variables. The explicit staging option survives shell reentry. Integration
must retain the update owner's later opt-in configuration block.

A new profile receives its assigned UUID, WPA3 SAE security, AP mode, disabled
automatic connection and a durable incomplete-setup marker in one add operation.
The marker is cleared only after an exact private password check. Ordinary
failure removes only the assigned UUID. Interrupted setup refuses any later
write or activation until management access resolves the incomplete profile.
Cleanup failure remains an error with generic recovery guidance.

Empty, unreadable, symbolic-link or nonregular existing credential files are
refused without replacement. A profile belonging to another interface is
also refused before mutation. This deliberately prevents moving possible
management Wi-Fi. Recovery instructions are in the Pi installation guide.

The source base has the legacy synchronous reset guard. The coordinator must
also apply the same canonical default to the asynchronous reset guard added by
the reconnect repair. A mixed older helper returning JitterSim is rejected with
HTTP 502 after one call rather than reported as a successful TestNet reset.

## Local evidence

- Complete installed-wheel suite: 3,264 passed with 850 subtests in 136.29 seconds.
  The 39 Linux-only skips are recorded separately and are not passes.
- Focused ownership, interrupted setup, bounded input, damaged credentials and
  actual shell reentry checks: 18 passed with 3 subtests.
- Wi-Fi reset browser acceptance passed at desktop and phone sizes. Dashboard
  access acceptance also passed at desktop and phone sizes.
- Fatal Ruff checks, shell syntax and whitespace checks passed.
- The built wheel matches all 64 source package files byte for byte. Its SHA256
  is `78c425131a96c85552bbc35ea637c1ffe995882fd2f1dc331ecf6ed9752c87de`.
- The allocated isolated Linux proof uses actual NetworkManager 1.52.1 and
  Python 3.11.16 with no network or Wi-Fi device. It checks literal password
  saves, restart persistence, process argument and environment privacy,
  history and log privacy, owned-UUID cleanup, interrupted setup markers,
  lock contention and descriptor inheritance. The final extended run passed failed cleanup and confirmed saved profile
  root ownership with mode 0600.

Logs and exact source hashes are retained under
`test-results/wifi-secret-repair/`. The first full installed suite found stale
shell-layout assertions and manual-only steps in the update-step matcher.
Those assertions were updated to the actual helper boundary. A focused run
without local socket permission could not bind its health fixture. The complete
rerun uses the required socket permission. One Docker launch could not see the
temporary bind mount. The allocated retry copies the same fixture source into
the container instead.

Hosted CI is not claimed green. Combined release checks, remote publication,
merge and Pi deployment remain with the release coordinator. No live radio or
Pi changes were made during this proof.

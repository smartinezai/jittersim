# Deployment audit and upgrade repair

## Human review summary

The live Pi runs the service and Pi hardening release at `2a254ae`. Its helper
and dashboard source hashes match that branch. Preserve its current features
and repair the upgrade path on that branch. Main is a separate release with
different features and is not a safe replacement for this installation.

The Pi has an obsolete enabled access recovery service whose command is
missing. Its old Wi-Fi service depends on that failed service. Source files
from older releases also remain after installation because copying merges
directories. Retire the obsolete services without disconnecting the active
Wi-Fi. Replace the installed source folders instead of merging them. Preserve
credentials, the database, the Wi-Fi name and its WPA3 settings.

Risk is high because an incorrect upgrade can stop client internet access or
administration. Keep Ethernet access and a root-private recovery copy. Test
service retirement, repeated upgrades, failure handling and the clean installed
package. Verify the Pi after installation and a controlled reboot. No clients
were associated at the start of this audit. Actual client reconnection and
two-device isolation need real clients and must be reported separately.

The owner explicitly authorized repairs, testing, publication and deployment
on 30 September 2026. Continue within that scope without a further plan pause.
There are no blocking design questions. The learning concept to revisit at
the completion checkpoint is why a healthy process can still have a broken
boot dependency.

## Work

1. Audit current main and the installed release independently. Check source
   hashes, failed services, logs, authentication, saved settings and Wi-Fi state.
2. Add a bounded upgrade step that disables and archives only obsolete
   JitterSim service definitions. Keep the current NetworkManager connection
   running. Refuse to interrupt an update that is already active.
3. Replace installed source folders cleanly after preparing their replacement.
   Retain existing credentials and data. Remove old package build output so
   wheel creation cannot include removed modules.
4. Add regression coverage for these observed failures. Run the full release
   tests, Linux packet checks, browser checks, lint and clean package checks.
   Review timeouts, failure recovery, permissions and repeated execution.
5. Commit and push the tested change to the Pi release branch once. Check CI
   on the exact published commit. Build and transfer that exact release.
6. Back up code, settings, services and NetworkManager files privately on the
   Pi. Install the reviewed release and compare source and installed hashes.
   Check authenticated API reads, apply/reset with an isolated fixture, mDNS,
   DNS forwarding, services and boot persistence. Restore the prior saved
   experiment state after checks.
7. Record the exact deployed commit and test results. State hardware limits
   plainly instead of inferring client behavior from service health.

## Data and recovery

Read existing local settings and service state. Copy existing credentials and
database files only into a root-private recovery folder on the Pi. Never print
tokens, passwords or user browsing data. Keep bearer authentication and the
management firewall. Add no public control endpoint or external data store.

Only retire service definitions omitted by this release. Keep unrelated units
and network connections. Retain retired definitions in a root-private folder
so software rollback can restore them. On any installation failure restore
the full private backup then verify administration and the access point before
continuing. A code rollback alone does not restore service enablement or Wi-Fi
settings.

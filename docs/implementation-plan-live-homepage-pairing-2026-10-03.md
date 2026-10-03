# Live homepage pairing implementation

## Human review summary

Implement the reviewed live channel after Samantha's explicit go-ahead on
3 October. This authorizes implementation and local verification in this
isolated branch. Publishing and live installation stay with the release owner.
The foundation is published integration 0c8abd4b499900799e0614d2901d2b9eb4078f33.

Risk is high. Approval must use the successful applied plan plus current
saved protection and a fresh direct Wi-Fi peer check. Checks must leave
capacity for Stop and Panic. A navigation already issued cannot be recalled.

## Chosen defaults

- Chance is an integer from 0 to 100. Off fields are omitted from saved
  profiles. Compiler metadata records zero as a real winning choice.
- The HTTPS listener is opt-in on the AP address at port 8443. The extension
  has permission only for https://netlab.local:8443. Reuse the configured
  neutral Wi-Fi certificate. Missing trust or listener startup failure disables pairing
  while the ordinary dashboard continues serving controls.
- Four physical HTTPS workers include TLS handshakes. Two physical helper
  identity checks use a separate Unix socket so they cannot fill control
  admission. Identity commands share a 450 ms total deadline. No background
  work loses its slot when the caller cancels.
- Pairing codes last 120 seconds with eight pending codes. Credentials last
  one hour with 32 sessions. Each code names one approved extension ID and
  at most eight explicit controlled HTTPS GET pages. Pairing stays off.
- Current protection and saved version are read with read-only SQLite and
  short lock waits. Recheck protection, plan generation and session validity
  before approval. No saved chance can stand in for applied metadata.
- Reuse proxy rule ordering. Handle device, group, timer, general and service
  list winners. Active destination quotas and unsupported everyone-else
  website behavior disable the effect rather than guess.
- Public names are checked using a fixed bounded resolver. The endpoint sees
  only the enrolled origin. The extension keeps the explicit page path local.
  This supports controlled tests. It does not pin a later browser connection.
- Household input allows only the AP policy port. The recovery transition
  guard does not allow it. Pairing can be unavailable during recovery.
- Before an older package starts, the installer converts the current profile
  JSON to the target package's supported fields and retains a private SQLite
  backup. Conversion is idempotent and preserves other current settings.
  It must be checked using the target reader before services start.

## Work and verification

1. Add focused failing tests for profile compatibility and applied selection.
2. Implement profile storage, editor, compiler and versioned codec metadata.
3. Implement separate helper snapshots and bounded fresh identity checks.
4. Implement bounded pairing state, admin enrollment and HTTPS listener.
5. Build the separate live extension from the delivered reference and test
   pairing, policy reads, Off, rejected credentials and tab cancellation.
6. Test simultaneous protection writes, Apply, Reset and stalled reads.
7. Review the complete diff including privacy, compatibility, limits and
   failure recovery. Run appropriate local workflow checks and save evidence.

## Deployment boundaries

Certificate enrollment, combined update integration and Pi deployment remain
with their owners. Real normal certificate trust, safe independent management
recovery and physical device acceptance are required before live activation.
No live traffic, radio or installed configuration is changed by this work.

## Private dashboard response follow-up

The frozen source review on 3 October found that existing pairing codes are
not cleared by the shared private-data lock hooks. A delayed create or revoke
reply can also write after token or device access changes. The original direct
authorization covers this correction in the isolated checkout.

First reproduce both cases in the served dashboard using a real browser.
Exercise token edits and actual fixture access removal with delayed create,
revoke and error responses. Repeat after sign-in is restored to prove that
a reply cannot enter a new private session.

Use the combined source's admin token revision plus a homepage generation
invalidated through privateDataLockHooks. Clear the code and enrolled inputs.
Guard success, failure, expiry and button cleanup by the current request
owner. Preserve the shared request and sign-in implementations. Run focused
dashboard checks and record both the original failure and corrected source.

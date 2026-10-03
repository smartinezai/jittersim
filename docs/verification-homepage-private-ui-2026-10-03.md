# Homepage private code lifetime correction

Correction aa0d73134cbe6ee12fee46561974a22389a7c9bb clears pairing codes and enrolled inputs when
token or device access changes. Earlier create and revoke replies cannot
restore private output, show an old failure or change newer request controls.
The shared request and sign-in functions are unchanged.

The actual served dashboard reproduced all 36 cases against frozen 64c3e93
before the fix. Those cases now pass at desktop and phone widths. The
fresh regular wheel passes the same 36 checks. The wheel matches all 67
source code and asset files. Its SHA256 is
6d3cbbd80aca61578490c18ac11d677145ca887c46cbd8d477d50e28815bde9b.

The checks use token edits and actual fixture device-access removal. They
hold create and revoke responses until access is lost or restored. Success,
failure, old expiry callbacks and cleanup during a newer pending request
are covered. Multi-line enrollment is checked too. Homepage responses are
deliberately substituted. Wi-Fi identity is simulated by fixture commands.

475 focused Python checks, workflow Ruff, Node syntax and whitespace checks
passed. The new dashboard browser step is registered in CI. Hosted CI and
the newer combined source remain unverified here. The earlier full suite
and 509 installed checks retain their original a80dd59 source scope.

The pairing consumer follows the current combined cleanup pattern from
cc58b78. A local generation advances through privateDataLockHooks. Each
reply checks its captured token revision, accepted private access and
request owner. The same check protects success, errors, expiry and cleanup.

Original failure receipts, corrected checks, commands and hashes are saved
in [the follow-up report](verification-results-homepage-private-ui-2026-10-03.json).
Normal installed Chrome trust and physical Pi verification remain open.
No remote or live state was changed.

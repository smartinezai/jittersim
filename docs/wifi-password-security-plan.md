# Keep Wi-Fi passwords out of process arguments

The configure and router-copy paths currently pass the Wi-Fi password as an
`nmcli` argument. Anyone able to inspect local processes can see it. Replace
only that password write with the existing NetworkManager editor over a pipe.
Keep the profile name, UUID, interface and selected security settings unchanged
by this write. Keep the existing bounded activation and recovery checks.

The main risk is moving the password into editor history or captured output.
The editor will run with home and cache paths pointing to `/dev/null`. Its
output must be discarded at the subprocess boundary. Its input must be omitted
from command representations. A real daemon fixture must prove exact password
storage including spaces and punctuation before production changes begin.

## Proposed steps

1. Verify the mechanism against the official nmcli manual and editor source.
   The manual documents editing an existing profile. The source shows a value
   prompt after `set 802-11-wireless-security.psk` and shows history persistence.
2. Use a disposable container with NetworkManager 1.42.4 from Bookworm and
   1.52.1 from the retained Wi-Fi image. Use no host networking or Wi-Fi device.
   Start a private system bus and daemon with all devices unmanaged. Create an
   inactive AP profile. Send only the password through subprocess input.
3. Prove exact persisted values after daemon restart, unchanged UUID, interface,
   mode and other settings. Check process arguments, history, daemon logs and
   failure output. Do not print fixture passwords in evidence. Record versions.
4. Get the main reviewer to review this mechanism and failure handling before
   source edits. Resolve the identity question below explicitly.
5. Add a narrow sensitive-input execution mode. Hide input from representations,
   discard editor stdout and stderr for this mode and suppress exception chaining that
   could retain input. Set non-secret child home/cache paths to `/dev/null`.
   Preserve ordinary command behavior. Use a fixed editor script consisting of
   the PSK value prompt, the validated printable ASCII password, persistent save
   and quit. Keep the operation timeout bounded. Validate the password again at
   the controller boundary before any command. Reject timeout-as-success for
   sensitive commands. Read the saved PSK privately using `--escape no` and
   compare its exact bytes to the expected value inside the executor. Return
   only success or a generic failure with no captured output. This read has a
   three second timeout and accepts only the expected 8 to 63 character value
   plus the final newline. Reject every other response.
6. Keep non-secret SSID/security writes on the current path. Keep name reset
   entirely free of password reads or writes. Editor failures must enter the
   existing recovery path. Test refusal, failure, timeout and restoration.
7. Run focused Wi-Fi/executor checks and appropriate full local checks. Review
   complete source and tests independently. Freeze source, evidence and commits
   for coordinator integration. No remote publication or live Pi changes here.

## Secret handling

The password is already validated as 8 to 63 printable ASCII characters before
commands run. It stays in helper memory and the pipe to nmcli. NetworkManager
stores it in its normal root-owned profile. This change adds no endpoint,
database, log or persistent secret file. Do not put the password in arguments,
environment variables, shell commands, exceptions or test output. A successful
editor exit alone is insufficient evidence of storage.

## Recovery and open question

The main reviewer approved keeping the existing bounded clone recovery in this
password repair. UUID-preserving recovery is a separate follow-up. The existing
recovery uses a NetworkManager clone then recreates the AP by name
after a failed activation. Official documentation says cloning generates a new
UUID. This already changes UUID during recovery. The password repair must
preserve UUID on success. No current AP control path consumes the original UUID.
Do not claim UUID-preserving rollback from the existing code.

The real Bookworm fixture demonstrated exit zero after a rejected property write
with the old password still stored. Exact private password comparison is a
required check before activation. The same fixture showed that a short PSK can
be saved by nmcli so controller validation must remain independent of it.

An editor failure may leave settings changed if persistent save completed before
the process was interrupted. The existing backup must remain until activation
and verification complete. Restoration makes at most one activation attempt
and preserves the backup when that attempt fails. Repeating the secret write
automatically is unnecessary. Rollback for this repair is reverting the source
commit before deployment rather than making live radio changes in the fixture.

## Primary references

- [Official nmcli manual](https://networkmanager.dev/docs/api/latest/nmcli.html)
- [Bookworm-era editor source](https://raw.githubusercontent.com/NetworkManager/NetworkManager/nm-1-42/src/nmcli/connections.c)

The value prompt preserves literal characters rather than treating the password
as an editor command. Real installed tooling must confirm that property.

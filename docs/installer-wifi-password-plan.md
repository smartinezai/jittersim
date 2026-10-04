# Save the initial Wi-Fi password privately

The runtime password repair is frozen at `563082b`. Fresh installation still
puts the generated password in nmcli process arguments. Replace only the fresh
AP credential creation block with a small standalone Python helper. Keep saved
credentials and security untouched on upgrades. The update owner also assigned the narrow early installer lock block. Do not
change package preparation, backup publication, certificates or restart order.

The helper must work without importing the installed package. It must validate
the incoming password before any NetworkManager command. It must create an
inactive WPA3 profile with a known new UUID then save and check the password
through the reviewed private execution boundary. A failed password write must
remove only that newly owned UUID with a bounded command. Nothing in this helper
activates Wi-Fi. No open AP may be left eligible for automatic connection.

## Ordering and ownership

The update owner confirmed the AP block follows package installation today but
failure and recovery must not require an importable package. Use a standalone
stdlib helper from the currently invoked installer source with system Python
and isolated imports. It does not import runtime Wi-Fi code or the virtual
environment. Standalone rollback restores saved NetworkManager state without
invoking AP creation. Older packages need not contain any new method.

The helper creates a fresh UUID explicitly. All its password writes, reads and
cleanup use that UUID rather than resolving the profile name repeatedly. Existing profiles retain their UUID. The frozen
runtime secret boundary stays
unchanged. The standalone helper independently uses the same audited bounded
pipe behavior because recovery cannot depend on installed package imports.
Creation includes a durable non-secret `connection.stable-id` marker containing
the assigned UUID. Clear it only after the exact private password check. A later
run validates UUID, interface, SAE, AP mode and non-automatic state. If the marker
still exists it refuses before any further modification or activation. Management
access must inspect that incomplete profile. Do not automatically retry a write.
Ordinary failure cleanup deletes only the assigned fresh UUID. If cleanup fails
the profile stays secured, non-automatic and marked incomplete. A failure clearing
the marker remains a failed installation.

A standalone source helper acquires `/var/lib/jittersim-updates/lock` before any
manual installer preflight, apt, source or configuration writes. This is the
same nonblocking exclusive anchor used by `updates.update_lock`. Check the private
administrator-owned directory and regular non-symlink lock file through directory
and file descriptors. Never unlink the anchor. Retain the locked descriptor as
inheritable descriptor 9 while executing the shell installer again. The shell
and AP helper verify the descriptor against the protected anchor and reuse that
same open file description. They never open a second anchor. Busy state fails
before mutation. Update-owner apply/rollback never calls this AP helper so it
cannot recursively acquire the lock. Strip known password environment variables
before launching the lock helper. Root source availability is checked first.

## Secret path

Disable shell tracing before generating or sourcing AP credentials. Immediately after sourcing, move the password to a freshly unset private shell
variable then unset `JITTERSIM_AP_PASSWORD` before even read-only nmcli probes.
Unset the generation variable before assignment and after writing `ap.env` too.
This removes inherited export flags instead of relying on their absence. The
shell's built-in printf sends the password plus one newline directly to the
helper's standard input. Clear the private shell variable after use. The helper
also drops the known password variable from every child environment.
No password appears in a command argument or temporary file added by this repair.
The existing root-owned mode-0600 `ap.env` remains the initial credential store.

Read at most 65 bytes within a three second overall input deadline and require a single final newline with an 8 to 63 byte
printable ASCII password. Preserve spaces and punctuation exactly. Use the
reviewed editor pipe, history suppression, bounded private equality check and
generic error handling. The helper must not print passwords or command output.

## Verification

1. Main reviewer checks this design and post-package ordering before edits. Completed: the reviewer approved the concrete safe-refusal marker policy.
   The update owner assigned the early stable-anchor serialization block.
2. Add failing checks for lock contention, failed/duplicate existence probes,
   interrupted creation and retry refusal, fresh-install password arguments, inherited exported
   credentials, invalid input before profile creation and unchanged upgrades.
3. Exercise the actual helper with real NetworkManager 1.52.1 in an allocated
   offline fixture. Verify fresh UUID/interface/security, exact persisted PSK
   after restart and no argv/environment/history/log exposure. Cover literal
   spaces and punctuation rather than only generated hex passwords.
4. Force real persistent-save failure. Assert only the owned fresh UUID is
   removed, unrelated profiles survive and no activation occurs. Cover failed
   cleanup with an inactive secured profile and a generic failure.
5. Check shell syntax, focused installer/runtime tests and the installed package
   boundary. Main reviewer reviews final source and saved evidence.
6. Freeze one follow-up commit for the coordinator. Combined Linux pipeline
   checks and publication remain with the coordinator. No live installer or
   radio changes here.

## Coordination

- Update owner approved the standalone boundary. It also confirmed a guarded
  post-install package approach could work. Keep the saved standalone design
  independent of package state and do not change preparation ordering.
- Coordinator assigns the next offline Docker slot before any fixture starts.
- Coordinator confirmed `TestNet` for genuinely new `ap.env` files and the
  explicit default-name reset. Existing saved names and credentials remain
  unchanged on upgrade. Update the runtime reset constant and neutral reset
  wording consistently in this separate follow-up commit.

Risk is initial AP creation, secret handling and the explicitly requested
default-name reset contract. Recovery remains
bounded by command deadlines. No endpoint, database or background work is added.

## Review decisions

Damaged existing credentials are refused without replacement. Only an absent
file receives a new password and TestNet. An existing profile assigned to a
different interface is deliberately refused before mutation so setup cannot
move management Wi-Fi. The explicit passwordless staging option survives the
lock launcher and shell reentry. The update owner retains its later opt-in
configuration block.

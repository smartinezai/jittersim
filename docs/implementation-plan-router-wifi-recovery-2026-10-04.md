# Router Wi-Fi recovery repair

## Review summary

Reduce JitterSim operation to delay and jitter on two devices. First prevent
the application from changing Vodafone Wi-Fi without an explicit configuration
choice. Preserve read-only router checks and copying credentials to the Pi.
Add safe, bounded records for router-control requests and their outcomes.
Do not claim that these changes identify the previous shutdown cause.

The installed router-form safety guards are already present. A separate broad
outage-logging branch exists but its Linux reboot proof and release admission
remain incomplete. Do not deploy that branch as an emergency shortcut.

Primary risk: accidentally blocking recovery or breaking credential import.
A second risk is retaining credentials in diagnostic evidence. No password,
token, request body, router response, device label or raw exception may enter
the new records. Router Wi-Fi stays on during installation and acceptance.

## Scope and implementation

1. Start an isolated checkout from the verified installed revision 081b2ab.
   Read the actual API, helper and frontend paths before editing.
2. Reject router Wi-Fi mutations by default at the privileged helper boundary.
   Keep router reads and Pi AP configuration separate. A frontend-only disabled
   button is insufficient. Use a documented explicit opt-in only if required
   for compatibility. No background retry may repeat a rejected write.
3. Record fixed operation categories, requested state, start and outcome,
   elapsed time and clock/boot information. Bound retention and writing cost.
   Records must finish without waiting for browser polling. Logging failure
   must not prevent Reset, Panic or management recovery.
4. Review the existing local logging implementation for reusable pieces.
   Avoid importing its entire shared journal policy without its required proof.
5. Explain the disabled router switch in the UI. Preserve token checks and
   protected-device access. Do not change household profile assignments.

## Review and verification

Treat this as elevated infrastructure and network risk. Check the current PR
template and security requirements in the selected source.

Test direct helper write refusal, API handling, repeated requests, concurrent
requests and read-only/import compatibility. Confirm no credentials enter
records. Exercise log-write failure and retention limits. Run applicable
existing Python, HTTP and browser checks against the exact final revision.
Any shared build or installer change needs a clean Linux build and the
corresponding update and rollback checks.

Before installation, preserve a verified recovery backup and use the guarded
installer. Verify installed file identities and normal-access state. Keep
Vodafone Wi-Fi on and the Pi on JitterSim-Recovery. Verify the disabled switch
through refusal tests without sending a real router mutation. Confirm client
browsing remains normal with effects off.

Do not perform another copied-name cutover or enable target profiles as part
of this patch. A later controlled trial needs its own verified rollback.
Never turn off router Wi-Fi to test the refusal.

## Open questions and review gate

The original router shutdown cause is unknown. Persistent evidence remains
needed before declaring the incident fixed. Determine whether bounded action
history suffices for request evidence or a separate safe local record is
needed. Review the complete helper change and recovery behavior before source
admission. Existing failing or unavailable release gates must be resolved or
reported accurately.

Internal plan review: a default helper-level refusal directly limits the
known dangerous operation. It does not repair unrelated router failures.
Credential import, Reset and offline recovery are explicit compatibility
gates. Broad persistent journald changes are excluded until independently
verified.

The owner approved this summary with "go" before implementation. Deployment
remains subject to verification and the exact Panic-source admission gate.


## Local implementation checkpoint

Default router-write refusal is implemented before gateway inspection or
router login. Only a trusted helper startup option can enable writes. The
dashboard switch stays read only. Read-only checks and Pi credential copying
are preserved. Fixed operation records rotate between two private files
under /var/log/jittersim-router. The maximum combined content is 128 KiB.
No shared journald configuration is changed. Records survive ordinary
file close and application replacement. Abrupt power-loss durability has
not been established.

204 focused checks pass on macOS Python 3.12 and Linux Python 3.13. Desktop
and 375px browser journeys pass. CI error lint, JavaScript syntax, whitespace
and an isolated pinned-dependency wheel build pass. Packaged changed files
match the source bytes. These are scoped checks, not the complete pipeline.

The guarded update tests have 11 refusals because the helper source tuple
is not yet admitted. Do not add its hash merely to make these tests pass.
Independent complete-source review, source admission, complete required
release checks and a fresh recovery backup remain deployment prerequisites.
The live Pi and router were unchanged by implementation. The original router
shutdown cause remains unconfirmed.

The deferred learning concept is why privileged refusal must happen before
router discovery or login. Revisit it after urgent recovery work.

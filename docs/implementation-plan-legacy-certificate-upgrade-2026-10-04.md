# Legacy certificate upgrade

The verified release cannot adopt this Pi's existing self-signed certificate
because the older installer wrote the same valid DNS name twice. Keep the
existing identity bytes and all signature, key matching, lifetime and name
checks. Accept repeated valid DNS entries only for a self-signed legacy
identity with no authority. Count every entry against the existing limit.
Authority-backed identities keep the current duplicate rejection.

Add a real OpenSSL regression for the legacy certificate, its preserved
recovery copy and a mismatched key. Check strict duplicate rejection and the
entry limit. Review the complete change and run the relevant identity and
update suites plus the configured local checks before publishing it.

The Pi has a private checked recovery archive. Its experiment effects are
stopped and a durable stop is recorded. Household rules are retained.
Finish through the guarded installer with the new exact source revision.
Verify installed files, services, authenticated TLS and management access.
Keep physical acceptance separate. Do not change the old certificate or
private key as a workaround. Restore the private recovery copy if the
validated upgrade cannot finish safely.

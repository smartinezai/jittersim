# Router Wi-Fi toggle repair

## Human review summary

Fix the reported Turn off router Wi-Fi failure on the Vodafone firmware. The Pi log confirms that the toggle still searches for an old HTML form. Copy router Wi-Fi already uses the firmware's encrypted settings API successfully.

Use the router's own encrypted write protocol to change only the primary Wi-Fi enabled flags. Preserve the other settings and confirm every supported primary radio after the change. Keep the older form path for older firmware. Return specific safe errors without exposing passwords or router response contents.

This continues the previously authorized router fixes, tests, push and deployment. The owner has requested immediate implementation because they need the app soon. No new design decision is required.

## Evidence and changes

- The installed helper failed at `set_wifi_enabled` before any settings write because `_wifi_form` found no matching form.
- The router's public `wifi_general_data.js` describes the encrypted write endpoint and its field names.
- Read current verified settings first. Check recognized radio flags before posting. Preserve the existing primary Wi-Fi settings and change only their enabled flags plus the master Wi-Fi flag when it is present.
- Use the existing verified login session and the firmware's authenticated encryption envelope. Do not log or retain settings, session material or credentials.
- Confirm the requested state through the encrypted read API. Reject partial, unknown or tampered confirmation. Bound retries, response size and total operation time. Keep enough helper timeout for the complete operation.
- Improve helper and dashboard errors with a fixed set of non-sensitive messages. A failed confirmation must explain that the router may already have changed and must not silently retry the write.

## Verification and delivery

1. Review this repair plan against the actual firmware script before implementation.
2. Add protocol tests with harmless encrypted settings. Check off and on, preservation of unrelated settings, authentication, tampered data, unknown state, partial radio changes and read-back failure. Keep the older form tests.
3. Check that helper/API failures and logs exclude submitted credentials and router contents. Test the operation timeout separately from quick helper reads.
4. Run the full Mac suite, focused lint and fresh Linux package checks. Complete the previously pending credential panel browser checks when Chrome permits control.
5. Commit the router repair separately from the credential panel then push ready commits together once. Build the exact tested release and deploy with the existing installer. Preserve credentials and saved configuration. No database migration or CI build change is required.
6. Verify the installed code and authenticated dashboard. Real router off/on testing can disconnect devices on Vodafone Wi-Fi. Use wired management access and the existing confirmation before turning Wi-Fi off. Record the starting state and restore it after acceptance when appropriate.

## Risks and recovery

The main risk is changing unintended router settings or losing management connectivity. Match the firmware's write fields and keep Ethernet connected to the Pi. Do not guess unsupported fields. Fail before writing when the starting response is invalid. A write timeout can leave the router changed, so report that uncertainty and require a read before another write. A code rollback restores the adapter but cannot restore Wi-Fi radio state. Use the router's local controls over Ethernet to restore Wi-Fi when needed.

All work stays bounded to one router and one operation. Credentials stay in memory only. There are no new endpoints, stored secrets or analytics.

## Review corrections

The repair review is complete. Use only the firmware's writable fields and exclude read-only fields. Preserve guest settings, band steering, split SSID, security, visibility and isolation. Omit all unchanged passphrase fields using the firmware's demonstrated optional-password behavior. Require recognized states for both primary radios and the master flag before writing. Verify each flag separately after writing.

The master flag also stops guest Wi-Fi activity while it is off, although guest configuration is preserved. Treat any failure after sending the write as an uncertain result. Send no second write and do not switch to the older writer after that point. Share one total deadline across login, reads, write, confirmation and logout. Use a fixed set of safe messages through both helper and API.

The user's previously authorized urgent repair scope covers this reported failure. The existing approved work and push/deploy authorization remain active.

The firmware keeps both configured bands enabled with a shared SSID even when the master switch is off. Match that behavior. With split SSIDs preserve the selected bands and change the master switch. Compare the master and each configured band with the submitted values. A disabled master proves both effective radios are off. Refuse enablement when a split configuration has no enabled band. The protocol fixture must reject a shared-SSID write that sets the configured band flags to zero.

## Validation result

295 tests passed on Mac and in a fresh Linux package installation. Linux network smoke and routed protection packet checks passed. Focused lint, the complete served JavaScript syntax check and diff checks passed. The protocol tests exercise encrypted off/on requests, shared and split SSIDs, preserved passwords and other settings, unknown states, partial changes, failed confirmation, helper timing and slow responses.

Real off/on acceptance on this router remains pending installation and a wired management connection. The existing copy action has already succeeded. Do not report the physical toggle as verified from these local tests.

## Deployment acceptance follow-up

The installed modules match release 2840e2f and all authenticated dashboard reads pass. The live network is deliberately reset. Diagnostics still requests removed traffic tables and reports an avoidable helper failure. Use the same reset-state check as live traffic, keep connected-client inspection available and explain why counters are absent. Cover reset and active states without changing the live network configuration. This is a small correction within the authorized bug fixes.

For the physical router test, prepare a Pi-local check with masked password entry. Verify the starting flags before any write. Run a detached bounded off/on check so losing the Mac's Wi-Fi does not stop restoration. Report only verified flags and fixed errors. Restore the exact starting writable settings and confirm them. Do not store the password or router settings on disk. The live test still needs the user's router password through the masked prompt.

The diagnostics correction passes all 297 tests on Mac and in a fresh Linux package installation. The Linux network smoke check and routed packet protection checks pass. The acceptance helper passes harmless encrypted-router checks for success, partial confirmation, an unsuitable starting state and concurrent settings changes. The helper keeps changes made by another administrator and reports any mismatch. Actual router acceptance still needs masked password entry.

## Actual firmware master-state repair

The masked live inspection confirms that `EnableWiFiFunction` is the only missing writable field in the encrypted Wi-Fi read response. All other required fields and radio flags are present. The public firmware reads its master switch through `_ga.isWifiEnabled`. The actual router index page assigns that field to a recognized boolean string.

When the encrypted response omits the master field, read a fresh index page through the same authenticated session. Parse only the exact `_ga.isWifiEnabled` assignment or global object property. Require a recognized state and fail on conflicting values. Share the existing operation deadline. Use the verified master value with the encrypted band settings for payload construction and read-back. Keep strict per-band checks, preserved settings and no repeated write. Add a fixture that omits the master field just like the actual response and test off/on plus absent, unknown and conflicting page states before deployment.

## Master-state repair validation

304 tests passed on Mac and in a fresh Linux package installation. The firmware fixture omits the master field and exercises verified off/on, missing state, conflicting state and failed confirmation. A missing master page cannot select the older writer. The masked acceptance helper also passes success and restoration against this response shape. Internal review found no remaining blocker after the missing-page correction. Actual router off/on and restoration still require the final masked hardware check after installation.

## Actual firmware security-mode repair

The live masked diagnostic confirms that all three security fields are numeric 4. The router's own wifi_general_data.js maps this value to WPA2-Personal before writing. The adapter currently writes the numeric read values unchanged. The router returns an empty HTTP 200 response and the requested off state does not confirm.

Translate recognized numeric security modes to the exact firmware write names for both primary bands and guest settings. Keep recognized write names when already present. Reject unknown modes before any write. Preserve Wi-Fi names, passwords, guest settings and every other existing choice. Do not add a second write or relax confirmation.

Add a fixture that returns numeric read modes but requires names in writes. Verify off and on, preservation and unknown-mode rejection. Run the full Mac suite and a fresh Linux package check. Build the exact tested commit, push the authorized changes and install it with settings backup. Finish the actual authenticated dashboard off/on check with independent restoration. An empty HTTP response remains insufficient evidence of successful change.

This is a narrow continuation of the approved urgent router repair. There are no new stored secrets, endpoints or database changes. The existing installer and settings backups cover code rollback. Router-state recovery remains the independent on action in the hardware check.

The focused plan review requires rejecting booleans and restricting TG3442 security codes to 0/4/5. The fixture keeps reads numeric after named writes so both directions exercise conversion. The hardware preservation check compares fresh numeric read responses before and after restoration. The urgent takeover and existing repair authorization cover this continuation.

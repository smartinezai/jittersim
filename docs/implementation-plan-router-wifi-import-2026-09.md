# Router Wi-Fi import compatibility

## Human review summary

Make **Copy router Wi-Fi to Pi** work with the Vodafone Station firmware reported by the user. The current import reaches the router's Wi-Fi page after login but cannot find the form fields it expects. Keep the operation read-only on the router and update JitterSim's parser to recognize the page shape used by this firmware. Never log or save the router password, Wi-Fi password, SSID values, cookies or full page contents as diagnostics.

The router status page reports firmware `AR01.05.069.14_070726_735.PC20.10.VF` and hardware type 7. It does not display the product name, so the exact model should be confirmed from the device label or a safe page identifier before claiming firmware-specific support. The existing adapter targets Arris TG3442DE. The main risk is changing the wrong submitted controls or copying incomplete credentials into the Pi access point. Tests must show that a failed read leaves the Pi access point unchanged and that an unknown Wi-Fi radio state is never reported as disabled.

**Risk: medium.** The importer reads credentials and changes the Pi access point. It must not change router settings. The existing installer preserves `/etc/jittersim` and `/var/lib/jittersim`, but reinstalling old code does not restore access-point credentials. Before live testing, record the current test-access-point SSID and password in a secure local place. If reconnecting fails, use the dashboard over the wired management connection to restore those values with its manual access-point controls.

**Open questions:** What exact product name is printed on the Vodafone Station label? Can the authenticated Wi-Fi page be inspected with a local diagnostic that returns only field names and page shape, never values or response content?

## Confirmed firmware behavior

The router's public `wifi_general_data.js` reads `php/wifi_general_data.php`. The response contains `wifidata`, an AES-CCM encrypted JSON object authenticated with the label `wifidata`. It exposes `SSID`, `Passphrase`, `SSID5G`, `Passphrase5G` and radio flags. The router builds the settings controls in JavaScript, so parsing the page as a static HTML form cannot read these settings.

Use the firmware's verified encrypted read flow with the login-derived session key and IV. Verify the login nonce with its authentication tag and use the complete nonce in the session header. Keep the existing HTML-form read as a fallback only for older firmware without the data endpoint. The import must not post to the Wi-Fi settings endpoint. Bound response size and request timeouts, reject unverified encrypted data and clear session material when the operation closes. This discovery does not require copying authenticated page contents or password values into diagnostics.

## Current behavior and evidence

1. `TG3442DERouter` requests `/?wifi_general&mid=WifiGeneral` and only recognizes a fixed list of SSID, password and radio-state names.
2. The Pi log reports `router Wi-Fi settings form was not found` after the login step. The import handler calls `configure_access_point` only after `read_wifi_settings` succeeds, so an unknown page should leave the Pi access point untouched.
3. The user-provided status confirms gateway `192.168.0.1`, Wi-Fi enabled and the firmware/hardware values above. It does not show the router product name or the HTML form/API shape.
4. Existing router unit coverage checks basic HTML form parsing and Wi-Fi toggling. It does not cover the newer firmware page or the failure path that must avoid applying incomplete data.
5. The current radio-state parser returns `False` when it finds no recognized state controls. That can report an unknown state as disabled and must be corrected.

## Decisions and work slices

1. Confirm the exact product family and inspect the authenticated Wi-Fi page locally. Any diagnostic must print only allow-listed field names, element types and non-sensitive page identifiers. It must not print values, passwords, tokens, cookies, full HTML or response bodies.
2. Add a sanitized fixture for the observed page shape. Extend the router adapter to read only the controls needed to copy the primary SSID and password. Preserve the current supported page shape if the older form remains in use.
3. Treat radio state as unknown when the page does not expose recognized state controls. Do not report unknown as disabled. The import can proceed when the SSID and password are complete because the Pi copy action does not change the router. Router Wi-Fi enable/disable must still fail safely unless the state controls are recognized and the requested state can be verified.
4. Fail with a clear, safe dashboard message when the page is unsupported or credentials are missing. Keep detailed diagnostics local and allow-listed. Add checks proving logs and returned errors never contain submitted credentials.
5. Add focused tests for both page shapes, missing fields, unknown radio state and the helper path that proves the Pi access point is not reconfigured after a failed read. Update the operator-facing import error only with non-sensitive guidance to use the manual AP settings.

## Data, security and access

The router admin password is submitted to the local helper for this operation. Keep it transient and do not persist or log it. The import reads the router's SSID and Wi-Fi password then passes them to the existing Pi access-point configuration path. Do not log, return in diagnostic details or add analytics for these values. Do not store router page bodies as fixtures. Use a manually sanitized fixture with values replaced by harmless placeholders. The operation must remain a read of router settings and must not change router settings.

NetworkManager stores the configured Pi access-point credentials after a successful import. Before the live test, preserve the previous AP settings securely outside application logs. If the new AP cannot be joined, restore the previous values through the dashboard's manual AP controls over the wired management connection. Reinstalling an old code revision alone does not restore those settings.

No database migration, backfill, new endpoint or external service is needed. The dashboard continues to use its existing authenticated helper endpoint.

## Verification and rollout

- Run focused router and helper tests, then the full test suite, Ruff checks and `git diff --check`.
- Verify old and new sanitized page fixtures, an unknown page, missing credentials, unknown radio state and an import failure that makes no access-point configuration call. Check that captured errors and logs contain no credential values.
- On the Pi, first record the existing AP credentials in a secure local place. Use the existing dashboard import action with the router password entered directly into the password field. Confirm the Pi AP receives the expected SSID and a test client reconnects. Do not copy the password into chat or logs. Confirm the router Wi-Fi settings did not change.
- Review the final diff for credential or response-body logging. Install the tested commit using `deploy/raspberry-pi/install.sh`. There are no CI workflow files in this checkout, so local verification and the Pi check are required.
- If import or client reconnection fails, use the wired management connection and manual AP settings to restore the recorded prior SSID and password. Reinstall the prior code revision if needed, then verify the prior AP is available.

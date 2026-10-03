# Controlled homepage tests

This Manifest V3 extension uses the live JitterSim policy channel. It starts
off and acts only when someone chooses an enrolled page in its own interface.
The only host permission is https://netlab.local:8443/*. It does not inspect
normal browsing or send page paths, history or page contents to the Pi.

## Pair and use

1. The release owner first verifies the installed HTTPS endpoint and enables
   the separate AP listener. Use normal certificate validation.
2. Load this directory as an unpacked extension in an approved test Chrome.
   Open the extension interface and copy its extension ID.
3. Sign into the dashboard. Open **Pair homepage test extension**. Enter that
   extension ID and one to eight controlled HTTPS page URLs with simple paths.
   Choose **Create pairing code**.
4. On the same test Wi-Fi device, enter the code in the extension within two
   minutes. Pairing stays off.
5. Save and Apply the profile's homepage bounce chance. Choose **Enable test
   effect** in the extension. Only its named page buttons request policy.
6. Choose **Off** to cancel pending work. The dashboard can revoke every
   homepage pairing. A rejected credential requires new pairing.

A 100 percent approval chooses the same site's home page. A zero percent,
expired or unavailable approval opens the selected deep page normally.
Closing the dedicated test tab cancels pending work without replacing it.
A navigation already issued cannot be recalled. A worker or browser restart
leaves the effect off. Credentials live in trusted extension session storage
and expire after one hour on the server. Re-pairing replaces that extension's
credential for the verified device.

Page enrollment refuses address literals, private names, URL credentials,
queries, fragments and ambiguous paths. Public DNS verification supports
controlled tests. It does not pin a browser's later connection. Use pages
you control whose GET requests and home pages are safe for this experiment.

## Local checks

Run the Python homepage tests with the repository's test environment.
The command node tests/browser/homepage-live/journey.cjs uses an
extension-capable Chromium. Set JITTERSIM_PLAYWRIGHT_MODULE for a separately
installed Node Playwright and JITTERSIM_BROWSER_EXECUTABLE for an existing
test browser.

The journey tests the actual extension with substituted policy transport.
The Python HTTPS tests verify the real listener with a disposable trusted
certificate. These are separate checks. The failed attempt to automate
Chrome's native certificate chooser is retained in the verification report.
Normally trusted extension fetch on the installed AP remains a release gate.

# Reliable local transport for the extension browser journey

The Linux journey currently opens a Chrome network error page even though the real extension launches the expected test page. Its first extension-created tab navigation produces no Playwright request or route event. Mac passes the same behavior. Replace the route-only document fixture with a real bounded HTTPS server on loopback so the journey checks actual document navigation without external network access.

The application extension and its manifest stay unchanged. Generate one temporary self-signed certificate for example.org using OpenSSL with a ten-second command limit. Start a Node HTTPS server bound only to 127.0.0.1 on a random port. Chromium maps only example.org to this exact loopback address and port. Permit only the generated certificate public-key fingerprint through the certificate exception switch. Keep real HTTPS pairing verification in the existing separate integration suite.

Serve only GET requests for /article and /. Reject other paths and methods. Count successful named-page and home-page reads. Set short server request/header/socket timeouts and a small connection limit. Keep the existing exact URL checks, approved-page checks, policy traffic counts, Off cancellation, close-tab cancellation, rejection and exact manifest assertions. Add an assertion that the opened page contains the controlled server document so a browser error page cannot pass. Require real server receipts for both paths.

Close all browser and server connections plus remove temporary key/certificate/profile files on success and failure. Preserve both failed Linux diagnostics and the passing Mac diagnostic. Run the unchanged-behavior journey on actual bounded Linux and Mac before integrating. Review the complete test-only change independently. Then run the fresh full release pipeline against the combined frozen source.

This changes only substituted document transport. It does not change runtime HTTPS certificate verification, product permissions or deployment configuration. No new package dependency is needed. OpenSSL is already a configured release-test image tool.

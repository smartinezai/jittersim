# Homepage bounce reference

This isolated prototype initiates named test navigations from an extension
page. It uses a disposable loopback fixture with simulated pairing, identity,
protection and service-list/general/device priority. It never contacts the Pi
or observes ordinary browsing. The effect starts off.

Only Article and Notes are enrolled. A fresh valid policy chooses either that
HTML page or its exact origin root. Local Off cancels an outstanding action.
Unknown/protected identity and unavailable, malformed or expired policy remove
the effect. They leave a new ordinary test navigation usable. No action is
repeated when the resulting home page loads.

The extension contains no admin token. Its random read-only reference
credential lives in extension session storage. The fixture's separate harness
credential is used only by the browser journey and never enters the extension.
All credentials disappear with the isolated test run.

The manifest permits the literal loopback host only. Chrome match patterns do
not limit ports so runtime configuration and navigation validation pin the
single disposable origin. There are no wildcard hosts, content scripts,
history permissions, TLS interception or real browsing monitoring.

Run the journey with the existing dependencies:

```sh
JITTERSIM_NODE_EXECUTABLE=/path/to/existing/node \
JITTERSIM_PLAYWRIGHT_MODULE=/path/to/existing/playwright \
JITTERSIM_BROWSER_EXECUTABLE=/path/to/test/chromium \
python tests/browser/homepage_bounce_reference.py
```

The journey loads the actual Manifest V3 extension in a separate temporary
Chromium profile. It checks the extension worker and served fixture navigation.
Startup, lookups, browser actions and cleanup have deadlines. The fixture
listens only on loopback and caps concurrent request handlers at four.

Live policy routes, verified Wi-Fi identity, profile storage and verified HTTPS
pairing remain unimplemented. A simulated loopback identity is not physical
acceptance. Normal-browser forms, downloads, login callbacks, server redirects
and random destinations remain outside this explicit-action slice.
The focused plan records the future delivery requirements.

## Verification on 2 October 2026

The served browser journey passed all 18 checks using the actual Manifest V3
extension in an isolated Google Chrome for Testing profile. It covered 0% and
100%, rule priority, protected/unknown identity, failed and invalid policy,
fresh reads, deadline expiry, local Off during an outstanding request and
absence of a repeated home-page decision. Unsafe and unenrolled actions were
rejected. Full Ruff and JavaScript syntax checks passed.

These results establish the reference flow only. They do not establish a live
Pi identity endpoint, HTTPS pairing or physical device protection. Docker was
not used and the user's Chrome profile and OS permissions were unchanged.

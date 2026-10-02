# Homepage bounce reference

This isolated prototype initiates named test navigations from an extension
page. It uses a disposable loopback fixture with simulated pairing, identity,
protection and service-list/general/device priority. It never contacts the Pi
or observes ordinary browsing. The effect starts off.

Only Article and Notes are enrolled. A fresh valid policy chooses either that
HTML page or its exact origin root. Local Off and closing the dedicated test
tab cancel an outstanding action. Re-pairing discards the old local session
and stays off. A rejected session requires pairing and explicit Enable again.
A worker restart keeps pairing but resets Enable. A browser restart clears
the session credential.
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
listens only on loopback and caps browser request handlers at four. A separate
loopback listener allows one test-runner control request at a time so stalled
browser requests cannot block the runner’s observations. The control secret
never enters the extension. Each listener refuses the other listener’s routes.

Live policy routes, verified Wi-Fi identity, profile storage and verified HTTPS
pairing remain unimplemented. A simulated loopback identity is not physical
acceptance. Normal-browser forms, downloads, login callbacks, server redirects
and random destinations remain outside this explicit-action slice.
The focused plan records the future delivery requirements.

## Initial verification on 2 October 2026

The served browser journey passed all 18 checks using the actual Manifest V3
extension in an isolated Google Chrome for Testing profile. It covered 0% and
100%, rule priority, protected/unknown identity, failed and invalid policy,
fresh reads, deadline expiry, local Off during an outstanding request and
absence of a repeated home-page decision. Unsafe and unenrolled actions were
rejected. Full Ruff and JavaScript syntax checks passed.

These results establish the reference flow only. They do not establish a live
Pi identity endpoint, HTTPS pairing or physical device protection. Docker was
not used and the user's Chrome profile and OS permissions were unchanged.


## Focused review on 2 October 2026

The actual extension passed 32 browser checks after fixing two reproduced
races: Enable during re-pairing and a delayed navigation after test-tab closure.
The journey also checks malformed pairing replies, replayed codes, session
loss, one outstanding action, Off followed by Enable and actual worker and
browser restarts. Eight HTTP checks cover credential deadlines, replacement,
harness isolation, malformed requests and four stalled handlers with recovery.
Full Ruff, JavaScript syntax and diff checks passed.

Run the focused HTTP checks with:

```sh
python -m pytest tests/test_homepage_reference_fixture.py
```

The fixture keeps one session and one pending pairing code. The browser keeps
one dedicated test tab and one pending action. Both request and response JSON
are limited to 8 KiB. Policy lookups have a 750 ms deadline and a maximum
one-second lease measured from the request start. Already issued navigations
cannot be recalled.

The machine-readable results are in
`docs/verification-results-homepage-reference-review-2026-10-02.json`.
Linux DNS packet acceptance still waits for the shared Docker VM. Physical
three-device acceptance still waits for the phones and safe Pi recovery.
These checks remain a local simulation and do not verify the newer integration
or Pi releases. No push or deployment was performed.


## Delivery on current integration

The reviewed reference and DNS acceptance were carried onto integration
`a1da456` without editing another worktree. The DNS dashboard journey now
runs in the Pi browser workflow. A separate five-minute CI job runs the
eight HTTP checks and the actual extension journey. Python and Node use
Playwright 1.63.0. The Node dependency is pinned in the package lock.

The extension journey selects full Chromium so headless CI can load the
extension. See [Playwright extension testing](https://playwright.dev/docs/chrome-extensions).
The existing Linux job collects the extended DNS packet case in a clean
privileged container. It has a ten-minute job limit. This gives that case
an isolated runner while unrelated local Docker containers remain active.

These are reference and DNS acceptance checks. The live helper identity
route and normally trusted HTTPS pairing are not supplied by this experiment.
Keep the original review report as evidence for its exact historical source.
The delivery report records the newer combined source separately.

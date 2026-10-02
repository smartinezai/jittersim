# Dashboard GUI baseline checks

These checks capture the current dashboard with synthetic fixture data. They
do not change a Pi, store real credentials or certify WCAG conformance. The
local axe-core test engine stays outside the installed dashboard wheel.

Use the repository's Python environment with an installed package rather than
an editable install:

```sh
python -m pip install '.[dev]' 'playwright==1.63.0'
python -m playwright install chromium
python -m pytest tests/unit/test_gui_capture.py tests/unit/test_gui_axe_report.py
python -m tests.browser.gui_baseline
python -m tests.browser.a11y_axe
```

On a new Linux host use Playwright's `install --with-deps chromium` command.
The commands need permission to open local sockets and launch Chromium.

Each runner uses a fresh browser context for each of 40 states: nine signed-in
pages plus sign-in at 375×812 and 1440×900 under light and dark browser
preferences. The current dashboard stays dark under both preferences. Page
readiness checks wait for fixture content, including the explicit empty
traffic state when no queues are active.
The known sign-in success toast is dismissed through its button before page
capture so it cannot cover navigation. Unexpected messages remain visible.

The default outputs are `test-results/gui-baseline/` and
`test-results/gui-axe/`. Each directory must be empty or absent. Use `--output`
with a new directory for another run. The manifest records completion, source
commit, whether the checkout has changes, installed package identity, browser
version and each state. The served HTML hash identifies the actual dashboard
content independently of the checkout commit.

Screenshots cover the visible window. Password fields are masked. CSS counts
are defined text scans rather than computed styles. Page and CSS compression
sizes use gzip level 6 with a fixed timestamp. They are estimates of compressed
content size rather than measurements of transferred network bytes. Request
counts cover fixed 15-second windows on the dark phone Devices state and dark
desktop Traffic state. They describe this synthetic fixture and window.

Accessibility reports preserve rule IDs, affected selectors, structural
markup and checks that still need manual inspection. Text and credential
attributes are removed. Existing violations and incomplete manual checks are
observations. A successful run means the full matrix was captured. It does not
mean the dashboard has no accessibility issues.

Broken scans, missing artifacts, unexpected runtime requests, browser errors,
policy errors or exceeded limits fail the runner. Startup is limited to 15
seconds, each state to 30 seconds and each runner to 12 minutes. Output is
capped at 100 MB with at most 40 screenshots. Failure manifests cannot claim
complete results. Browser contexts and the fixture process are cleaned up on
failure too.

The separate GUI baseline CI job runs the report integrity tests and both
browser runners. It uploads available artifacts even on failure and retains
them for seven days. Review screenshots plus the accessibility JSON before
accepting a baseline for the combined release.

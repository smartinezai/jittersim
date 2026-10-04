"""Bounded synthetic dashboard capture shared by test-only GUI runners."""

import gzip
import hashlib
import importlib.metadata
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

PAGES = ("devices", "profiles", "schedules", "router", "traffic", "measurements", "activity",
         "access", "diagnostics")
STATES = tuple(
    {"id": f"{name}-{width}-{scheme}", "page": name, "width": width, "height": height,
     "color_scheme": scheme, "signed_in": name != "sign-in"}
    for width, height in ((375, 812), (1440, 900))
    for scheme in ("light", "dark")
    for name in ("sign-in",) + PAGES
)
KNOWN_ENDPOINTS = frozenset((
    "/ /api/access /api/access-point /api/access-point/reset-name /api/access/apply "
    "/api/access/device /api/access/disable /api/action-history /api/activity "
    "/api/activity/clear /api/activity/latest /api/apply /api/backup /api/conditions "
    "/api/connected-device-labels /api/connected-device-labels/delete /api/connected-devices "
    "/api/connected-devices/apply-profile /api/connected-devices/group /api/dashboard-access "
    "/api/dashboard-access/ /api/dashboard-access/register-self /api/dashboard-access/remove "
    "/api/dashboard-access/status /api/data-limits /api/data-limits/delete "
    "/api/data-limits/reset /api/device-workspace/ /api/devices /api/devices/delete "
    "/api/diagnostics /api/everyone-else /api/export /api/gateway-persistence "
    "/api/general-profile /api/general-profile/stop /api/group-data-limits "
    "/api/group-data-limits/delete /api/group-data-limits/reset /api/groups /api/groups/delete "
    "/api/health /api/history /api/history/preview /api/history/restore /api/impact /api/import "
    "/api/normal-access /api/observations /api/observations/add-global-block "
    "/api/observations/add-to-profile /api/observations/remove-global-block "
    "/api/observations/scan /api/plan /api/policies /api/profiles /api/profiles/delete "
    "/api/progressions /api/progressions/stop /api/random-website-analytics /api/reset "
    "/api/router /api/router/ /api/router/check /api/router/operation /api/router/preview-ap "
    "/api/router/status /api/router/sync-ap /api/router/wifi /api/router/wifi-credentials "
    "/api/scenarios /api/scenarios/delete /api/scenarios/start /api/scenarios/stop "
    "/api/schedules /api/schedules/delete /api/schedules/preview /api/service-assignments "
    "/api/service-assignments/delete /api/service-lists /api/service-lists/delete "
    "/api/service-quotas /api/service-quotas/delete /api/service-quotas/reset /api/site-blocks "
    "/api/site-blocks/delete /api/state /api/system-health /api/test-sessions "
    "/api/test-sessions/ /api/test-sessions/save /api/test-sessions/start /api/time-quotas "
    "/api/time-quotas/ /api/time-quotas/delete /api/time-quotas/reset /api/time-quotas/start "
    "/api/time-quotas/stop /api/timers /api/timers/cancel /api/traffic /api/traffic-settings "
    "/api/unaffected-devices /api/unaffected-devices/delete /api/update-preferences "
    "/api/updates /api/updates/install /api/updates/rollback /api/uplink"
).split())
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
MAX_REPORT_BYTES = 5 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MANIFEST_RESERVE_BYTES = 512 * 1024


class CaptureLimitError(RuntimeError):
    """The test exceeded its declared capture budget."""


class CaptureTimeout(RuntimeError):
    """The fixture or browser did not finish within the capture deadline."""


@contextmanager
def deadline(seconds):
    """Preserve the earlier deadline when a state runs inside the whole run."""
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()

    def expired(_signum, _frame):
        raise CaptureTimeout("Capture deadline exceeded")

    signal.signal(signal.SIGALRM, expired)
    effective = min(seconds, previous_timer[0]) if previous_timer[0] else seconds
    signal.setitimer(signal.ITIMER_REAL, effective)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0]:
            remaining = previous_timer[0] - (time.monotonic() - started)
            if remaining > 0:
                signal.setitimer(signal.ITIMER_REAL, remaining, previous_timer[1])


class ArtifactStore:
    """Write bounded artifact files in one clean directory."""

    def __init__(self, output_dir, max_bytes=MAX_ARTIFACT_BYTES):
        self.root = Path(output_dir)
        self.max_bytes = max_bytes
        if self.root.is_symlink():
            raise CaptureLimitError("Artifact directory is a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        if any(self.root.iterdir()):
            raise CaptureLimitError("Artifact directory must be empty")
        self.bytes_written = 0
        self.files = {}

    def path(self, name):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", name):
            raise CaptureLimitError("Unsafe artifact name")
        path = self.root / name
        if path.is_symlink():
            raise CaptureLimitError("Artifact file is a symlink")
        return path

    def write_bytes(self, name, data):
        path = self.path(name)
        size = len(data)
        previous = self.files.get(name, 0)
        reserve = min(MANIFEST_RESERVE_BYTES, self.max_bytes // 10) if name != "manifest.json" else 0
        if name.endswith(".png") and name not in self.files:
            if sum(key.endswith(".png") for key in self.files) >= 40:
                raise CaptureLimitError("Screenshot count exceeded")
        if self.bytes_written - previous + size > self.max_bytes - reserve:
            raise CaptureLimitError("Artifact byte budget exceeded")
        path.write_bytes(data)
        self.bytes_written += size - previous
        self.files[name] = size
        return name

    def write_json(self, name, data):
        encoded = json.dumps(data, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        if len(encoded) > MAX_REPORT_BYTES:
            raise CaptureLimitError("JSON report exceeds its byte budget")
        return self.write_bytes(name, encoded)

    def check_limits(self):
        for name, expected_size in self.files.items():
            path = self.path(name)
            if not path.is_file() or path.stat().st_size != expected_size:
                raise CaptureLimitError("Artifact is missing or changed")
        total = 0
        for path in self.root.iterdir():
            if path.is_symlink() or not path.is_file():
                raise CaptureLimitError("Unexpected artifact entry")
            total += path.stat().st_size
        if total > self.max_bytes:
            raise CaptureLimitError("Artifact byte budget exceeded")


def validate_completion(records, expected=STATES):
    expected_ids = [state["id"] for state in expected]
    actual_ids = [record["id"] for record in records if record.get("status") == "complete"]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected_ids):
        raise RuntimeError("Capture matrix is incomplete")
    if len(records) != len(expected_ids):
        raise RuntimeError("Capture matrix has unexpected states")


def stop_fixture(process):
    try:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
    finally:
        if process.stdout is not None:
            process.stdout.close()


@contextmanager
def capture_session():
    from playwright.sync_api import sync_playwright

    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("JITTERSIM_BROWSER_TEST_")}
    environment.update(JITTERSIM_BROWSER_TEST_SEED="1", JITTERSIM_BROWSER_TEST_SCHEDULER="1")
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, env=environment, start_new_session=True,
    )
    try:
        with deadline(15), selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise CaptureTimeout("Fixture startup timed out")
            line = process.stdout.readline().strip()
            if not line.isdecimal() or not 1 <= int(line) <= 65535:
                raise RuntimeError("Fixture returned an invalid port")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                yield browser, f"http://127.0.0.1:{int(line)}"
            finally:
                browser.close()
    finally:
        stop_fixture(process)


def request_endpoint(url, base_url):
    parsed = urlsplit(url)
    if (parsed.scheme, parsed.netloc) != (urlsplit(base_url).scheme, urlsplit(base_url).netloc):
        return None
    # Query values and arbitrary text paths are never part of a report.
    if parsed.path in KNOWN_ENDPOINTS:
        return parsed.path
    return "other-local"


@contextmanager
def open_state(browser, base_url, state):
    from playwright.sync_api import expect

    context = browser.new_context(
        viewport={"width": state["width"], "height": state["height"]},
        color_scheme=state["color_scheme"], service_workers="block",
    )
    evidence = {"external_requests": 0, "console_errors": 0, "page_errors": 0,
                "policy_errors": 0, "request_counts": {}}
    counts = Counter()
    try:
        page = context.new_page()
        page.set_default_timeout(10000)
        page.set_default_navigation_timeout(10000)

        def request_seen(request):
            endpoint = request_endpoint(request.url, base_url)
            if endpoint is None:
                evidence["external_requests"] += 1
            else:
                if endpoint not in counts and len(counts) >= 64:
                    raise CaptureLimitError("Request endpoint budget exceeded")
                counts[endpoint] += 1
                if sum(counts.values()) > 10000:
                    raise CaptureLimitError("Request event budget exceeded")

        def console_seen(message):
            if message.type == "error":
                evidence["console_errors"] += 1

        page.on("request", request_seen)
        page.on("console", console_seen)
        page.on("pageerror", lambda _error: evidence.__setitem__(
            "page_errors", evidence["page_errors"] + 1))
        page.add_init_script("""window.__guiPolicyErrors = 0;
            document.addEventListener('securitypolicyviolation', () => {
                window.__guiPolicyErrors += 1;
            });""")
        response = page.goto(base_url)
        if response is None or response.status != 200:
            raise RuntimeError("Fixture dashboard response failed")
        body = response.body()
        if len(body) > MAX_RESPONSE_BYTES:
            raise CaptureLimitError("Dashboard response exceeds measurement limit")
        evidence["served_body_bytes"] = len(body)
        evidence["served_body_sha256"] = hashlib.sha256(body).hexdigest()
        evidence["served_body_gzip6_bytes"] = len(gzip.compress(body, compresslevel=6, mtime=0))
        expect(page.locator("#open-admin-credentials")).to_be_visible()
        expect(page.locator("#private-dashboard")).to_be_hidden()
        if state["signed_in"]:
            page.locator("#open-admin-credentials").click()
            page.locator("#admin-token").fill("browser-test")
            page.locator("#check-admin-token").click()
            expect(page.locator("#private-dashboard")).to_be_visible()
            expect(page.locator("#check-admin-token")).to_be_enabled()
            expect(page.locator("#token-check-status")).to_have_class("token-check-status success")
            expect(page.locator("#token-check-status")).to_have_text(
                "Token accepted. Diagnostics loaded.")
            page.locator("#close-credentials").click()
            # Regular page captures start after the known sign-in success notification.
            # Unexpected messages remain visible so readiness or runtime checks can fail.
            if page.locator("#toast").is_visible():
                expect(page.locator("#toast")).to_have_class("toast success")
                expect(page.locator("#toast-message")).to_have_text(
                    "Token accepted. Diagnostics loaded.")
                page.locator("#toast-dismiss").click()
            expect(page.locator("#toast")).to_be_hidden()
            evidence["sign_in_success_toast_cleared"] = True
            page.locator(f'[data-page-link="{state["page"]}"]').click()
            expect(page.locator(f'#page-{state["page"]}')).to_be_visible()
            expect(page.locator(f'[data-page-link="{state["page"]}"]')).to_have_attribute(
                "aria-current", "page")
            wait_for_content(page, state["page"])
        page.evaluate("window.scrollTo(0, 0)")
        evidence["request_counts"] = counts
        yield page, evidence
        evidence["policy_errors"] = page.evaluate("window.__guiPolicyErrors")
        if any(evidence[key] for key in (
                "external_requests", "console_errors", "page_errors", "policy_errors")):
            raise RuntimeError("Browser runtime evidence contains failures")
    finally:
        context.close()


def wait_for_content(page, name):
    from playwright.sync_api import expect

    checks = {
        "devices": ("#device-table", "Test phone"),
        "profiles": ("#saved-profiles", "Slow connection"),
        "schedules": ("#scenario-list", "Commute"),
        "measurements": ("#measurement-table", "Add result files"),
        "activity": ("#activity-count", "events"),
        "diagnostics": ("#profiles-json", "slow"),
    }
    if name in checks:
        selector, text = checks[name]
        expect(page.locator(selector)).to_contain_text(text)
    elif name == "router":
        expect(page.locator("#uplink-details")).not_to_contain_text("Enter the admin token")
        expect(page.locator("#uplink-details")).not_to_be_empty()
    elif name == "traffic":
        expect(page.locator("#traffic-status")).to_have_text(re.compile(
            r"^(Sampling live traffic every 2 seconds\.|No counters yet\. "
            r"Assign a profile and choose Start\.)$"))
    elif name == "access":
        expect(page.locator("#access-summary")).to_have_attribute(
            "data-state", re.compile(r"^(applied|pending|error)$"))


def current_commit():
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                            timeout=5, check=True)
    value = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise RuntimeError("Unable to identify capture commit")
    return value


def source_identity():
    """Use Git or explicitly supplied container identity without guessing cleanliness."""
    commit = os.environ.get("JITTERSIM_GUI_SOURCE_COMMIT")
    dirty = os.environ.get("JITTERSIM_GUI_SOURCE_DIRTY")
    if commit is not None or dirty is not None:
        if not commit or not re.fullmatch(r"[0-9a-f]{40}", commit) or dirty not in ("0", "1"):
            raise RuntimeError("Container source identity requires a commit and explicit dirty flag")
        return {"commit": commit, "source_dirty": dirty == "1", "source_identity_method": "supplied"}
    commit = current_commit()
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                            timeout=5, check=True)
    return {"commit": commit, "source_dirty": bool(result.stdout), "source_identity_method": "git"}


def run_matrix(output_dir, runner, run_name):
    store = ArtifactStore(output_dir)
    started = time.monotonic()
    records = []
    manifest = {"runner": run_name, "status": "running", "complete": False,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "expected_states": len(STATES), "states": records,
                "limits": {"states": 40, "state_seconds": 30, "total_seconds": 720,
                           "artifact_bytes": store.max_bytes}}
    try:
        manifest.update(source_identity())
        import jittersim.api.server as server_module
        manifest["runtime_package"] = {
            "name": "jittersim", "version": importlib.metadata.version("jittersim"),
            "server_source_sha256": hashlib.sha256(Path(server_module.__file__).read_bytes()).hexdigest(),
            "location_kind": "installed" if "site-packages" in server_module.__file__ else "checkout",
        }
        with deadline(720), capture_session() as (browser, base_url):
            manifest["browser_version"] = browser.version
            for state in STATES:
                record = {**state, "status": "running"}
                records.append(record)
                try:
                    with deadline(30), open_state(browser, base_url, state) as (page, evidence):
                        record["evidence"] = evidence
                        record["result"] = runner(page, state, evidence, store)
                        summary = record["result"]
                        if not isinstance(summary, dict):
                            raise RuntimeError("Capture callback did not return evidence")
                        artifacts = [summary[key] for key in ("artifact", "screenshot", "statistics")
                                     if key in summary]
                        if not artifacts or any(name not in store.files for name in artifacts):
                            raise RuntimeError("Capture callback artifact is missing")
                    record["evidence"] = dict(evidence)
                    record["status"] = "complete"
                    store.check_limits()
                except Exception as error:
                    record["status"] = "failed"
                    record["failure_type"] = type(error).__name__
                    raise
                store.write_json("manifest.json", manifest)
        validate_completion(records)
        manifest.update(status="complete", complete=True)
    except BaseException as error:
        manifest.update(status="failed", complete=False, failure_type=type(error).__name__)
        raise
    finally:
        manifest["elapsed_seconds"] = round(time.monotonic() - started, 3)
        store.write_json("manifest.json", manifest)
    return manifest

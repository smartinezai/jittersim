"""Exercise live traffic chart line identification and interaction in Chromium."""

import os
import re
import selectors
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

HEADERS = {"Authorization": "Bearer browser-test"}
LIMIT = 15000
SERIES = (
    "upload", "download", "isp_upload", "isp_download", "profile_upload", "profile_download"
)
STARTED = time.monotonic()


def start_fixture():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "JITTERSIM_BROWSER_TEST_SCHEDULER": "1"},
    )
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=15):
            process.terminate()
            raise RuntimeError("Traffic chart dashboard fixture did not start")
        return process, int(process.stdout.readline().strip())


def traffic_response(general_active, low_rate=False):
    """Return deterministic counters that produce six separated chart lines."""
    elapsed = max(0.0, time.monotonic() - STARTED)
    upload_bytes = int(elapsed * (200_000 if low_rate else 30_000_000) / 8)
    download_bytes = int(elapsed * (2_000_000 if low_rate else 40_000_000) / 8)
    return {
        "available": True,
        "sampled_at": time.time(),
        "devices": {
            "phone": {
                "upload_bytes": upload_bytes,
                "download_bytes": download_bytes,
                "upload_packets": upload_bytes // 1000,
                "download_packets": download_bytes // 1000,
            }
        },
        "queues": {"phone": {
            "upload_dropped": 0, "download_dropped": 0,
            "upload_queued": 0, "download_queued": 0,
        }},
        "isp_limits": {"upload_kbit": 90000, "download_kbit": 100000},
        "profile_limits_available": True,
        # The upload trace also measures 30 Mbit/s so pointer inspection sees a true overlap.
        "profile_limits": {"phone": {"upload_kbit": 30000, "download_kbit": 5000}},
        "general_profile_limits": (
            {"upload_kbit": 30000, "download_kbit": 5000} if general_active else None
        ),
    }


def choose_plot_point(page, rate_mbit):
    """Move over a known point near the newest sample, using CSS pixel coordinates."""
    canvas = page.locator("#traffic-chart")
    box = canvas.bounding_box()
    assert box and box["width"] > 0
    point = page.evaluate(
        """({box, rate}) => {
          const canvas = document.querySelector('#traffic-chart');
          const context = canvas.getContext('2d');
          const pixelRatio = window.devicePixelRatio || 1;
          const height = box.height;
          const bottom = height - 34;
          const top = 16;
          const y = bottom - (bottom - top) * rate / trafficAxisMaximum(trafficSamples);
          return {x: box.width - 16, y: y, ratio: pixelRatio};
        }""",
        {"box": box, "rate": rate_mbit},
    )
    canvas.hover(position={"x": point["x"], "y": point["y"]})


def expect_emphasis(page, series):
    button = page.locator(f'[data-traffic-series="{series}"]')
    expect(button).to_have_class(re.compile(r"\btraffic-highlighted\b"))
    expect(page.locator(".traffic-legend [data-traffic-series].traffic-dimmed")).to_have_count(5)
    return button


def is_traffic_poll(url):
    return urlsplit(url).path == "/api/traffic"


def prepare_page(browser, base, width, dpr, general_active, touch=False, low_rate=False):
    context = browser.new_context(
        viewport={"width": width, "height": 900},
        device_scale_factor=dpr,
        has_touch=touch,
        is_mobile=touch,
    )
    page = context.new_page()
    page.set_default_timeout(LIMIT)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: errors.append(message.text)
        if message.type == "error" or "content security policy" in message.text.lower()
        else None,
    )
    requests = []
    traffic_poll_responses = []
    page.on("request", lambda request: requests.append((request.method, request.url)))
    page.on(
        "response",
        lambda response: traffic_poll_responses.append(response)
        if is_traffic_poll(response.url)
        else None,
    )
    if general_active:
        page.route("**/api/general-profile", lambda route: route.fulfill(json={
            "profile": {"id": "normal", "name": "Shared normal"},
            "active": True,
            "state": {"observed_state": "applied"},
        }))
    page.route("**/api/traffic", lambda route: route.fulfill(
        json=traffic_response(general_active, low_rate)
    ))

    def post(path, data):
        response = page.request.post(base + path, headers=HEADERS, data=data)
        assert response.ok, (path, response.status, response.text())

    post("/api/profiles", {"id": "normal", "name": "Normal"})
    post("/api/devices", {
        "id": "phone", "name": "Test phone", "ipv4_address": "192.168.50.184",
        "profile_id": "normal",
    })
    page.goto(base)
    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill("browser-test")
    page.locator("#check-admin-token").click()
    expect(page.locator("#private-dashboard")).to_be_visible()
    page.locator("#close-credentials").click()
    page.locator('a[data-page-link="traffic"]').click()
    expect(page.locator("#page-traffic")).to_be_visible()
    expect(page.locator("#traffic-status")).to_contain_text("Sampling live traffic", timeout=8000)
    if general_active:
        expect(page.locator("#traffic-device")).to_contain_text("All affected devices (shared)")
    else:
        expect(page.locator("#traffic-device")).to_contain_text("Test phone")
    expect(page.locator("#traffic-chart-detail")).to_be_visible()
    page.wait_for_load_state("networkidle", timeout=8000)
    if page.locator("#toast-dismiss").is_visible():
        page.locator("#toast-dismiss").click()
    while len(traffic_poll_responses) < 3:
        with page.expect_response(
            lambda response: is_traffic_poll(response.url), timeout=7000
        ):
            pass
    return context, page, requests, errors


def expect_legend_targets(page, touch):
    """Keep visible chart controls usable on desktop and touch screens."""
    assert page.evaluate('matchMedia("(pointer: coarse)").matches') == touch
    buttons = page.locator(".traffic-legend button:visible")
    assert buttons.count() >= 4
    heights = []
    for button in buttons.all():
        expect(button).to_be_visible()
        button.scroll_into_view_if_needed()
        box = button.bounding_box()
        viewport = page.viewport_size
        assert box and viewport
        assert box["width"] >= 44 and box["height"] >= 44, box
        assert box["x"] >= 0 and box["x"] + box["width"] <= viewport["width"] + 1, box
        assert box["y"] >= 0 and box["y"] + box["height"] <= viewport["height"] + 1, box
        heights.append(box["height"])
    print(f"Visible legend targets: {buttons.count()}, minimum height {min(heights)}px, "
          f"width {page.viewport_size['width']}px, coarse pointer {touch}")


def check_chart(browser, base, width, dpr, general_active, touch=False):
    context, page, requests, errors = prepare_page(
        browser, base, width, dpr, general_active, touch
    )
    try:
        selector = page.locator("#traffic-device")
        expect(selector).to_have_value("@all")
        upload_limit = page.locator("#traffic-profile-upload-legend")
        download_limit = page.locator("#traffic-profile-download-legend")
        if general_active:
            # The reported mismatch: shared profile references stay named in aggregate mode.
            expect(upload_limit).to_be_visible()
            expect(download_limit).to_be_visible()
            expect(upload_limit).to_contain_text("Shared profile upload limit")
            expect(download_limit).to_contain_text("Shared profile download limit")
            visible_series = SERIES
        else:
            expect(upload_limit).to_be_hidden()
            expect(download_limit).to_be_hidden()
            visible_series = SERIES[:4]
        for series in visible_series:
            expect(page.locator(f'[data-traffic-series="{series}"]')).to_be_visible()
        expect_legend_targets(page, touch)

        expect(page.locator("#traffic-status")).to_contain_text(
            "Sampling live traffic", timeout=8000
        )
        detail = page.locator("#traffic-chart-detail")
        if general_active:
            # Both upload series are exactly 30 Mbit/s. The detail must expose the overlap
            # candidates instead of silently choosing whichever stroke was drawn last.
            choose_plot_point(page, 30)
            expect(detail).to_contain_text("Upload traffic")
            expect(detail).to_contain_text("Shared profile upload limit")
            expect(detail).to_contain_text("measured at the Pi")
            expect(detail).to_contain_text("recorded")

            # The yellow line sits apart from measured download traffic and is directly
            # identifiable from its canvas geometry.
            choose_plot_point(page, 5)
            expect_emphasis(page, "profile_download")
            expect(detail).to_contain_text("Shared profile download limit")
            expect(detail).to_contain_text("5.00 Mbit/s")

            # Pointer interaction must be local. Exclude the already-running bounded poll.
            request_mark = len(requests)
            choose_plot_point(page, 30)
            if touch:
                upload_limit.tap()
            else:
                upload_limit.hover()
            selected = expect_emphasis(page, "profile_upload")
            expect(detail).to_contain_text("30.00 Mbit/s")
            if touch:
                selected.tap()
                expect(selected).to_have_attribute("aria-pressed", "false")
                selected.tap()
                expect(selected).to_have_attribute("aria-pressed", "true")
            else:
                selected.click()
                expect(selected).to_have_attribute("aria-pressed", "true")
                selected.click()
                expect(selected).to_have_attribute("aria-pressed", "false")
                selected.click()
            expect(selected).to_have_attribute("aria-pressed", "true")
            unrelated = [item for item in requests[request_mark:] if not is_traffic_poll(item[1])]
            assert not unrelated, f"Inspecting a chart line issued requests: {unrelated}"
            if width == 1440:
                for _ in range(3):
                    with page.expect_response(
                        lambda response: is_traffic_poll(response.url), timeout=5000
                    ):
                        pass
                    expect(selected).to_have_attribute("aria-pressed", "true")
                    expect_emphasis(page, "profile_upload")
                page.locator(".traffic-chart-wrap").screenshot(
                    path="test-results/traffic-chart-wide-selected.png"
                )
            # An open question owns Escape. It answers no and leaves the highlight where it is.
            page.evaluate("() => { window.question = confirmWorkflow('Add this name to the profile?'); }")
            expect(page.get_by_role("dialog")).to_have_count(1)
            page.keyboard.press("Escape")
            expect(page.get_by_role("dialog")).to_have_count(0)
            expect(selected).to_have_attribute("aria-pressed", "true")
            page.keyboard.press("Escape")
            expect(selected).to_have_attribute("aria-pressed", "false")

            upload = page.locator('[data-traffic-series="upload"]')
            upload.focus()
            expect_emphasis(page, "upload")
            expect(detail).to_contain_text("Upload traffic")
            page.keyboard.press("Escape")
        else:
            with page.expect_response(
                lambda response: is_traffic_poll(response.url), timeout=5000
            ):
                selector.select_option("phone")
            expect(upload_limit).to_contain_text("Device upload limit")
            expect(download_limit).to_contain_text("Device download limit")
            focus_target = page.locator('[data-traffic-series="profile_download"]')
            focus_target.focus()
            expect(detail).to_contain_text("Device download limit")
            for _ in range(3):
                with page.expect_response(
                    lambda response: is_traffic_poll(response.url), timeout=5000
                ):
                    pass
                expect(focus_target).to_be_focused()
            if width == 375:
                page.locator(".traffic-chart-wrap").screenshot(
                    path="test-results/traffic-chart-narrow-selected.png"
                )

        # Change layout substantially and make sure redraw geometry remains interactive.
        resized_width = 375 if width > 768 else 1440
        page.set_viewport_size({"width": resized_width, "height": 900})
        expect(page.locator("#traffic-chart")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth") <= resized_width + 1
        expect_legend_targets(page, touch)
        choose_plot_point(page, 5)
        expect_emphasis(page, "profile_download")
        expect(detail).to_contain_text("5.00 Mbit/s")
        expect(detail).to_contain_text("recorded")
        assert not errors, errors
    finally:
        context.close()


def check_low_rate_chart(browser, base, width):
    context, page, requests, errors = prepare_page(
        browser, base, width, 1, True, low_rate=True
    )
    try:
        expect(page.locator("#traffic-offscale")).to_contain_text("ISP download plan: 100.00 Mbit/s")
        assert page.evaluate("trafficSamples.at(-1).download / trafficAxisMaximum(trafficSamples)") >= 0.1
        latest = page.locator("#traffic-latest-rates")
        expect(latest).to_contain_text("Latest interval at the Pi")
        expect(page.locator(".traffic-samples")).not_to_have_attribute("open", "")
        page.locator(".traffic-samples summary").click()
        expect(page.locator("#traffic-sample-rows tr").first).to_be_visible()
        assert page.locator("#traffic-sample-rows tr").count() <= 30
        assert page.locator("#traffic-sample-rows").evaluate(
            "node => !node.closest('[aria-live], [role=status], [role=alert]')"
        )
        pause = page.locator("#traffic-pause")
        pause.click()
        expect(pause).to_have_text("Resume sampling")
        expect(pause).to_have_attribute("aria-pressed", "true")
        sample_text = page.locator("#traffic-sample-rows").inner_text()
        count = sum(is_traffic_poll(url) for _, url in requests)
        page.wait_for_timeout(5100)
        assert sum(is_traffic_poll(url) for _, url in requests) == count
        assert page.locator("#traffic-sample-rows").inner_text() == sample_text
        page.locator('a[data-page-link="profiles"]').click()
        page.locator('a[data-page-link="traffic"]').click()
        expect(pause).to_have_attribute("aria-pressed", "true")
        assert sum(is_traffic_poll(url) for _, url in requests) == count
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
        page.locator(".traffic-chart-wrap").screenshot(path=f"test-results/traffic-axis-{width}.png")
        with page.expect_response(lambda response: is_traffic_poll(response.url)):
            pause.click()
        expect(pause).to_have_attribute("aria-pressed", "false")
        # The first post-pause response is a fresh baseline, never an interval across the pause.
        expect(page.locator("#traffic-sample-rows tr")).to_have_count(0)
        expect(page.locator("#traffic-status")).to_contain_text("Collecting the first sample")
        expect(latest).to_contain_text("Latest interval at the Pi", timeout=7000)
        assert not errors, errors
    finally:
        context.close()


def check_counter_restarts(browser, base, width):
    """Synthetic counter replacement keeps old points and leaves a truthful gap."""
    context, page, _, errors = prepare_page(browser, base, width, 1, False)
    try:
        page.evaluate("""() => {
          clearTrafficTimer(); trafficSamplingAllowed = false;
        }""")
        page.wait_for_function("!trafficRequestInFlight && !trafficReadingInFlight")
        state = {"bytes": 10_000_000, "packets": 10_000, "at": 100}

        def reply(route):
            data = traffic_response(False)
            data["sampled_at"] = state["at"]
            data["devices"]["phone"] = {
                "upload_bytes": state["bytes"], "download_bytes": state["bytes"],
                "upload_packets": state["packets"], "download_packets": state["packets"],
            }
            route.fulfill(json=data)

        page.unroute("**/api/traffic")
        page.route("**/api/traffic", reply)
        page.evaluate("() => {resetTrafficHistory(); trafficSamplingAllowed = true;}")
        page.evaluate("() => pollTraffic()")
        state.update(bytes=12_000_000, packets=12_000, at=102)
        page.evaluate("() => pollTraffic()")
        assert page.evaluate("trafficSamples.map(s => s.upload)") == [8]
        # Restart packets only: byte rates and their history remain valid.
        state.update(bytes=14_000_000, packets=0, at=104)
        page.evaluate("() => pollTraffic()")
        assert page.evaluate("trafficSamples.map(s => s.upload)") == [8, 8]
        expect(page.locator("#traffic-upload-packets")).to_have_text("—")
        # Restart bytes too: an unavailable interval, never a fabricated zero/spike.
        state.update(bytes=0, packets=0, at=106)
        page.evaluate("() => pollTraffic()")
        expect(page.locator("#traffic-upload-rate")).to_have_text("unavailable")
        expect(page.locator("#traffic-latest-rates")).to_contain_text("unavailable")
        expect(page.locator("#traffic-status")).to_contain_text("earlier history is kept")
        page.locator(".traffic-samples summary").click()
        expect(page.locator("#traffic-sample-rows tr")).to_have_count(3)
        expect(page.locator("#traffic-sample-rows tr").first).to_contain_text("unavailable")
        state.update(bytes=2_000_000, packets=2_000, at=108)
        page.evaluate("() => pollTraffic()")
        assert page.evaluate("trafficSamples.map(s => s.upload)") == [8, 8, None, 8]
        # The null point breaks both measured lines; it is not joined across the restart.
        assert page.evaluate("""() => trafficSeriesGeometry(trafficSamples, {},
          {left:0,right:600,top:0,bottom:100},10,performance.now(),'device')
          .filter(s => ['upload','download'].includes(s.field))
          .every(s => s.points.length === 3 && s.segments.length === 1)""")
        expect(page.locator("#traffic-status")).to_have_text("Sampling live traffic every 2 seconds.")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=f"test-results/traffic-counter-{width}.png", full_page=True)
        assert not errors, errors
    finally:
        context.close()


def run():
    Path("test-results").mkdir(exist_ok=True)
    process, port = start_fixture()
    try:
        base = f"http://127.0.0.1:{port}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                for width in (1440, 375):
                    check_counter_restarts(browser, base, width)
                if os.environ.get("JITTERSIM_TRAFFIC_COUNTER_AUDIT") == "1":
                    print("Traffic counter restart checks passed at desktop and phone sizes.")
                    return
                for width, dpr, active, touch in (
                    (1440, 1, True, False), (375, 1, False, False),
                    (375, 3, True, False), (375, 3, True, True)
                ):
                    check_chart(browser, base, width, dpr, active, touch)
                for width in (1440, 375):
                    check_low_rate_chart(browser, base, width)
            finally:
                browser.close()
        print("Traffic chart interaction passed at desktop, phone and high-density sizes.")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()

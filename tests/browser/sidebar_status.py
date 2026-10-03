"""Check that the sidebar shows the general profile and the devices that are affected."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.phone import is_phone, open_status, sidebar_control

MAC_ADDRESS = "02:00:00:00:00:01"
CLIENT_NAME = "Fixture laptop"
PROFILE_NAME = "Shared slow network"
HEADERS = {"Authorization": "Bearer browser-test"}


def start_fixture():
    # a scheduler like a real install so that an Apply keeps the network running
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
            raise RuntimeError("Sidebar status dashboard fixture did not start")
        return process, int(process.stdout.readline().strip())


def devices_line(page):
    """The devices text: the summary of a list or the plain line when there is no list."""

    return page.locator("#status-devices-plain, #status-devices-summary").locator("visible=true")


def show_status(page):
    """On a phone the full status block opens from the one line strip in the header."""

    if is_phone(page):
        expect(page.locator("#status-strip")).to_be_visible()
        open_status(page)


def wait_for_automatic_token_check(page):
    # The check ends after many requests to a dashboard that answers one at a time.
    # A slow shared CI machine needs more than 5 seconds (like apply_clarity).
    expect(page.locator("#message")).to_contain_text(
        "Token accepted. Diagnostics loaded.", timeout=20000
    )
    show_status(page)


def check_one_message(page, width, height):
    """A result is said once: in the toast, which is small, in a corner and leaves on its own."""

    toast = page.locator("#toast")
    expect(toast).to_be_visible()
    expect(toast).to_contain_text("Token accepted")
    # The message line keeps the text for scripts but shows nothing and says nothing.
    expect(page.locator("#message")).to_be_hidden()
    assert page.locator("#message").get_attribute("aria-hidden") == "true"
    announcing = page.evaluate(
        """() => [...document.querySelectorAll(
          '[role="status"], [role="alert"], [aria-live="polite"], [aria-live="assertive"]')]
          .filter((node) => !node.closest('[hidden]') && node.getAttribute('aria-hidden') !== 'true'
                  && node.textContent.includes('Token accepted')).map((node) => node.id)"""
    )
    assert announcing == ["action-announcer"], announcing
    box = toast.bounding_box()
    assert box["width"] <= 22 * 16 + 1, box
    assert box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= width, box
    if not is_phone(page):
        assert height - (box["y"] + box["height"]) <= 17, box
    expect(toast.get_by_role("button", name="Dismiss message")).to_be_visible()
    expect(toast).to_be_hidden(timeout=9000)

    # A result must not hide the control currently being used by keyboard.
    page.evaluate("showToast('Keyboard notification check', 'success')")
    dismiss = toast.get_by_role("button", name="Dismiss message")
    dismiss.focus()
    page.wait_for_timeout(6500)
    expect(toast).to_be_visible()
    expect(dismiss).to_be_focused()
    page.locator("#page-devices").focus()
    page.mouse.move(0, 0)
    expect(toast).to_be_hidden(timeout=9000)
    expect(page.locator("#page-devices")).to_be_focused()

    page.evaluate("document.activeElement.blur(); showToast('Automatic sign-in result', 'success')")
    dismiss.focus()
    page.keyboard.press("Enter")
    expect(toast).to_be_hidden()
    expect(page.locator("#brand-title")).to_be_focused()

    # Deliberate keyboard dismissal returns to a usable target even if the source disappears.
    for unavailable in (None, "hidden", "removed"):
        page.evaluate("""(unavailable) => {
          const source = document.createElement('button');
          source.id = 'notification-source'; source.textContent = 'Source';
          document.body.appendChild(source); source.focus();
          showToast('Persistent recovery hint', 'error');
          if (unavailable === 'hidden') source.hidden = true;
          if (unavailable === 'removed') source.remove();
        }""", unavailable)
        dismiss.focus()
        page.wait_for_timeout(16000 if unavailable is None else 50)
        expect(toast).to_be_visible()
        page.keyboard.press("Enter")
        expect(toast).to_be_hidden()
        expect(page.locator("#notification-source" if unavailable is None else "#brand-title")).to_be_focused()
        page.evaluate("document.getElementById('notification-source')?.remove()")

    if is_phone(page):
        page.evaluate("showToast('Recovery instructions. '.repeat(200), 'error')")
        message = page.locator("#toast-message")
        assert message.evaluate("node => node.scrollHeight > node.clientHeight")
        expect(dismiss).to_be_visible()
        rect = message.bounding_box()
        session = page.context.new_cdp_session(page)
        start_y, end_y = rect["y"] + rect["height"] - 12, rect["y"] + 12
        touch = {"x": rect["x"] + rect["width"] / 2, "y": start_y}
        session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [touch]})
        for step in range(1, 6):
            touch["y"] = start_y + (end_y - start_y) * step / 5
            session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [touch]})
            page.wait_for_timeout(20)
        session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        page.wait_for_function("document.getElementById('toast-message').scrollTop > 0")
        session.detach()
        page.screenshot(path=f"test-results/sidebar-long-recovery-{width}.png")
        dismiss.click()
        expect(toast).to_be_hidden()


def check_width(browser, width, height):
    # a fresh fixture for each width so that each run starts before the first Apply
    process, port = start_fixture()
    try:
        base = f"http://127.0.0.1:{port}"
        context = browser.new_context(viewport={"width": width, "height": height}, has_touch=width < 769)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        status = page.locator("#sidebar-status")

        def post(path, data):
            response = page.request.post(base + path, headers=HEADERS, data=data)
            assert response.ok, (path, response.status, response.text())

        page.goto(base)
        expect(page.locator("#private-dashboard")).to_be_hidden()
        expect(status).to_be_hidden()

        page.locator("#open-admin-credentials").click()
        page.locator("#admin-token").fill("browser-test")
        page.locator("#check-admin-token").click()
        expect(page.locator("#private-dashboard")).to_be_visible()
        page.locator("#remember-admin-token").check()
        # A reload before the token is stored would forget it.
        expect(page.locator("#remember-admin-token-note")).to_contain_text(
            "Remembered until", timeout=20000
        )
        page.locator("#close-credentials").click()

        post(
            "/api/profiles",
            {
                "id": "slow",
                "name": PROFILE_NAME,
                "upload": {"bandwidth_kbit": 1000},
                "download": {"bandwidth_kbit": 2000},
            },
        )
        post(
            "/api/connected-device-labels",
            {"mac_address": MAC_ADDRESS, "display_name": CLIENT_NAME, "device_type": "laptop"},
        )
        page.reload()
        wait_for_automatic_token_check(page)
        check_one_message(page, width, height)

        # signed in with nothing applied yet
        expect(status).to_be_visible()
        expect(page.locator("#status-general")).to_have_text("Everyone: none")
        expect(devices_line(page)).to_have_text("Not running")
        expect(page.locator("#status-lists")).to_be_hidden()
        # nothing to open when there is no list of devices
        expect(page.locator("#status-devices")).to_be_hidden()
        # the collapsed block stays small so that the footer buttons keep their place
        if not is_phone(page):
            assert status.bounding_box()["height"] <= 52, status.bounding_box()

        # the general profile reaches the one connected client
        expect(page.locator("#general-profile-select")).to_be_enabled()
        page.locator("#general-profile-select").select_option("slow")
        page.locator("#general-profile-apply").click()
        expect(page.locator("#status-general")).to_have_text(
            f"Everyone: {PROFILE_NAME} · active"
        )
        expect(devices_line(page)).to_have_text("1 device affected")
        page.locator("#status-devices-summary").click()
        expect(page.locator("#status-devices-list li")).to_have_count(1)
        expect(page.locator("#status-devices-list li")).to_contain_text(CLIENT_NAME)
        expect(page.locator("#status-devices-list li")).to_contain_text(PROFILE_NAME)
        # a long list scrolls inside the block
        limits = page.locator("#status-devices-list").evaluate(
            "(node) => [getComputedStyle(node).maxHeight, getComputedStyle(node).overflowY]"
        )
        assert limits[0] == "128px" and limits[1] == "auto", limits
        page.wait_for_timeout(300)
        page.locator(".sidebar").screenshot(path=f"test-results/sidebar-status-{width}.png")
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 1

        # an enabled service list shows on its own line
        post(
            "/api/service-lists",
            {
                "id": "video",
                "name": "Video",
                "profile_id": "slow",
                "services": [],
                "hostnames": ["video.example.com"],
                "enabled": True,
            },
        )
        page.reload()
        wait_for_automatic_token_check(page)
        expect(page.locator("#status-lists")).to_have_text(
            "Service lists: Video (every unprotected device)"
        )

        # A saved change sets the observed state to unknown for about a second while the
        # scheduler keeps running. The answer must stay the same during that second.
        state_body = page.evaluate("request('/api/state', 'GET')")
        general_body = page.evaluate("request('/api/general-profile', 'GET')")
        assert state_body["state"]["running"] is True, state_body
        state_body["state"]["observed_state"] = "unknown"
        general_body["active"] = False
        general_body["state"]["observed_state"] = "unknown"
        state_route = lambda route: route.fulfill(json=state_body)  # noqa: E731
        general_route = lambda route: route.fulfill(json=general_body)  # noqa: E731
        page.route("**/api/state", state_route)
        page.route("**/api/general-profile", general_route)
        page.reload()
        wait_for_automatic_token_check(page)
        expect(page.locator("#general-profile-status")).to_contain_text("Saved: " + PROFILE_NAME)
        expect(page.locator("#status-general")).to_have_text(
            f"Everyone: {PROFILE_NAME} · active"
        )
        expect(devices_line(page)).to_have_text("1 device affected")
        page.unroute("**/api/state", state_route)
        page.unroute("**/api/general-profile", general_route)

        # a protected client is never listed
        post("/api/unaffected-devices", {"mac_address": MAC_ADDRESS, "display_name": CLIENT_NAME})
        page.reload()
        wait_for_automatic_token_check(page)
        expect(devices_line(page)).to_have_text("No connected device is affected")
        expect(page.locator("#status-devices")).to_be_hidden()
        expect(page.locator("#status-devices-list li")).to_have_count(0)
        expect(status).not_to_contain_text(CLIENT_NAME)
        page.wait_for_timeout(300)
        page.locator(".sidebar").screenshot(path=f"test-results/sidebar-status-protected-{width}.png")

        # A reset from another tab or the command line shows up on the next poll.
        post("/api/reset", {})
        expect(devices_line(page)).to_have_text("Profiles off", timeout=9000)
        expect(page.locator("#status-general")).to_have_text(
            f"Everyone: {PROFILE_NAME} · paused"
        )
        expect(page.locator("#status-lists")).to_be_hidden()
        post("/api/apply", {})
        expect(devices_line(page)).to_have_text("No connected device is affected", timeout=9000)
        expect(page.locator("#status-general")).to_have_text(
            f"Everyone: {PROFILE_NAME} · active"
        )

        # turning every profile off
        page.locator("#panic-button").click()
        expect(devices_line(page)).to_have_text("Profiles off")
        expect(page.locator("#status-general")).to_have_text(
            f"Everyone: {PROFILE_NAME} · paused"
        )
        page.reload()
        wait_for_automatic_token_check(page)
        expect(devices_line(page)).to_have_text("Profiles off")

        # clearing the token takes the block and the names out of the page
        sidebar_control(page, "#open-admin-credentials").click()
        page.locator("#clear-admin-token").click()
        expect(status).to_be_hidden()
        expect(page.locator("#status-general")).to_have_text("")
        expect(page.locator("#status-devices-plain")).to_have_text("")
        expect(page.locator("#status-devices-summary")).to_have_text("")

        assert not errors, errors
        context.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def run():
    Path("test-results").mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
        )
        try:
            for width, height in ((1920, 1000), (390, 844)):
                check_width(browser, width, height)
            print("Sidebar status acceptance passed at desktop and phone widths.")
        finally:
            browser.close()


if __name__ == "__main__":
    run()

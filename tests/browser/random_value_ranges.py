"""Exercise real profile range controls at desktop and phone widths."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def run():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Range dashboard fixture did not start")
            port = int(process.stdout.readline().strip())
        base = f"http://127.0.0.1:{port}"
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                for width, height in ((1920, 1080), (390, 844)):
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    assert page.request.post(base + "/api/profiles/ranges", data={}).status == 401
                    page.goto(base + "#profiles")
                    page.locator("#open-admin-credentials").click()
                    page.locator("#admin-token").fill("browser-test")
                    page.locator("#check-admin-token").click()
                    expect(page.locator("#token-check-status")).to_contain_text("Token accepted")
                    page.locator("#remember-admin-token").check()
                    page.locator("#close-credentials").click()
                    expect(page.locator("#profile-range-status")).to_contain_text("Rerolls are off")
                    form_toggle = page.locator("#profile-form-title .card-toggle")
                    if (
                        form_toggle.count()
                        and form_toggle.get_attribute("aria-expanded") == "false"
                    ):
                        form_toggle.click()
                    details = page.locator("details").filter(has=page.locator("#profile-reroll"))
                    if details.count() and details.get_attribute("open") is None:
                        details.locator("summary").click()
                    stem = "#upload-corrupt-random-"
                    preview = page.locator(stem + "preview")
                    expect(page.locator(stem + "variation")).to_be_disabled()
                    page.locator("#profile-id").fill("ranges-" + str(width))
                    page.locator("#profile-name").fill("Range test " + str(width))
                    page.locator("#upload-corrupt").fill("6")
                    page.locator("#profile-reroll").fill("30")
                    page.locator(stem + "variation").fill("7.25")
                    expect(preview).to_contain_text("fixed at 6%")
                    expect(preview).to_contain_text("Starts at 6%")
                    page.locator("#upload-delay").fill("1")
                    page.locator("#upload-delay-random-variation").fill("1")
                    expect(page.locator("#upload-delay-random-preview")).to_contain_text(
                        "fixed at 1 ms"
                    )
                    page.locator(stem + "mode").select_option("range")
                    expect(page.locator(stem + "relative-label")).to_be_hidden()
                    page.locator(stem + "minimum").fill("")
                    expect(page.locator(stem + "minimum")).to_have_value("")
                    assert not page.locator(stem + "minimum").evaluate(
                        "(input) => input.checkValidity()"
                    )
                    page.locator(stem + "minimum").fill("13")
                    page.locator(stem + "maximum").fill("0")
                    assert not page.locator(stem + "minimum").evaluate(
                        "(input) => input.checkValidity()"
                    )
                    page.locator(stem + "minimum").fill("0")
                    page.locator(stem + "maximum").fill("13")
                    expect(preview).to_contain_text("0%–13%")
                    page.locator(stem + "enabled").fill("66")
                    expect(preview).to_contain_text("66% chance active")
                    page.locator("#download-loss").fill("25")
                    page.locator("#profile-reroll").fill("0")
                    expect(page.locator(stem + "mode")).to_be_disabled()
                    expect(preview).to_contain_text("Rerolls off. Base value: 6%")
                    page.locator("#profile-reroll").fill("30")
                    expect(page.locator(stem + "minimum")).to_have_value("0")
                    expect(page.locator(stem + "maximum")).to_have_value("13")
                    with page.expect_response(
                        lambda response: (
                            response.url.endswith("/api/profiles")
                            and response.request.method == "POST"
                        )
                    ) as saved:
                        page.locator('#profile-form button[type="submit"]').click()
                    assert saved.value.ok
                    profile = saved.value.json()["profile"]
                    assert profile["upload"]["corrupt_randomness"]["mode"] == "range"
                    assert profile["download"]["loss_percent"] == 25
                    assert profile["upload"]["corrupt_randomness"]["enabled_probability"] == 66
                    page.reload()
                    expect(page.locator("#private-dashboard")).to_be_visible()
                    page.locator(
                        ".saved-profile-row", has_text="Range test " + str(width)
                    ).get_by_role(
                        "button", name="Edit Range test " + str(width), exact=True
                    ).click()
                    expect(page.locator(stem + "mode")).to_have_value("range")
                    expect(page.locator(stem + "maximum")).to_have_value("13")
                    expect(preview).to_contain_text("0%–13%")
                    if details.count() and details.get_attribute("open") is None:
                        details.locator("summary").click()
                    page.locator(stem + "minimum").fill("5")
                    page.locator(stem + "maximum").fill("12")
                    expect(preview).to_contain_text("5%–12%")
                    page.locator(stem + "mode").select_option("relative")
                    expect(preview).to_contain_text("fixed at 6%")
                    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
                    if page.locator("#toast-dismiss").is_visible():
                        page.locator("#toast-dismiss").click()
                    page.locator(stem + "card").screenshot(
                        path=f"test-results/random-range-{width}.png"
                    )
                    page.locator(stem + "mode").select_option("range")
                    expect(preview).to_contain_text("5%–12%")
                    page.locator(stem + "card").screenshot(
                        path=f"test-results/custom-range-{width}.png"
                    )
                    assert not errors, errors
                    context.close()
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()

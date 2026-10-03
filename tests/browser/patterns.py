"""Check fresh patterns and bounded history at desktop and phone widths."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def run():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True,
        env={**os.environ, "JITTERSIM_BROWSER_TEST_SCHEDULER": "1"},
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Pattern dashboard fixture did not start")
            base = f"http://127.0.0.1:{int(process.stdout.readline().strip())}"
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE"))
            try:
                for width, height in ((1920, 1000), (390, 844)):
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    profile_id = f"fresh-{width}"
                    headers = {"Authorization": "Bearer browser-test"}
                    assert page.request.post(base + "/api/profiles", headers=headers, data={
                        "id": profile_id, "name": "Fresh pattern", "random_mode": "fresh",
                        "download": {"delay_ms": 100}, "reroll_interval_seconds": 5,
                    }).ok
                    assert page.request.get(base + "/api/pattern-history").status == 401
                    page.goto(base + "/#profiles")
                    page.locator("#open-admin-credentials").click()
                    page.locator("#admin-token").fill("browser-test")
                    page.locator("#check-admin-token").click()
                    expect(page.locator("#private-dashboard")).to_be_visible()
                    expect(page.locator("#message")).to_contain_text("Token accepted. Diagnostics loaded.")
                    page.locator("#close-credentials").click()
                    row = page.locator(f'.saved-profile-row[data-profile-id="{profile_id}"]')
                    row.get_by_role("button", name="Edit Fresh pattern", exact=True).click()
                    expect(page.locator("#profile-random-mode")).to_have_value("fresh")
                    details = page.locator("#profile-form details")
                    details.locator("summary").click()
                    page.locator("#profile-seed").fill("123")
                    page.locator("#profile-form button[type=submit]").click()
                    expect(page.locator("#message")).to_contain_text("Clear the saved seed")
                    page.locator("#profile-seed").fill("")
                    page.locator("#profile-form button[type=submit]").click()
                    expect(page.locator("#message")).to_contain_text("Profile saved.")
                    row.get_by_role("button", name="Apply to all: Fresh pattern", exact=True).click()
                    expect(row.locator(".general-badge")).to_have_count(1)
                    history = page.locator("details").filter(has=page.locator("#load-pattern-history"))
                    history.locator("summary").click()
                    for _ in range(20):
                        page.locator("#load-pattern-history").click()
                        expect(page.locator("#load-pattern-history")).to_be_enabled()
                        if page.locator("#pattern-history-body tr").count():
                            break
                        page.wait_for_timeout(200)
                    expect(page.locator("#pattern-history-body")).to_contain_text("general-profile")
                    expect(page.locator("#pattern-history-status")).to_have_text("Latest choices loaded.")
                    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
                    page.evaluate("dismissToast()")
                    page.locator("#profile-form").screenshot(path=f"test-results/pattern-form-{width}.png")
                    history.screenshot(path=f"test-results/pattern-history-{width}.png")
                    assert not errors, errors
                    assert page.request.post(base + "/api/general-profile/stop", headers=headers, data={}).ok
                    context.close()
            finally:
                browser.close()
        print("Fresh patterns and history passed at desktop and phone widths.")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()

"""Exercise certificate detail privacy and downloads in the served dashboard.

The API details and public download bytes are disposable fixtures. This does
not install a certificate or assert operating-system trust or live discovery.
"""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.phone import sidebar_control
from tests.browser.router_copy import ADMIN_TOKEN, sign_in


def run_width(browser, base, width, height):
    context = browser.new_context(viewport={"width": width, "height": height}, base_url=base)
    page = context.new_page()
    errors = []
    details = []
    downloads = []
    address = "https://" + "a" * 63 + ".local:8080"
    fingerprint = "disposable-public-certificate-fingerprint"

    def trust_details(route):
        details.append(route.request.headers.get("authorization"))
        route.fulfill(json={"available": True, "address": address, "fingerprint": fingerprint,
                            "steps": {"mac": ["Fixture Mac instruction"],
                                      "iphone": ["Fixture phone instruction"]},
                            "announce": {"state": "published"}})

    def trust_download(route):
        downloads.append(route.request.headers.get("authorization"))
        route.fulfill(content_type="application/x-x509-ca-cert",
                      body="disposable public certificate download bytes")

    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/trust", trust_details)
    page.route("**/api/trust/ca.crt", trust_download)
    try:
        page.goto(base + "/")
        expect(page.locator("#trust-pi")).to_be_hidden()
        expect(page.locator("#easy-address")).to_contain_text("netlab.local")
        expect(page.locator("#trust-fingerprint")).to_have_text("unknown")
        assert details == []
        sign_in(page)
        page.locator('[data-page-link="access"]').click()
        expect(page.locator("#trust-pi")).to_be_visible()
        expect(page.locator("#trust-fingerprint")).to_have_text(fingerprint)
        expect(page.locator("#trust-steps-mac")).to_have_text("Fixture Mac instruction")
        expect(page.locator("#one-address-link")).to_have_attribute("href", address + "/")
        assert details == ["Bearer " + ADMIN_TOKEN]
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 1, page.evaluate("""() =>
            [...document.querySelectorAll('*')].filter(node => node.getBoundingClientRect().right > innerWidth + 1)
                .slice(0, 12).map(node => [node.id, node.className, node.getBoundingClientRect().right])""")
        with page.expect_download() as downloaded:
            page.locator("#trust-download-crt").click()
        download = downloaded.value
        assert download.suggested_filename == "local-dashboard.crt"
        assert Path(download.path()).read_bytes() == b"disposable public certificate download bytes"
        assert downloads == ["Bearer " + ADMIN_TOKEN]
        expect(page.locator("#trust-download-crt")).to_be_enabled()
        sidebar_control(page, "#open-admin-credentials").click()
        page.locator("#admin-token").fill("different-unchecked-token")
        expect(page.locator("#private-dashboard")).to_be_hidden()
        expect(page.locator("#trust-pi")).to_be_hidden()
        for selector in ("#trust-fingerprint", "#trust-address", "#trust-status", "#one-address-url",
                         "#trust-steps-mac", "#trust-steps-iphone"):
            expect(page.locator(selector)).to_have_text("")
        assert page.locator("#one-address-link").get_attribute("href") is None
        expect(page.locator("#trust-download-crt")).to_be_disabled()
        expect(page.locator("#trust-download-profile")).to_be_disabled()
        assert details == ["Bearer " + ADMIN_TOKEN]
        assert fingerprint not in page.content()
        assert not errors, errors
    finally:
        context.close()


def run():
    process = subprocess.Popen([sys.executable, "-m", "tests.browser.fixture"],
                               stdout=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Trust dashboard fixture did not start")
            port = int(process.stdout.readline().strip())
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE"))
            try:
                for width, height in ((1920, 1000), (390, 844)):
                    run_width(browser, f"http://127.0.0.1:{port}", width, height)
            finally:
                browser.close()
        print("Trust details, download privacy and long addresses passed at desktop and 390px widths.")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()

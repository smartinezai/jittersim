"""Return to unfinished editors without saving or replacing their values."""

from playwright.sync_api import expect

from tests.browser.phone import open_controls, sidebar_control


def check_draft_navigation(page):
    original_viewport = page.viewport_size
    writes = []

    def record_write(request):
        if request.method != "GET":
            writes.append(request.url)

    page.on("request", record_write)
    try:
        page.locator('[data-page-link="profiles"]').click()
        heading = page.locator("#profile-form-title .card-toggle")
        if heading.get_attribute("aria-expanded") == "false":
            heading.click()
        page.locator("#profile-id").fill("draft-navigation")
        page.locator("#profile-name").fill("Unfinished <b>profile</b>")
        review = page.get_by_role("button", name="Review Profile: Unfinished <b>profile</b>", exact=True)
        expect(page.locator("#draft-links button")).to_have_count(1)
        expect(page.locator("#draft-links b")).to_have_count(0)
        heading.click()
        page.locator('[data-page-link="devices"]').click()
        before = len(writes)
        review.click()
        expect(heading).to_have_attribute("aria-expanded", "true")
        expect(page.locator("#profile-form")).to_be_visible()
        expect(page.locator("#profile-name")).to_have_value("Unfinished <b>profile</b>")
        assert page.url.endswith("#profiles")
        assert page.locator("#profile-form").evaluate("form => form.contains(document.activeElement)")
        expect(page.locator("#profile-form :focus")).to_be_in_viewport()
        assert len(writes) == before, writes

        page.locator('[data-page-link="access"]').click()
        policy = page.get_by_role("button", name="Network access rules", exact=True)
        if policy.get_attribute("aria-expanded") == "false":
            policy.click()
        domains = page.get_by_label("Global blocked domains (comma separated)", exact=True)
        domains.fill("unfinished.example")
        rules_review = page.get_by_role("button", name="Review Network access rules", exact=True)
        expect(page.locator("#draft-links button")).to_have_count(2)
        button_node = review.element_handle()
        page.locator('[data-page-link="devices"]').click()
        rules_review.focus()
        with page.expect_response(lambda response: response.url.endswith("/api/conditions")):
            expect(rules_review).to_be_focused()
        expect(rules_review).to_be_focused()
        assert button_node.evaluate("button => button.isConnected")
        expect(page.locator("#draft-links button")).to_have_text([
            "Review Profile: Unfinished <b>profile</b>", "Review Network access rules"
        ])

        page.locator('[data-page-link="profiles"]').click()
        page.locator("#profile-name").fill("A" * 180)
        expect(page.locator("#draft-links button").first).to_contain_text("A" * 100 + "…")
        assert button_node.evaluate("button => button.isConnected")
        page.set_viewport_size({"width": 320, "height": 568})
        page.emulate_media(reduced_motion="reduce")
        assert page.evaluate("document.documentElement.scrollWidth <= 321")
        page.locator("#profile-name").fill("Unfinished profile")
        if page.locator("#toast").is_visible():
            page.locator("#toast button").click()
        page.locator("#draft-notice").screenshot(path="test-results/unfinished-forms-320.png")
        open_controls(page)
        rules_review.focus()
        before = len(writes)
        rules_review.press("Enter")
        expect(page.locator("#controls-toggle")).to_have_attribute("aria-expanded", "false")
        expect(domains).to_have_value("unfinished.example")
        expect(page.locator("#policy-form :focus")).to_be_in_viewport()
        assert page.url.endswith("#access")
        assert len(writes) == before, writes

        page.set_viewport_size(original_viewport)
        page.emulate_media(reduced_motion="no-preference")
        page.locator("#draft-links button").first.click()
        page.locator("#profile-name").fill("Unfinished profile")
        review = page.get_by_role("button", name="Review Profile: Unfinished profile", exact=True)
        page.locator("#draft-notice").screenshot(path="test-results/unfinished-forms-desktop.png")
        page.route("**/api/profiles", lambda route: route.fulfill(
            status=503, content_type="application/json", body='{"error":"Save unavailable"}'
        ) if route.request.method == "POST" else route.continue_())
        page.locator('#profile-form button[type="submit"]').click()
        expect(page.locator("#profile-form .form-error")).to_contain_text("Save unavailable")
        expect(review).to_be_visible()
        expect(page.locator("#profile-name")).to_have_value("Unfinished profile")
        page.unroute("**/api/profiles")
        page.locator('#profile-form button[type="submit"]').click()
        expect(review).to_have_count(0)
        expect(rules_review).to_be_visible()
        expect(domains).to_have_value("unfinished.example")

        page.locator('[data-page-link="devices"]').click()
        page.locator("#device-table tr", has_text="Test phone").get_by_role(
            "button", name="Open controls for Test phone", exact=True
        ).click()
        page.locator("#workspace-minutes").fill("17")
        device_review = page.get_by_role("button", name="Review Device timer: Test phone", exact=True)
        page.get_by_role("button", name="Close device controls", exact=True).click()
        expect(page.locator("#device-workspace")).to_be_hidden()
        page.locator('[data-page-link="schedules"]').click()
        before = len(writes)
        device_review.click()
        expect(page.locator("#workspace-title")).to_have_text("Test phone")
        expect(page.locator("#workspace-minutes")).to_have_value("17")
        expect(page.locator("#workspace-profile")).to_be_focused()
        assert len(writes) == before, writes

        page.locator('[data-page-link="measurements"]').click()
        page.locator("#test-session-name").fill("Navigation test")
        page.locator("#test-session-start").click()
        expect(page.locator("#test-session-details")).to_be_visible()
        page.locator("#test-session-notes").fill("Keep these unfinished notes.")
        notes_review = page.get_by_role("button", name="Review Test notes: Navigation test", exact=True)
        page.locator('[data-page-link="devices"]').click()
        before = len(writes)
        notes_review.click()
        expect(page.locator("#test-session-notes")).to_be_focused()
        expect(page.locator("#test-session-notes")).to_have_value("Keep these unfinished notes.")
        expect(page.locator("#test-session-heading")).to_have_text("Navigation test")
        assert len(writes) == before, writes

        form = page.locator("#test-session-edit").element_handle()
        form.evaluate("form => form.remove()")
        notes_review.click()
        expect(page.locator("#message")).to_contain_text("This unfinished form is no longer available.")
        expect(notes_review).to_be_visible()
        page.locator("#test-session-details").evaluate("(parent, form) => parent.prepend(form)", form)
        expect(page.locator("#test-session-notes")).to_have_value("Keep these unfinished notes.")
        assert len(writes) == before, writes

        discard = page.get_by_role("button", name="Discard unfinished edits", exact=True)
        discard.click()
        page.get_by_role("dialog").get_by_role("button", name="Cancel", exact=True).click()
        expect(notes_review).to_be_visible()
        expect(page.locator("#test-session-notes")).to_have_value("Keep these unfinished notes.")
        discard.click()
        page.get_by_role("dialog").get_by_role("button", name="Continue", exact=True).click()
        expect(page.locator("#draft-notice")).to_be_hidden()
        assert len(writes) == before, writes
        page.locator("#test-session-notes").fill("Private unfinished notes")
        expect(notes_review).to_be_visible()
        sidebar_control(page, "#open-admin-credentials").click()
        page.locator("#check-admin-token").click()
        expect(page.locator("#private-dashboard")).to_be_visible()
        expect(notes_review).to_be_visible()
        expect(page.locator("#test-session-notes")).to_have_value("Private unfinished notes")
        page.locator("#admin-token").fill("replacement-token")
        expect(page.locator("#private-dashboard")).to_be_hidden()
        expect(page.locator("#draft-links button")).to_have_count(0)
        page.locator("#admin-token").fill("browser-test")
        page.locator("#check-admin-token").click()
        expect(page.locator("#private-dashboard")).to_be_visible()
        page.locator("#close-credentials").click()
        expect(page.locator("#draft-notice")).to_be_hidden()
        page.locator('[data-page-link="devices"]').click()
        print("Unfinished-form navigation browser acceptance passed.")
    finally:
        page.remove_listener("request", record_write)
        page.set_viewport_size(original_viewport)
        page.emulate_media(reduced_motion="no-preference")

"""Check service-list editing through the real five-second dashboard timer."""

from playwright.sync_api import expect

from tests.browser.gui_capture import capture_session, deadline


def check_service_list_focus(page):
    """Use an authenticated fixture page without issuing service-list writes."""
    page.set_default_timeout(12000)
    page.get_by_role("link", name="Schedules and limits", exact=True).click()
    card = page.locator("#service-list-card")
    if card.get_attribute("data-collapse") == "closed":
        card.get_by_role("button", name="Service lists", exact=True).click()
    inputs = page.locator("#service-list-catalogue input")
    expect(inputs.first).to_be_visible()
    original = page.request.get(page.url.split("#")[0] + "api/service-lists",
                                headers={"Authorization": "Bearer browser-test"}).json()
    state = {"catalogue": original["catalogue"], "outage": False, "fail": False, "move": False}
    writes = []
    errors = []

    def request_seen(request):
        if "/api/service-lists" in request.url and request.method != "GET":
            writes.append(request.method)

    def error_seen(_error):
        errors.append("pageerror")

    def respond(route):
        if state["move"]:
            state["move"] = False
            page.locator('#service-list-catalogue input[value="' + key + '"]').uncheck()
            page.locator("#service-list-name").focus()
            page.locator("#service-list-name").fill("Changed during read")
            page.locator("#service-list-name").evaluate("node => node.setSelectionRange(2, 5)")
            page.wait_for_timeout(200)
        if state["fail"]:
            route.fulfill(status=503, json={"error": "Synthetic read failure"})
            return
        route.fulfill(json={"catalogue": state["catalogue"], "service_lists": [{
            "id": "focus-list", "name": "Focus list", "profile_id": "normal",
            "profile_name": "Normal connection", "services": [], "hostnames": ["focus.example"],
            "domains": ["focus.example"], "enabled": True, "outage_now": state["outage"],
        }]})

    def poll():
        with page.expect_response(lambda response: response.url.endswith("/api/service-lists")
                                  and response.request.method == "GET", timeout=12000) as pending:
            pass
        pending.value.body()
        # Fetch completion precedes synchronous rendering in a following browser task.
        page.wait_for_timeout(100)

    page.on("request", request_seen)
    page.on("pageerror", error_seen)
    page.route("**/api/service-lists", respond)
    try:
        for field, value in (("id", "unfinished"), ("name", "Unfinished list"),
                             ("hostnames", "unsaved.example")):
            page.locator("#service-list-" + field).fill(value)
        page.locator("#service-list-profile").select_option("normal")
        page.locator("#service-list-enabled").uncheck()
        key = inputs.nth(1).get_attribute("value")
        chosen = inputs.nth(1)
        chosen.focus()
        page.keyboard.press("Space")
        expect(chosen).to_be_checked()
        page.evaluate("window.__focusServiceNode = document.activeElement")
        for _ in range(2):
            poll()
            expect(chosen).to_be_focused()
            expect(chosen).to_be_checked()
            assert page.evaluate("document.activeElement === window.__focusServiceNode")
        page.keyboard.press("Space")
        expect(chosen).not_to_be_checked()
        page.keyboard.press("Space")
        expect(page.locator("#service-list-id")).to_have_value("unfinished")
        expect(page.locator("#service-list-name")).to_have_value("Unfinished list")
        expect(page.locator("#service-list-hostnames")).to_have_value("unsaved.example")
        expect(page.locator("#service-list-profile")).to_have_value("normal")
        expect(page.locator("#service-list-enabled")).not_to_be_checked()
        observed = page.locator("#service-list-observed")
        observed.focus()
        page.evaluate("window.__focusObservedOption = document.querySelector('#service-list-observed option')")
        poll()
        expect(observed).to_be_focused()
        assert page.evaluate("document.querySelector('#service-list-observed option') === window.__focusObservedOption")
        chosen.focus()
        changed = [dict(item) for item in reversed(state["catalogue"])]
        for item in changed:
            if item["key"] == key:
                item.update(name="Renamed service", domains=["changed.example", "second.example"])
        changed.append({"key": "focus-new", "name": "Added service", "domains": ["new.example"]})
        # Remove a different service while the chosen key survives.
        changed = [item for item in changed if item["key"] != original["catalogue"][0]["key"]]
        state["catalogue"] = changed
        poll()
        chosen = page.locator('#service-list-catalogue input[value="' + key + '"]')
        expect(chosen).to_be_focused()
        expect(chosen).to_be_checked()
        assert page.evaluate("document.activeElement === window.__focusServiceNode")
        expect(chosen.locator("..")).to_have_attribute("title", "changed.example, second.example")
        expect(chosen.locator("..")).to_contain_text("Renamed service")
        assert inputs.evaluate_all("nodes => nodes.map(node => node.value)") == [item["key"] for item in changed]
        expect(inputs.last).not_to_be_checked()
        state["outage"] = True
        poll()
        expect(page.locator("#service-list-list")).to_contain_text("random outage now")
        expect(chosen).to_be_focused()
        state["move"] = True
        poll()
        expect(page.locator("#service-list-name")).to_be_focused()
        expect(page.locator("#service-list-name")).to_have_value("Changed during read")
        assert page.locator("#service-list-name").evaluate("node => [node.selectionStart, node.selectionEnd]") == [2, 5]
        expect(chosen).not_to_be_checked()
        chosen.focus()
        index = [item["key"] for item in changed].index(key)
        state["catalogue"] = [item for item in changed if item["key"] != key]
        fallback = state["catalogue"][min(index, len(state["catalogue"]) - 1)]["key"]
        poll()
        expect(page.locator('#service-list-catalogue input[value="' + fallback + '"]')).to_be_focused()
        expect(chosen).to_have_count(0)
        state["catalogue"] = []
        poll()
        expect(page.locator("#service-list-hostnames")).to_be_focused()
        state["fail"] = True
        poll()
        expect(page.locator("#service-list-hostnames")).to_be_focused()
        expect(page.locator("#service-list-hostnames")).to_have_value("unsaved.example")
        state.update(fail=False, catalogue=original["catalogue"])
        poll()
        expect(inputs).to_have_count(len(original["catalogue"]))
        expect(page.locator("#service-list-hostnames")).to_be_focused()
        assert not writes, "Polling unexpectedly wrote service-list data"
        assert not errors, "Polling caused browser script errors"
    finally:
        page.unroute("**/api/service-lists", respond)
        page.remove_listener("request", request_seen)
        page.remove_listener("pageerror", error_seen)
        page.evaluate("delete window.__focusServiceNode; delete window.__focusObservedOption")


def check_service_list_contexts(browser, base_url):
    """Keep all unsaved form edits and response overrides in disposable contexts."""
    from tests.browser.journeys import unlock

    with deadline(180):
        for width in (375, 1440):
            context = browser.new_context(viewport={"width": width, "height": 900})
            try:
                page = context.new_page()
                page.goto(base_url)
                unlock(page)
                check_service_list_focus(page)
            finally:
                context.close()


def run():
    with capture_session() as (browser, base_url):
        check_service_list_contexts(browser, base_url)
    print("Service-list focus checks passed at phone and desktop widths")


if __name__ == "__main__":
    run()

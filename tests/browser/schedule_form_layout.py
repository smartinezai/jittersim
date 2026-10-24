"""Keep the schedule time range together without crowding narrow windows."""

from playwright.sync_api import expect

VIEWPORTS = ((1366, 768), (1920, 1000), (1024, 768), (768, 1024),
             (683, 384), (375, 812), (320, 568))
CONTROLS = ("schedule-id", "schedule-device", "schedule-profile", "schedule-start",
            "schedule-end", "schedule-days", "schedule-enabled")


def geometry(page):
    return page.locator("#schedule-form").evaluate(
        """form => {
            const fields = form.querySelector('.fields');
            const rect = node => {
                const {x, y, width, height} = node.getBoundingClientRect();
                return {x, y, width, height};
            };
            return {
                columns: getComputedStyle(fields).gridTemplateColumns.split(' ').length,
                start: rect(form.querySelector('#schedule-start').parentElement),
                end: rect(form.querySelector('#schedule-end').parentElement),
                form: rect(form),
                controls: Array.from(fields.querySelectorAll(
                    'input:not([type="hidden"]), select')).map(node => ({
                        id: node.id || node.textContent, ...rect(node)
                    })),
                pageWidth: document.documentElement.scrollWidth
            };
        }"""
    )


def values(page):
    return page.evaluate(
        """ids => ids.map(id => {
            const node = document.getElementById(id);
            return [id, node.value, node.checked];
        })""", list(CONTROLS)
    )


def check_schedule_layout(page):
    """Exercise the real form at every wrapping threshold in the laptop regression."""

    viewport = page.viewport_size
    original = values(page)
    selected = page.locator("#schedule-device option:checked, #schedule-profile option:checked")
    names = selected.all_text_contents()
    try:
        for width, height in VIEWPORTS:
            page.set_viewport_size({"width": width, "height": height})
            selected.evaluate_all(
                "nodes => nodes.forEach(node => node.textContent = 'A long household device '"
                " + 'or profile name that must stay inside the schedule form')"
            )
            found = geometry(page)
            assert found["pageWidth"] <= width + 1, (width, found)
            if found["columns"] > 1:
                assert abs(found["start"]["y"] - found["end"]["y"]) < 1, (width, found)
            else:
                assert found["end"]["y"] >= (
                    found["start"]["y"] + found["start"]["height"]
                ), (width, found)
            for control in found["controls"]:
                assert control["x"] >= found["form"]["x"] - 1, (width, control)
                assert control["x"] + control["width"] <= (
                    found["form"]["x"] + found["form"]["width"] + 1
                ), (width, control)
                assert control["height"] >= 43.5, (width, control)
            assert values(page) == original
            page.locator('.day-toggle[data-day="6"]').focus()
            page.keyboard.press("Tab")
            expect(page.locator("#schedule-start")).to_be_focused()
            assert page.locator("#schedule-start").evaluate(
                "node => getComputedStyle(node).outlineStyle !== 'none'"
            )
            if width in (1366, 375, 320):
                page.locator("#schedule-form").screenshot(
                    path=f"test-results/schedule-form-fit/form-{width}.png"
                )
            print(f"Schedule layout {width}x{height}: {found['columns']} columns")
    finally:
        selected.evaluate_all(
            "(nodes, names) => nodes.forEach((node, i) => node.textContent = names[i])", names
        )
        page.set_viewport_size(viewport)

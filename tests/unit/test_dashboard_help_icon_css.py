"""Pin the box of the round help icons so that a page rule cannot stretch them again."""

import re

from jittersim.api.server import DASHBOARD_HTML

CSS = re.sub(r"/\*.*?\*/", "", re.search(r"<style>(.*?)</style>", DASHBOARD_HTML, re.DOTALL).group(1), flags=re.DOTALL)


def rule(selector):
    """The declarations of every rule that lists this selector. A later value wins."""

    found = {}
    seen = False
    for selectors, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", CSS):
        if selector in [item.strip() for item in selectors.split(",")]:
            seen = True
            found.update(
                (name.strip(), value.strip())
                for name, value in (item.split(":", 1) for item in body.split(";") if ":" in item)
            )
    assert seen, selector
    return found


def test_the_icon_is_a_small_round_box_that_no_page_rule_stretches():
    # .content button sets min-height 2.75rem and the icon sits in .content. The icon rule has
    # to be as specific as that rule or it stretches to a tall oval.
    assert rule(".content button")["min-height"] == "2.75rem"
    icon = rule(".content .help-icon")
    assert icon["width"] == icon["height"] == "1.25rem"
    assert icon["min-height"] == "0" and icon["min-width"] == "0"
    assert icon["padding"] == "0" and icon["border-radius"] == "50%"
    assert icon["box-sizing"] == "border-box"
    assert rule(".sidebar .help-icon")["height"] == "1.25rem"


def test_the_touch_target_is_invisible_and_adds_nothing_to_the_layout():
    target = rule(".help-icon::before")
    assert target["position"] == "absolute" and target["content"] == '""'
    assert target["transform"] == "translate(-50%, -50%)"
    assert rule(".content .help-icon")["position"] == "relative"
    # a phone or a touch screen gets the 44px target
    phone = re.search(r"@media \(max-width: 48rem\), \(pointer: coarse\) \{(.*?)\n\}", CSS, re.DOTALL).group(1)
    assert re.search(r"\.help-icon::before \{[^{}]*width: 2\.75rem; height: 2\.75rem;", phone)


def test_the_label_keeps_the_icon_on_its_line():
    assert rule(".field-label")["align-items"] == "center"
    # a wrapping label row would drop the icon onto a line of its own on a narrow phone
    assert "flex-wrap" not in rule(".field-label")
    assert not re.search(r"\.field-label\s*\{[^{}]*flex-wrap", CSS)


def test_the_icon_has_a_hover_and_a_keyboard_focus_state():
    assert re.search(r"\.help-icon:hover,[^{]*\{[^}]*background", CSS)
    focus = rule(".help-icon:focus-visible")
    assert "outline" in focus and "outline-offset" in focus

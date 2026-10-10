"""The dashboard page may only get smaller and simpler: static numbers may not grow.

The numbers are counted from the assembled page and compared with the committed
tests/browser/baselines/gui-stats.json. Nothing here needs a browser, Playwright or Pillow.
When a number is over its limit the failure says how to bring it down or how to raise the budget
on purpose (`python -m tests.browser.gui_stats --update --allow-growth`).
"""

import json
import re

import pytest

from jittersim.api.server import DASHBOARD_HTML
from tests.browser import gui_stats

# The only addresses outside the Pi that the page may name. Both are plain links the person
# clicks, never requests the page makes by itself (the page's policy blocks those at runtime).
# The w3.org address is the SVG namespace name that the speed test QR code needs. It is never fetched.
ALLOWED_OFF_ORIGIN_URLS = {"http://192.168.0.1", "https://duckduckgo.com/?q=", "http://www.w3.org/2000/svg"}


def test_the_dashboard_stays_within_its_committed_static_budget():
    problems = gui_stats.compare(gui_stats.measure(), gui_stats.load_recorded())
    assert not problems, gui_stats.explain(problems)


def test_the_page_names_only_the_allowed_off_origin_addresses():
    found = set(re.findall(r"https?://[^\s\"'<>)`\\]+", DASHBOARD_HTML))
    assert found <= ALLOWED_OFF_ORIGIN_URLS, sorted(found - ALLOWED_OFF_ORIGIN_URLS)


def test_the_baseline_file_is_the_one_the_tool_writes():
    # One number per line: a merge conflict then only touches the numbers in dispute.
    path = gui_stats.BASELINE_PATH
    recorded = gui_stats.load_recorded(path)
    assert path.read_text(encoding="utf-8") == gui_stats.render(recorded)
    assert set(recorded) >= {name for name, _, _ in gui_stats.METRICS}
    assert all(type(value) is int and value >= 0 for value in recorded.values())


def test_measuring_is_deterministic_and_covers_every_metric():
    first, second = gui_stats.measure(), gui_stats.measure()
    assert first == second
    assert set(first) == {name for name, _, _ in gui_stats.METRICS}
    # A number that is always zero would never catch anything: the counts below are the
    # ones the GUI plan lowers, so they must be seen on the real page.
    for name in ("page_bytes", "distinct_colors", "distinct_font_sizes", "important_declarations",
                 "aria_live_in_markup", "aria_live_flip_sites", "window_confirm_calls", "element_ids"):
        assert first[name] > 0, name


@pytest.mark.parametrize("addition, metric", [
    ("<style>.x{color:%s}</style>" % " ".join(
        "#0a0a%02x" % number for number in range(40)), "distinct_colors"),
    ("<style>.x{color:red !important}</style>", "important_declarations"),
    ("<style>:root{--x:1}</style>", "root_rules"),
    ("<script>window.confirm('Sure?')</script>", "window_confirm_calls"),
    ("<script>node.setAttribute('aria-live', 'polite')</script>", "aria_live_flip_sites"),
    ('<div aria-live="polite" onclick="go()" style="color:red"></div>', "aria_live_in_markup"),
    ('<div onclick="go()"></div>', "inline_event_handler_attributes"),
    ('<div style="color:red"></div>', "inline_style_attributes"),
    ("<style>.x{font-size:%s}</style>" % "}.x{font-size:".join(
        "%d.37rem" % number for number in range(1, 8)), "distinct_font_sizes"),
])
def test_a_change_that_adds_to_the_page_is_caught(addition, metric):
    page = DASHBOARD_HTML.replace("</body>", addition + "</body>", 1)
    assert page != DASHBOARD_HTML
    problems = gui_stats.compare(gui_stats.measure(page), gui_stats.load_recorded())
    assert any(problem.startswith(metric + ":") for problem in problems), (metric, problems)
    message = gui_stats.explain(problems)
    assert "--update --allow-growth" in message and metric in message


def test_a_change_that_removes_things_never_fails():
    page = DASHBOARD_HTML.replace("!important", "", 5)
    assert gui_stats.compare(gui_stats.measure(page), gui_stats.load_recorded()) == []


class TestComparison:
    NAMES = [name for name, _, _ in gui_stats.METRICS]

    def numbers(self, **changes):
        numbers = dict.fromkeys(self.NAMES, 100)
        numbers.update(changes)
        return numbers

    def test_growth_within_the_tolerance_passes_and_beyond_it_fails(self):
        recorded = self.numbers()
        assert gui_stats.compare(self.numbers(page_bytes=102), recorded) == []
        problems = gui_stats.compare(self.numbers(page_bytes=103), recorded)
        assert problems == ["page_bytes: now 103, limit 102 (recorded 100, recorded + 2%)"]

    def test_strict_numbers_may_not_grow_at_all(self):
        recorded = self.numbers()
        assert gui_stats.compare(self.numbers(window_confirm_calls=100), recorded) == []
        assert gui_stats.compare(self.numbers(window_confirm_calls=99), recorded) == []
        problems = gui_stats.compare(self.numbers(window_confirm_calls=101), recorded)
        assert problems == ["window_confirm_calls: now 101, limit 100 (recorded 100, no growth allowed)"]

    def test_small_numbers_have_no_room_to_grow_at_all(self):
        assert gui_stats.limit("distinct_font_sizes", 23) == 23
        assert gui_stats.limit("distinct_font_sizes", 50) == 51

    def test_a_number_without_a_baseline_is_reported_not_skipped(self):
        recorded = self.numbers()
        del recorded["distinct_gap_values"]
        problems = gui_stats.compare(self.numbers(), recorded)
        assert problems == ["distinct_gap_values: now 100, but the baseline has no value for it"]

    def test_extra_numbers_in_the_baseline_are_ignored(self):
        assert gui_stats.compare(self.numbers(), {**self.numbers(), "retired_metric": 1}) == []

    def test_update_lowers_but_never_raises_without_permission(self):
        recorded = self.numbers()
        current = self.numbers(page_bytes=90, js_bytes=150, window_confirm_calls=101, css_bytes=101)
        after = gui_stats.updated(current, recorded)
        assert after["page_bytes"] == 90
        assert after["js_bytes"] == 100 and after["window_confirm_calls"] == 100
        assert after["css_bytes"] == 100
        assert gui_stats.compare(current, after) != []

    def test_update_with_growth_raises_only_numbers_over_their_limit(self):
        recorded = self.numbers()
        current = self.numbers(js_bytes=150, css_bytes=101, window_confirm_calls=101, page_bytes=90)
        after = gui_stats.updated(current, recorded, allow_growth=True)
        assert after["js_bytes"] == 150 and after["window_confirm_calls"] == 101
        assert after["css_bytes"] == 100, "growth inside the tolerance is not written down"
        assert after["page_bytes"] == 90
        assert gui_stats.compare(current, after) == []

    def test_update_adds_a_new_number(self):
        recorded = self.numbers()
        del recorded["root_rules"]
        assert gui_stats.updated(self.numbers(root_rules=7), recorded)["root_rules"] == 7

    def test_the_baseline_text_round_trips(self):
        text = gui_stats.render(self.numbers())
        assert json.loads(text)["recorded"] == self.numbers()
        assert text.endswith("\n") and text.count('": 100') == len(self.NAMES)


def test_the_command_updates_a_baseline_file_and_reports_the_state(tmp_path, capsys):
    path = tmp_path / "gui-stats.json"
    assert gui_stats.main(["--baseline", str(path), "--update"]) == 0
    assert gui_stats.load_recorded(path) == gui_stats.measure()
    assert gui_stats.main(["--baseline", str(path)]) == 0
    lowered = dict(gui_stats.load_recorded(path), window_confirm_calls=0)
    path.write_text(gui_stats.render(lowered), encoding="utf-8")
    assert gui_stats.main(["--baseline", str(path)]) == 1
    assert "--update --allow-growth" in capsys.readouterr().err
    assert gui_stats.main(["--baseline", str(path), "--update"]) == 1
    assert gui_stats.main(["--baseline", str(path), "--update", "--allow-growth"]) == 0
    assert gui_stats.load_recorded(path)["window_confirm_calls"] == gui_stats.measure()["window_confirm_calls"]

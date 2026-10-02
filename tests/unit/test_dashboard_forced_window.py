"""The forced window card of the dashboard: its time logic in node and its place in the page."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jittersim.api.server import DASHBOARD_HTML

SOURCE = (Path(__file__).resolve().parents[2] / "src" / "jittersim" / "api" / "forcedwindow.js").read_text()
NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")


def evaluate(expression):
    start = SOURCE.index("const WINDOW_DAYS")
    end = SOURCE.index("function renderForcedWindows")
    program = SOURCE[start:end] + "\nconsole.log(JSON.stringify(" + expression + "));"
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def form(**values):
    base = {"id": "w", "startDay": "4", "startTime": "18:00", "endDay": "0", "endTime": "06:00"}
    return json.dumps({**base, **values})


@NEEDS_NODE
def test_a_window_reads_as_days_and_times():
    saved = {"start_day": 4, "start_minute": 1080, "end_day": 6, "end_minute": 1440}
    assert evaluate("windowText(" + json.dumps(saved) + ")") == "Friday 18:00 to Sunday 24:00"
    assert evaluate("windowClock(5)") == "00:05"
    assert evaluate("windowClock(1439)") == "23:59"


@NEEDS_NODE
def test_the_form_becomes_a_window():
    window = evaluate("windowFromForm(" + form() + ")")
    assert window == {"id": "w", "start_day": 4, "start_minute": 1080, "end_day": 0,
                      "end_minute": 360, "enabled": True}


@NEEDS_NODE
def test_an_end_at_midnight_ends_the_day_before():
    # Friday 18:00 to Monday 00:00 is Friday to Sunday 24:00
    window = evaluate("windowFromForm(" + form(endTime="00:00") + ")")
    assert (window["end_day"], window["end_minute"]) == (6, 1440)
    # and Monday 00:00 wraps back to Sunday
    window = evaluate("windowFromForm(" + form(endDay="2", endTime="00:00") + ")")
    assert (window["end_day"], window["end_minute"]) == (1, 1440)


@NEEDS_NODE
def test_a_window_over_midnight_keeps_its_days():
    window = evaluate("windowFromForm(" + form(startDay="5", startTime="15:00", endDay="6", endTime="05:00") + ")")
    assert (window["start_day"], window["start_minute"], window["end_day"], window["end_minute"]) == (5, 900, 6, 300)


def test_the_card_is_part_of_the_schedules_page_script():
    assert "installForcedWindows();" in DASHBOARD_HTML
    assert "'/api/forced-windows/login'" in DASHBOARD_HTML
    assert DASHBOARD_HTML.index("installForcedWindows();") < DASHBOARD_HTML.index("installFieldHelp();")


def test_the_card_never_shows_or_stores_the_router_password():
    # The password goes to the Pi once. The page only keeps it in the field until it is sent.
    assert "localStorage" not in SOURCE and "sessionStorage" not in SOURCE
    assert re.search(r"element\('forced-window-password'\)\.value = ''", SOURCE)
    assert 'type="password"' in SOURCE and 'autocomplete="off"' in SOURCE

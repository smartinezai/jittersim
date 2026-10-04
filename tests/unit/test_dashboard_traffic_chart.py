"""Exercise the served chart's bounded line geometry and inspection rules."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
DEFINITIONS = SCRIPT[
    SCRIPT.index("    let latestTrafficReferences = {") : SCRIPT.index("    function showMessage(")
]
HELPERS = SCRIPT[
    SCRIPT.index("    function trafficSeriesName(") : SCRIPT.index("    function drawTrafficKey(")
]
FORMATTING = SCRIPT[
    SCRIPT.index("    function formatTrafficRate(") : SCRIPT.index("    function trafficRateMbit(")
]


def run_chart(program):
    result = subprocess.run(
        ["node", "-e", "const TRAFFIC_WINDOW_MS = 60000;\n" + DEFINITIONS + FORMATTING
         + HELPERS + "\n" + program],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
PLOT = "{left:0,right:600,top:0,bottom:100}"


@pytest.mark.parametrize("scope, label", [
    ("shared", "Shared profile upload limit"),
    ("device", "Device upload limit"),
    ("everyone", "Everyone else upload limit"),
])
def test_profile_scope_is_explicit(scope, label):
    assert run_chart(
        "console.log(JSON.stringify(trafficSeriesGeometry([], {profile_upload:10}, "
        + PLOT + ", 100, 60000, " + json.dumps(scope) + ")[0].label));"
    ) == label


def test_empty_history_contains_only_known_references():
    assert run_chart(
        "const lines = trafficSeriesGeometry([], {isp_upload:null,isp_download:100,"
        "profile_upload:0,profile_download:undefined}, " + PLOT + ",100,60000,'shared');"
        "console.log(JSON.stringify(lines.map(line=>({field:line.field,value:line.points[0].value}))));"
    ) == [
        {"field": "isp_download", "value": 100},
        {"field": "profile_upload", "value": 0},
    ]


def test_zero_traffic_is_a_real_sample():
    assert run_chart(
        "const lines = trafficSeriesGeometry([{time:60000,upload:0,download:0}], {}, "
        + PLOT + ",100,60000,'device');"
        "console.log(JSON.stringify(lines.map(line=>line.field)));"
    ) == ["upload", "download"]


@pytest.mark.parametrize("field", ["upload", "profile_upload"])
def test_missing_values_break_the_line_instead_of_inventing_a_connection(field):
    assert run_chart(
        "const field = " + json.dumps(field) + ";"
        "const samples = [10,null,20].map((value,i)=>({time:i*30000,[field]:value}));"
        "const line = trafficSeriesGeometry(samples, {}, " + PLOT
        + ",100,60000,'shared')[0];"
        "console.log(JSON.stringify({points:line.points.length,segments:line.segments.length}));"
    ) == {"points": 2, "segments": 0}


def test_reference_changes_hold_the_previous_limit_until_the_next_record():
    assert run_chart(
        "const lines = trafficSeriesGeometry([{time:0,profile_upload:10},"
        "{time:60000,profile_upload:20}], {}, " + PLOT + ",100,60000,'shared');"
        "const hit = trafficLineHits(lines,{x:300,y:90}," + PLOT + ")[0];"
        "console.log(JSON.stringify({value:hit.point.value,time:hit.point.time}));"
    ) == {"value": 10, "time": 0}


def test_vertical_step_can_be_inspected_without_interpolating_a_limit():
    assert run_chart(
        "const lines = trafficSeriesGeometry([{time:0,profile_upload:10},"
        "{time:60000,profile_upload:20}], {}, " + PLOT + ",100,60000,'shared');"
        "const hit = trafficLineHits(lines,{x:600,y:81}," + PLOT + ")[0];"
        "console.log(JSON.stringify({value:hit.point.value,time:hit.point.time}));"
    ) == {"value": 20, "time": 60000}


def test_close_overlapping_lines_are_reported_together():
    assert run_chart(
        "const samples = [0,60000].map(time=>({time,upload:10,download:11,profile_upload:40}));"
        "const lines = trafficSeriesGeometry(samples,{}," + PLOT + ",100,60000,'shared');"
        "console.log(JSON.stringify(trafficLineHits(lines,{x:300,y:90}," + PLOT
        + ").map(hit=>hit.field)));"
    ) == ["upload", "download"]


def test_another_line_within_the_hit_radius_does_not_override_the_nearest():
    assert run_chart(
        "const samples = [0,60000].map(time=>({time,upload:10,download:16}));"
        "const lines = trafficSeriesGeometry(samples,{}," + PLOT + ",100,60000,'shared');"
        "console.log(JSON.stringify(trafficLineHits(lines,{x:300,y:90}," + PLOT
        + ").map(hit=>hit.field)));"
    ) == ["upload"]


@pytest.mark.parametrize("pointer", ["null", "{x:-1,y:90}", "{x:601,y:90}", "{x:300,y:10}"])
def test_empty_plot_space_does_not_select_a_line(pointer):
    assert run_chart(
        "const lines = trafficSeriesGeometry([], {profile_upload:10}, " + PLOT
        + ",100,60000,'shared');console.log(JSON.stringify(trafficLineHits(lines,"
        + pointer + "," + PLOT + ")));"
    ) == []


def test_a_single_sample_is_inspectable():
    assert run_chart(
        "const lines = trafficSeriesGeometry([{time:60000,upload:10}],{}," + PLOT
        + ",100,60000,'device');console.log(JSON.stringify(trafficLineHits(lines,"
        "{x:598,y:90}," + PLOT + ").map(hit=>hit.point.value)));"
    ) == [10]


def test_removed_reference_remains_identifiable_in_retained_history():
    assert run_chart(
        "latestTrafficReferences.profile_upload = null;"
        "const line = trafficSeriesGeometry([{time:10000,profile_upload:10},"
        "{time:20000,profile_upload:null}],{}," + PLOT + ",100,30000,'shared')[0];"
        "console.log(JSON.stringify(trafficLineDetail(line,line.points[0],30000)));"
    ) == ("Shared profile upload limit: 10.00 Mbit/s · applied profile limit"
          " · recorded 20 seconds ago · no current limit")


def test_measured_readout_identifies_an_interval_and_units():
    assert run_chart(
        "const line = trafficSeriesGeometry([{time:10000,upload:0.4}],{}," + PLOT
        + ",100,18000,'device')[0];"
        "console.log(JSON.stringify(trafficLineDetail(line,line.points[0],18000)));"
    ) == "Upload traffic: 400.0 kbit/s · measured at the Pi over the interval ending 8 seconds ago"


def test_fallback_reference_age_is_the_last_read_not_a_claim_about_a_change():
    assert run_chart(
        "trafficReferencesReadAt = 10000;"
        "const line = trafficSeriesGeometry([],{isp_download:100}," + PLOT
        + ",100,18000,'shared')[0];"
        "console.log(JSON.stringify(trafficLineDetail(line,line.points[0],18000)));"
    ) == "ISP download plan: 100.00 Mbit/s · ISP plan reference · last read 8 seconds ago"


def test_pointer_redraws_share_one_pending_frame():
    start = SCRIPT.index("    function requestTrafficChartDraw(")
    end = SCRIPT.index("\n    function ", start + 1)
    assert run_chart(
        "let requests = 0; let callback; let draws = 0;"
        "const window = {requestAnimationFrame(fn) {callback = fn; return ++requests;}};"
        "function drawTrafficChart() {draws += 1;}"
        + SCRIPT[start:end]
        + "for (let i=0;i<100;i++) requestTrafficChartDraw();"
        "const before = requests; callback(); requestTrafficChartDraw();"
        "console.log(JSON.stringify({before,requests,draws}));"
    ) == {"before": 1, "requests": 2, "draws": 1}

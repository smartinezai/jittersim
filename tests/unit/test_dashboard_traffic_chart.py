"""Exercise the served chart's bounded line geometry and inspection rules."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML
from tests.dashboard_script import function_source

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


@pytest.mark.parametrize(("peak", "maximum"), [
    (0, 0.1), (0.08, 0.1), (0.1, 0.2), (0.2, 0.5),
    (2, 5), (4, 5), (5, 10), (40, 50), (100, 200),
])
def test_data_axis_uses_one_two_five_steps_with_headroom(peak, maximum):
    assert run_chart(
        "console.log(JSON.stringify(trafficAxisMaximum([{upload:0,download:"
        + json.dumps(peak) + "}])));"
    ) == maximum


def test_fast_references_do_not_flatten_slow_measured_traffic():
    result = run_chart(
        "const samples = [{time:60000,upload:0.2,download:2,isp_download:100}];"
        "const maximum = trafficAxisMaximum(samples);"
        "const lines = trafficSeriesGeometry(samples,{}," + PLOT + ",maximum,60000,'device');"
        "const reference = lines.find(line=>line.field==='isp_download');"
        "const hit = trafficLineHits(lines,reference.marker," + PLOT + ")[0];"
        "console.log(JSON.stringify({maximum,fill:2/maximum,marker:reference.marker.value,"
        "hit:hit.point.value,above:reference.points[0].y<0}));"
    )
    assert result == {"maximum": 5, "fill": 0.4, "marker": 100, "hit": 100, "above": True}


def test_missing_measurements_use_the_minimum_axis():
    assert run_chart(
        "console.log(JSON.stringify(trafficAxisMaximum([{upload:null,download:undefined,"
        "isp_download:100,profile_upload:200}])));"
    ) == 0.1


def test_an_in_range_limit_keeps_its_real_line_without_a_marker():
    assert run_chart(
        "const line = trafficSeriesGeometry([],{isp_download:5}," + PLOT
        + ",5,60000,'device')[0];console.log(JSON.stringify({marker:!!line.marker,"
        "y:line.points[0].y,value:line.points[0].value}));"
    ) == {"marker": False, "y": 0, "value": 5}


DOM = """
const nodes = {};
function element(id) {
  return nodes[id] || (nodes[id] = {textContent:'',setAttribute(key,value){this[key]=value;},
    replaceChildren(...children){this.children=children;}});
}
const document = {createElement(tag){return {tag,children:[],appendChild(child){this.children.push(child);}};}};
"""


def test_sample_text_is_bounded_newest_first_and_preserves_zero_rates():
    assert run_chart(
        DOM + "const TRAFFIC_MAX_POINTS=30;"
        "const trafficSamples=Array.from({length:40},(_,i)=>({recordedAt:1000*i,"
        "intervalSeconds:2,upload:i,download:0}));"
        "updateTrafficText();"
        "console.log(JSON.stringify({rows:nodes['traffic-sample-rows'].children.length,"
        "first:nodes['traffic-sample-rows'].children[0].children[1].textContent,"
        "last:nodes['traffic-sample-rows'].children[29].children[1].textContent,"
        "zero:nodes['traffic-sample-rows'].children[0].children[2].textContent,"
        "latest:nodes['traffic-latest-rates'].textContent}));"
    ) == {"rows": 30, "first": "39.00 Mbit/s", "last": "10.00 Mbit/s",
          "zero": "0.0 kbit/s", "latest": "Latest interval at the Pi: upload 39.00 Mbit/s, download 0.0 kbit/s."}


def test_pause_freezes_history_and_resume_clears_the_counter_baseline():
    assert run_chart(
        DOM + "let trafficPausedAt=null;let trafficSamplingAllowed=true;"
        "let trafficSamplingRevision=0;let previousTrafficSample={time:1};"
        "let trafficSamples=[{time:1}];let timersCleared=0;let draws=0;let reads=0;"
        "const performance={now:()=>2000};"
        "function clearTrafficTimer(){timersCleared++;}"
        "function drawTrafficChart(){draws++;}"
        "function resetTrafficHistory(){trafficSamples=[];previousTrafficSample=null;}"
        "async function pollTraffic(){reads++;}function scheduleTrafficPoll(){}"
        "toggleTrafficSampling();const paused={at:trafficPausedAt,reads,"
        "samples:trafficSamples.length,pressed:nodes['traffic-pause']['aria-pressed']};"
        "toggleTrafficSampling();console.log(JSON.stringify({paused,reads,"
        "samples:trafficSamples.length,baseline:previousTrafficSample,"
        "pressed:nodes['traffic-pause']['aria-pressed'],timersCleared}));"
    ) == {"paused": {"at": 2000, "reads": 0, "samples": 1, "pressed": "true"},
          "reads": 1, "samples": 0, "baseline": None, "pressed": "false", "timersCleared": 2}


def test_paused_sampling_stays_quiet_on_visibility_and_page_changes():
    sources = "\n".join(("async " if name in {"pollTraffic", "startTrafficSampling"} else "")
                        + function_source(name) for name in (
        "pollTraffic", "scheduleTrafficPoll", "syncTrafficSampling", "startTrafficSampling"
    ))
    assert run_chart(
        DOM + "let trafficPausedAt=20;let trafficSamplingAllowed=true;"
        "let trafficRequestInFlight=false;let trafficFailures=0;let calls=0;"
        "function trafficGraphShown(){return true;}"
        "function clearTrafficTimer(){}const window={setTimeout(){calls++;}};"
        "function readTraffic(){calls++;}function resetTrafficHistory(){calls++;}"
        + sources + "\n(async()=>{await pollTraffic();scheduleTrafficPoll();"
        "syncTrafficSampling();await startTrafficSampling();"
        "console.log(JSON.stringify(calls));})();"
    ) == 0


@pytest.mark.parametrize("reject", [False, True])
def test_a_response_from_before_pause_cannot_modify_the_snapshot(reject):
    assert run_chart(
        "let trafficPausedAt=null;let trafficSamplingAllowed=true;"
        "let trafficRequestInFlight=false;let trafficReadingInFlight=null;let trafficFailures=0;"
        "let previousTrafficSample=null;let adminTokenRevision=1;let adminTokenVerified=true;let trafficSamplingRevision=0;"
        "function trafficGraphShown(){return true;}let finish;"
        "function readTraffic(){return new Promise((resolve,reject)=>{finish="
        + ("reject" if reject else "resolve") + ";});}"
        + "async " + function_source("pollTraffic")
        + "\n(async()=>{const pending=pollTraffic();trafficSamplingRevision++;"
        "trafficPausedAt=2000;finish(" + ("new Error('offline')" if reject else "{}") + ");"
        "await pending;console.log(JSON.stringify({inFlight:trafficRequestInFlight,failures:trafficFailures}));})();"
    ) == {"inFlight": False, "failures": 0}



def test_reset_clears_sample_text_and_offscale_text_even_when_canvas_is_hidden():
    assert run_chart(
        DOM + "const TRAFFIC_MAX_POINTS=30;let previousTrafficSample={time:1};"
        "let trafficSamples=[{time:1}];function drawTrafficChart(){}"
        "element('traffic-offscale').textContent='Old device limit';"
        + function_source("resetTrafficHistory")
        + "resetTrafficHistory();console.log(JSON.stringify({"
        "rows:nodes['traffic-sample-rows'].children.length,"
        "offscale:nodes['traffic-offscale'].textContent,hidden:nodes['traffic-offscale'].hidden,"
        "latest:nodes['traffic-latest-rates'].textContent,baseline:previousTrafficSample}));"
    ) == {"rows": 0, "offscale": "", "hidden": True,
          "latest": "Waiting for a measured traffic interval.", "baseline": None}



POLL_STATE = DOM + """
let trafficPausedAt=null, trafficSamplingAllowed=true, trafficSamplingRevision=0;
let trafficRequestInFlight=false, trafficReadingInFlight=null, latestTrafficReading=null;
let adminTokenRevision=1, adminTokenVerified=true, trafficFailures=0;
let previousTrafficSample=null, trafficSamples=[], now=0, frame, requests=0;
const TRAFFIC_MAX_POINTS=30, TRAFFIC_FAILURES_BEFORE_CLEARING=3;
const EVERYONE_ELSE_TRAFFIC='__everyone_else__';
const performance={now:()=>now};
element('traffic-device').value='phone';
function trafficGraphShown(){return true;}
function trafficReferencesFromResponse(){return {};}
function resetTrafficHistory(){trafficSamples=[];previousTrafficSample=null;}
function drawTrafficChart(){}
function stopTrafficSampling(message){throw new Error(message);}
let respond=()=>Promise.resolve(frame);
function request(){requests++;return respond();}
""" + "\n".join(function_source(name) for name in (
    "readTraffic", "formatPacketRate", "mergedTrafficCounters",
    "selectedTrafficTotals", "selectedQueueTotals"
)) + "\nasync " + function_source("pollTraffic")


def counter_frame(browser_ms, sampled_at, counter=None):
    if counter is None:
        counter = round(sampled_at * 1_000_000)
    return {"browser_ms": browser_ms, "sampled_at": sampled_at, "available": True,
            "devices": {"phone": {"upload_bytes": counter, "download_bytes": counter,
                                    "upload_packets": counter / 1000,
                                    "download_packets": counter / 1000}}, "queues": {}}


def polled_frames(frames):
    return run_chart(
        POLL_STATE + "const frames=" + json.dumps(frames) + ";"
        "(async()=>{for(frame of frames){now=frame.browser_ms;await pollTraffic();}"
        "console.log(JSON.stringify({samples:trafficSamples,baseline:previousTrafficSample,"
        "status:nodes['traffic-status'].textContent,"
        "latest:nodes['traffic-latest-rates']?.textContent,requests}));})();"
    )


@pytest.mark.parametrize("server_gap", [1.1, 2, 2.9])
def test_rate_interval_uses_server_samples_despite_different_receipt_gaps(server_gap):
    result = polled_frames([counter_frame(0, 100), counter_frame(2000, 100 + server_gap)])
    sample, = result["samples"]
    assert sample["upload"] == pytest.approx(8)
    assert sample["download"] == pytest.approx(8)
    assert sample["intervalSeconds"] == pytest.approx(server_gap)
    assert sample["recordedAt"] == pytest.approx((100 + server_gap) * 1000)
    assert sample["time"] == 2000  # Plot position stays in the browser's monotonic clock.


def test_a_repeated_server_sample_keeps_rates_and_the_counter_baseline():
    result = polled_frames([
        counter_frame(0, 100), counter_frame(2000, 102), counter_frame(4000, 102),
        counter_frame(6000, 104),
    ])
    assert [sample["upload"] for sample in result["samples"]] == [8, 8]
    assert [sample["time"] for sample in result["samples"]] == [2000, 6000]
    assert result["samples"][-1]["intervalSeconds"] == 2


def test_clock_reversal_restarts_instead_of_drawing_a_spike():
    result = polled_frames([
        counter_frame(0, 100, 0), counter_frame(2000, 102, 2_000_000),
        counter_frame(4000, 90, 3_000_000), counter_frame(6000, 92, 5_000_000),
    ])
    sample, = result["samples"]
    assert sample["upload"] == 8
    assert sample["time"] == 6000
    assert sample["intervalSeconds"] == 2


@pytest.mark.parametrize("invalid", [None, "102", True])
def test_invalid_sample_time_clears_the_baseline_until_a_new_valid_pair(invalid):
    result = polled_frames([
        counter_frame(0, 100), counter_frame(2000, 102),
        counter_frame(4000, invalid, 103_000_000), counter_frame(6000, 106),
        counter_frame(8000, 108),
    ])
    sample, = result["samples"]
    assert sample["upload"] == 8
    assert sample["time"] == 8000


def test_counter_reset_still_restarts_with_server_timestamps():
    result = polled_frames([
        counter_frame(0, 100, 10_000_000), counter_frame(2000, 102, 12_000_000),
        counter_frame(4000, 104, 0), counter_frame(6000, 106, 2_000_000),
    ])
    sample, = result["samples"]
    assert sample["upload"] == 8
    assert sample["time"] == 6000


def test_server_clock_does_not_change_the_thirty_sample_browser_retention_bound():
    result = polled_frames([counter_frame(i * 2000, 1_000_000 + i * 2) for i in range(35)])
    assert len(result["samples"]) == 30
    assert result["samples"][0]["time"] == 10000
    assert result["samples"][-1]["time"] == 68000
    assert all(sample["upload"] == 8 for sample in result["samples"])


@pytest.mark.parametrize("older_failed", [False, True])
def test_fresh_graph_waits_for_an_older_workspace_flight_then_requests_a_new_baseline(older_failed):
    result = run_chart(
        POLL_STATE + "let finish;respond=()=>requests===1?new Promise((resolve,reject)=>"
        "{finish=" + ("reject" if older_failed else "resolve") + ";}):Promise.resolve(frame);"
        "(async()=>{const workspace=readTraffic(0);workspace.catch(()=>null);"
        "frame=" + json.dumps(counter_frame(10000, 112)) + ";now=10000;"
        "const resumed=pollTraffic();const before=requests;finish("
        + ("new Error('older workspace read failed')" if older_failed
           else json.dumps(counter_frame(5000, 105))) + ");await resumed;"
        "const baseline=previousTrafficSample.sampledAt;frame="
        + json.dumps(counter_frame(12000, 114)) + ";now=12000;await pollTraffic();"
        "console.log(JSON.stringify({before,requests,baseline,rate:trafficSamples[0].download}));})();"
    )
    assert result == {"before": 1, "requests": 3, "baseline": 112, "rate": 8}


def test_pausing_again_while_waiting_for_a_workspace_flight_starts_no_new_request():
    assert run_chart(
        POLL_STATE + "let finish;respond=()=>new Promise(resolve=>{finish=resolve;});"
        "(async()=>{const workspace=readTraffic(0);const resumed=pollTraffic();"
        "trafficPausedAt=10000;trafficSamplingRevision++;finish("
        + json.dumps(counter_frame(5000, 105)) + ");await resumed;await workspace;"
        "console.log(JSON.stringify({requests,baseline:previousTrafficSample,inFlight:trafficRequestInFlight}));})();"
    ) == {"requests": 1, "baseline": None, "inFlight": False}

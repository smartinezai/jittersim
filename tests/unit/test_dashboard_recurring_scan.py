"""Run the served capture lifecycle with a fake clock and explicit POST replies."""

import json
import shutil
import subprocess

import pytest

from tests.dashboard_script import SCRIPT, function_source

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")

HARNESS = r"""
const hooks=[], privateDataLockHooks=hooks;
const button={disabled:false,textContent:'',attrs:{},setAttribute(k,v){this.attrs[k]=v;},
  removeAttribute(k){delete this.attrs[k];}};
const summary={textContent:'Latest successful scan: 2 displayed names new to list.'};
const status={textContent:'Scanning off.'}, panel={hidden:false};
function element(id){return id==='observation-scan-summary'?summary:
  id==='observation-capture-status'?status:id==='private-dashboard'?panel:button;}
const markers=[{removed:false,remove(){this.removed=true;}}];
const document={hidden:false,querySelectorAll:()=>markers};
let page='traffic', adminTokenVerified=true, adminTokenRevision=1, helperConfigured=true;
let observationRequestInFlight=false;
function currentPage(){return page;}
let clock=0, nextTimer=0;
const timers=new Map();
const window={setTimeout(fn,ms){const id=++nextTimer;timers.set(id,{fn,at:clock+ms});return id;},
  clearTimeout(id){timers.delete(id);}};
const calls=[],pending=[],messages=[],draws=[];
function request(path,method){calls.push([path,method]);return new Promise((resolve,reject)=>pending.push({resolve,reject}));}
function showMessage(text,kind){messages.push([text,kind]);}
function renderServiceListObserved(){}
function renderObservations(data){draws.push(data);observationListKeys=data.available?
  new Set((data.devices||[]).flatMap(d=>(d.domains||[]).map(o=>observationNameKey(d.ipv4_address,o.domain)))):null;}
const flush=async()=>{for(let i=0;i<10;i++)await Promise.resolve();};
async function advance(ms){const end=clock+ms;for(;;){const next=[...timers.entries()]
  .filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;
  clock=next[1].at;timers.delete(next[0]);next[1].fn();await flush();}clock=end;await flush();}
const old={ipv4_address:'192.168.50.124',domain:'old.example'};
const fresh={ipv4_address:'192.168.50.124',domain:'new.example'};
const anotherDevice={ipv4_address:'192.168.50.125',domain:'old.example'};
const list={available:true,devices:[{ipv4_address:old.ipv4_address,domains:[old,fresh]},
  {ipv4_address:anotherDevice.ipv4_address,domains:[anotherDevice]}]};
function baseline(){observationListKeys=new Set([observationNameKey(old.ipv4_address,old.domain)]);}
async function finishCapture(){pending.shift().resolve({captured_observations:1,observations:[fresh]});
  await flush();if(pending.length)pending.shift().resolve(list);await flush();}
function posts(){return calls.filter(c=>c[1]==='POST').length;}
"""

NAMES = (
    "observationNameKey", "newObservationScanKeys", "newDisplayedObservationCount",
    "observationScanEligible", "updateObservationScanButton", "stopObservationScanning",
    "syncObservationScanning", "observationScanCurrent", "scheduleObservationScan",
    "toggleObservationScanning", "scanObservations", "loadObservations",
)


def run_recurring_scan(steps, *, active=False):
    private_start = SCRIPT.index("    let observationListKeys = null;")
    private_end = SCRIPT.index("    let randomWebsiteAnalyticsRequestVersion", private_start)
    state_start = SCRIPT.index("    const OBSERVATION_SCAN_INTERVAL_MS =")
    state_end = SCRIPT.index("    function observationScanEligible", state_start)
    functions = "\n".join(
        ("async " if name in {"scanObservations", "loadObservations"} else "")
        + function_source(name) for name in NAMES
    )
    program = (HARNESS + SCRIPT[private_start:private_end] + SCRIPT[state_start:state_end]
               + functions + ("\nobservationScanning=true;" if active else "")
               + "\n(async()=>{\n" + steps
               + "\n})().catch(e=>{console.error(e.stack);process.exit(1);});")
    result = subprocess.run(["node", "-e", program], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_default_off_and_saved_get_polling_do_not_capture():
    assert run_recurring_scan("""
      await advance(300000);const read=loadObservations();pending.shift().resolve(list);await read;
      console.log(JSON.stringify({posts:posts(),active:observationScanning,timers:timers.size}));
    """) == {"posts": 0, "active": False, "timers": 0}


def test_start_posts_immediately_and_repeats_thirty_seconds_after_completion_only():
    out = run_recurring_scan("""
      const first=toggleObservationScanning();const immediate=posts();
      await advance(90000);const noOverlap=posts();await finishCapture();await first;
      const next=[...timers.values()][0].at-clock;
      await advance(29999);const before=posts();await advance(1);const after=posts();
      await advance(120000);const pendingPosts=posts();await finishCapture();
      console.log(JSON.stringify({immediate,noOverlap,next,before,after,pendingPosts,
        completionDelay:[...timers.values()][0].at-clock,messages}));
    """)
    assert out == {"immediate": 1, "noOverlap": 1, "next": 30000, "before": 1,
                   "after": 2, "pendingPosts": 2, "completionDelay": 30000, "messages": []}


def test_stop_remains_clickable_during_capture_and_old_flight_blocks_a_new_start():
    out = run_recurring_scan("""
      const first=toggleObservationScanning();const stopUsable=!button.disabled&&button.textContent==='Stop scanning';
      toggleObservationScanning();const stopped=button.disabled&&button.textContent==='Start scanning';
      toggleObservationScanning();await scanObservations();const during=posts();
      pending.shift().resolve({captured_observations:1,observations:[fresh]});await first;
      const reenabled=!button.disabled;await advance(300000);const after=posts();
      toggleObservationScanning();console.log(JSON.stringify({stopUsable,stopped,during,reenabled,after,
        restarted:posts(),draws,latest:latestObservationScan}));
    """)
    assert out == {"stopUsable": True, "stopped": True, "during": 1, "reenabled": True,
                   "after": 1, "restarted": 2, "draws": [], "latest": None}


@pytest.mark.parametrize("loss", ["document.hidden=true", "page='devices'", "adminTokenVerified=false",
                                  "helperConfigured=false", "panel.hidden=true"])
def test_eligibility_loss_stops_without_automatic_resume(loss):
    out = run_recurring_scan("""
      const first=toggleObservationScanning();await finishCapture();await first;
    """ + loss + ";syncObservationScanning();" + """
      const stopped=!observationScanning&&timers.size===0;
      document.hidden=false;page='traffic';adminTokenVerified=true;helperConfigured=true;panel.hidden=false;
      syncObservationScanning();await advance(300000);
      console.log(JSON.stringify({stopped,posts:posts(),active:observationScanning}));
    """)
    assert out == {"stopped": True, "posts": 1, "active": False}


@pytest.mark.parametrize("loss", ["document.hidden=true", "page='profiles'", "adminTokenVerified=false",
                                  "helperConfigured=false", "panel.hidden=true"])
def test_every_post_requires_current_visible_verified_access(loss):
    assert run_recurring_scan(loss + ";toggleObservationScanning();await advance(300000);"
                              "console.log(JSON.stringify({posts:posts()}));") == {"posts": 0}


@pytest.mark.parametrize("phase", ["capture", "read"])
@pytest.mark.parametrize("reject", [False, True])
def test_same_token_private_clear_suppresses_pending_capture_or_read(phase, reject):
    steps = "baseline();const first=toggleObservationScanning();\n"
    if phase == "read":
        steps += "pending.shift().resolve({captured_observations:1,observations:[fresh]});await flush();\n"
    steps += "hooks.forEach(h=>h());\n"
    steps += ("pending.shift().reject(new Error('private capture failure'));" if reject else
              "pending.shift().resolve(" + ("list" if phase == "read" else
              "{captured_observations:1,observations:[fresh]}") + ");")
    out = run_recurring_scan(steps + """
      await first;await advance(300000);
      console.log(JSON.stringify({draws,messages,posts:posts(),active:observationScanning,
        latest:latestObservationScan,summary:summary.textContent}));
    """)
    assert out == {"draws": [], "messages": [], "posts": 1, "active": False,
                   "latest": None, "summary": "Saved results refresh automatically."}


def test_failed_captures_back_off_then_stop_for_explicit_restart_without_toasts():
    out = run_recurring_scan("""
      const first=toggleObservationScanning();pending.shift().reject(new Error('capture unavailable'));await first;
      const one=[...timers.values()][0].at-clock;
      await advance(60000);pending.shift().reject(new Error('capture unavailable'));await flush();
      const two=[...timers.values()][0].at-clock;
      await advance(120000);pending.shift().reject(new Error('capture unavailable'));await flush();
      await advance(300000);console.log(JSON.stringify({one,two,posts:posts(),active:observationScanning,
        timers:timers.size,status:status.textContent,messages}));
    """)
    assert out["one"] == 60000 and out["two"] == 120000
    assert out["posts"] == 3 and out["active"] is False and out["timers"] == 0
    assert "Start scanning to retry" in out["status"] and out["messages"] == []


@pytest.mark.parametrize("status_code", [401, 403])
def test_denied_capture_never_retries(status_code):
    out = run_recurring_scan("""
      const first=toggleObservationScanning();const error=new Error('Access denied');
      error.status=""" + str(status_code) + ";pending.shift().reject(error);await first;" + """
      await advance(300000);console.log(JSON.stringify({posts:posts(),active:observationScanning}));
    """)
    assert out == {"posts": 1, "active": False}


def test_stopped_queued_callback_cannot_capture_in_a_new_run():
    out = run_recurring_scan("""
      const first=toggleObservationScanning();await finishCapture();await first;
      const old=[...timers.values()][0].fn;toggleObservationScanning();
      toggleObservationScanning();const owner=observationCaptureOwner;old();await flush();
      console.log(JSON.stringify({posts:posts(),sameOwner:owner===observationCaptureOwner,
        pending:observationRequestInFlight}));
    """)
    assert out == {"posts": 2, "sameOwner": True, "pending": True}


def test_navigation_visibility_and_private_hooks_are_wired_to_stop_lifecycle():
    from pathlib import Path
    navigation = (Path(__file__).parents[2] / "src/jittersim/api/dashboard-navigation.js").read_text()
    assert 'if (typeof syncObservationScanning === "function") syncObservationScanning();' in navigation
    assert 'document.addEventListener("visibilitychange", syncObservationScanning)' in SCRIPT
    assert 'window.addEventListener("pagehide", () => stopObservationScanning())' in SCRIPT
    assert 'addEventListener("click", toggleObservationScanning)' in SCRIPT


@pytest.mark.parametrize("loss", ["document.hidden=true", "page='devices'", "helperConfigured=false"])
def test_pending_capture_is_not_delivered_or_resumed_after_context_loss(loss):
    out = run_recurring_scan("const first=toggleObservationScanning();" + loss + """;
      syncObservationScanning();document.hidden=false;page='traffic';helperConfigured=true;
      pending.shift().resolve({captured_observations:1,observations:[fresh]});await first;
      await advance(300000);console.log(JSON.stringify({posts:posts(),draws,
        active:observationScanning,latest:latestObservationScan}));
    """)
    assert out == {"posts": 1, "draws": [], "active": False, "latest": None}

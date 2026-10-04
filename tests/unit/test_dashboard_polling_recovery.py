"""Exercise failed and overlapping reads in the actual served dashboard functions."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")
SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)


def block(start, end):
    first = SCRIPT.index(start)
    return SCRIPT[first:SCRIPT.index(end, first)]


def run(source, body):
    result = subprocess.run(
        [NODE, "-"], input=source + "\n(async () => {\n" + body +
        "\nconsole.log('CASE-COMPLETE');\n})().catch(e => {console.error(e); process.exit(1);});",
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[-1] == "CASE-COMPLETE", result.stdout
    return json.loads(lines[-2])


def test_fulfilled_diagnostics_failure_remains_pending():
    source = """
const ADMIN_TOOLS_PAGES = ['traffic', 'diagnostics'];

let adminTokenVerified = true;
let adminTokenRevision = 0;
let dashboardLoaded = true;
const document = {hidden:false};
async function loadAdminTools() {return {ok:false, message:'read failed'};}
""" + block("    let adminToolsStale =", "    async function refreshDashboard(") + block("function readOldAdminTools(", "function startEditing(")
    actual = run(source, "invalidateAdminTools(); await readOldAdminTools('diagnostics'); await Promise.resolve(); "
                 "console.log(JSON.stringify({pending:adminToolsStale}));")
    assert actual["pending"] is True


def test_successful_read_restores_availability_without_repeating_unknown_save():
    source = r"""
const nodes = new Map();
function element(id) {
  if (!nodes.has(id)) nodes.set(id,{value:'fixture-token',textContent:'',className:''});
  return nodes.get(id);
}
const document = {getElementById:element};
function renderRuntimeText() {element('runtime-summary').textContent='connected';}
let adminTokenRevision=0, adminTokenVerified=true, adminTokenChecking=false;
let pendingFormSubmitButton=null;
function lostConnectionError(e) {e.connectionLost=true; return e;}
let timer;
globalThis.setTimeout = callback => {timer=callback; return 1;};
globalThis.clearTimeout = () => {};
let writes=0;
async function fetch(path, options) {
  if (options.method==='POST') {
    writes++;
    return await new Promise((resolve,reject) =>
      options.signal.addEventListener('abort', () => reject(new Error('aborted'))));
  }
  return {ok:true,status:200,json:async()=>({system:{version:'1.0.0'}})};
}
""" + block("const PI_NOT_ANSWERING =", "async function show(")
    actual = run(source, "const save=request('/api/profiles','POST',{}); timer(); "
                 "let outcome; try {await save;} catch(e) {outcome=e.message;} "
                 "await request('/api/health','GET'); "
                 "console.log(JSON.stringify({text:element('runtime-summary').textContent,"
                 "writes,outcome,unknown:uncertainWritePaths.has('/api/profiles')}));")
    assert actual["text"] != "Pi not answering, retrying"
    assert actual["writes"] == 1
    assert "result is unknown" in actual["outcome"]
    assert actual["unknown"] is True


def test_diagnostics_invalidated_during_read_remains_pending_and_single_flight():
    source = """
const ADMIN_TOOLS_PAGES=['traffic','diagnostics'];
let adminTokenVerified=true,adminTokenRevision=0,dashboardLoaded=true;
const document={hidden:false};
let resolve,reads=0;
async function loadAdminTools() {reads++;return await new Promise(r=>resolve=r);}
""" + block("    let adminToolsStale =", "    async function refreshDashboard(") + block(
        "function readOldAdminTools(", "function startEditing(")
    actual = run(source, "invalidateAdminTools(); const first=readOldAdminTools('diagnostics'); "
                 "const second=readOldAdminTools('traffic'); invalidateAdminTools(); "
                 "resolve({ok:true}); await Promise.all([first,second]); "
                 "console.log(JSON.stringify({reads,pending:adminToolsStale}));")
    assert actual == {"reads": 1, "pending": True}


def test_old_diagnostics_rejection_cannot_change_a_new_signin():
    source = """
const ADMIN_TOOLS_PAGES=['traffic','diagnostics'];
let adminTokenVerified=true,adminTokenRevision=0,dashboardLoaded=true;
const document={hidden:false};
let reject;
async function loadAdminTools() {return await new Promise((r,j)=>reject=j);}
""" + block("    let adminToolsStale =", "    async function refreshDashboard(") + block(
        "function readOldAdminTools(", "function startEditing(")
    actual = run(source, "invalidateAdminTools(); const old=readOldAdminTools('diagnostics'); "
                 "adminTokenRevision++; adminToolsStale=false; reject(new Error('old read')); "
                 "await old; console.log(JSON.stringify({pending:adminToolsStale}));")
    assert actual["pending"] is False


@pytest.mark.parametrize("path", ["/api/profiles-new", "/api/devices-export", "/api/apply-unrelated"])
def test_unknown_route_prefix_requires_full_refresh(path):
    source = block("const READ_AFTER_WRITE = [", "// The diagnostics text")
    actual = run(source, "const names=readsAfterWrites([" + json.dumps(path) +
                 "]); console.log(JSON.stringify(names===null?null:Array.from(names)));")
    assert actual is None


def test_cached_private_traffic_belongs_to_current_credentials():
    source = """
let adminTokenRevision=0,adminTokenVerified=true;
let reads=0;
async function request() {reads++;return {devices:{current:{bytes:7}}};}
""" + block("    let latestTrafficReading =", "    let trafficFailures =")
    actual = run(source, "await readTraffic(1500); adminTokenRevision++; "
                 "const next=await readTraffic(1500); console.log(JSON.stringify({reads,next}));")
    assert actual["reads"] == 2


def test_old_failed_connected_read_cannot_restore_a_private_list():
    source = """
let adminTokenRevision=0,adminTokenVerified=true;
let connectedDevicesRequestInFlight=false,lastConnectedData={available:true,devices:[{name:'private'}]};
let connectedRenderKey='old',connectedFailures=0;
let rendered=0;
function element() {return {textContent:''};}
function interactionInside() {return false;}
function renderConnectedDevices() {rendered++;}
const connectedDataHooks=[];
const CONNECTED_FAILURES_BEFORE_UNAVAILABLE=3;
const rejects=[];
async function request() {return await new Promise((r,j)=>rejects.push(j));}
""" + block("    async function loadConnectedDevices(", "    const connectedDataHooks =")
    actual = run(source, "const old=loadConnectedDevices(); adminTokenRevision++; "
                 "adminTokenVerified=false; lastConnectedData=null; "
                 "for (const reject of rejects) reject(new Error('old failure')); await old; "
                 "console.log(JSON.stringify({rendered,active:connectedDevicesRequestInFlight}));")
    assert actual == {"rendered": 0, "active": False}

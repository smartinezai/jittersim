"""The served access readers keep full editor values and drafts around summary polls."""

import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML
from tests.unit.test_dashboard_refresh_after_write import LOADERS

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")
SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)


def block(start, end):
    first = SCRIPT.index(start)
    return SCRIPT[first:SCRIPT.index(end, first)]


def run(body):
    harness = r"""
const assert = require('node:assert/strict');
const nodes = new Map();
function makeNode(text = '') {
  return {textContent:text, value:'', checked:false, hidden:false, disabled:false,
    dataset:{}, children:[], append(...items) {this.children.push(...items)},
    replaceChildren(...items) {this.children=items}};
}
function element(id) {
  if (!nodes.has(id)) { const node=makeNode(); node.id=id; nodes.set(id,node); }
  return nodes.get(id);
}
function textNode(tag, text) {return makeNode(text)}
function actionButton(text) {return makeNode(text)}
const devices=[{id:'alice',name:'Alice',mac_address:'02:00:00:00:00:01'}];
const groups=[];
const lastConnectedDevices={devices:[]};
const deviceTitle=d=>d.name;
const formDrafts=new Map();
let helperConfigured=true,accessRulesSaved=false,dashboardLoaded=true,adminTokenVerified=true;
let adminTokenRevision=0;
let preparedLoaded=false,preparedBusy=false;
const document={hidden:false};
const currentPage=()=> 'access';
let displayedSites=[],deviceText=[];
function renderDeviceCards() {deviceText=devices.map(deviceAccessText)}
function renderApplySummary() {}
function renderGettingStarted() {}
function populateWorkflowMenus() {}
async function loadPrivateDataPanel(id, job) {return job()}
async function show() {}
const names=Array.from({length:2000},(_,i)=>'ads'+String(i).padStart(5,'0')+'.tracker.example.test');
let editor={version:1,mac:{mode:'denylist',allowlist:[],denylist:[]},
  websites:{mode:'unrestricted',allowed_domains:['allowed.example.test'],blocked_domains:names},
  options:{enabled:true,reject_wifi_joins:false,dns_failure_percent:25},
  site_blocks:[{id:'video',name:'Video',domains:['video.example.test'],enabled:true}]};
let status={available:true,wifi_admission_available:true,applied:true,version:1,enabled:true,
  normal_access:false,saved_policy:{options:editor.options},
  policy:{options:editor.options,mac:editor.mac,
    websites:{mode:'unrestricted',allowed_domain_count:1,blocked_domain_count:2000},
    clients:[{id:'alice',block_ids:['video']}],
    blocks:[{id:'video',name:'Video',enabled:true,domain_count:1}]}};
let requests=[],readHook=null,readError=null;
async function request(path, method) {
  requests.push([path,method]);
  if (path==='/api/policies') return structuredClone(editor);
  assert.equal(path,'/api/access?summary=1');
  if (readHook) return readHook();
  if (readError) throw readError;
  return structuredClone(status);
}
"""
    stubs = "\n".join("async function %s() {}" % name for name in LOADERS
                      if name not in ("loadPolicies", "loadAccessStatus"))
    source = (
        harness + stubs
        + block("let policyVersion =", "function renderSiteRules(")
        + block("function renderAccessDevices(", "function installAccessControls(")
        + block("async function loadPolicies(", "async function loadState(")
        + block("function pagePollJobs(", "async function pollDashboard(")
        + block("const READ_AFTER_WRITE = [", "function readOldAdminTools(")
        + "function populateAccessTargets() {}\n"
        + "function renderSiteRules() {displayedSites=structuredClone(siteRules)}\n"
        + "(async () => {\n" + body
        + "\nconsole.log('CASE-COMPLETE');\n})().catch(e=>{console.error(e);process.exitCode=1});"
    )
    result = subprocess.run([NODE, "-"], input=source, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("CASE-COMPLETE"), result.stdout


def test_initial_refresh_and_polls_keep_complete_editor_lists_and_device_status():
    run(r"""
await refreshDashboard(null);
assert.equal(element('website-blocklist').value.split(', ').length,2000);
assert.equal(element('website-blocklist').value,names.join(', '));
assert.equal(element('website-allowlist').value,'allowed.example.test');
assert.equal(displayedSites[0].domains[0],'video.example.test');
assert.match(deviceText[0],/Global website rules, Video/);
assert.match(element('dns-failure-status').textContent,/25%/);
requests=[];
for (const page of ['access','devices']) await Promise.all(pagePollJobs(page).map(job=>job()));
assert.deepEqual(requests,[['/api/access?summary=1','GET'],['/api/access?summary=1','GET']]);
assert.equal(element('website-blocklist').value,names.join(', '));
assert.equal(displayedSites[0].domains[0],'video.example.test');
assert.equal(policyVersion,1);
assert.equal(siteVersion,1);
""")


def test_refreshes_after_saves_and_imports_read_full_names_without_overwriting_dirty_forms():
    run(r"""
await refreshDashboard(null);
formDrafts.set('policy-form',{dirty:true});
formDrafts.set('site-rule-form',{dirty:true});
element('website-blocklist').value='unfinished.example.test';
element('website-allowlist').value='unfinished-allow.example.test';
element('dns-failure-percent').value=75;
element('site-rule-domains').value='unfinished-site.example.test';
editor.version=2; editor.websites.blocked_domains=['changed.example.test'];
status.version=2; status.applied=false;
for (const path of ['/api/policies','/api/import','/api/history/restore']) {
  const scope=readsAfterWrites([path]);
  assert.equal(scope,null);
  await refreshDashboard(scope);
  assert.equal(element('website-blocklist').value,'unfinished.example.test');
  assert.equal(element('website-allowlist').value,'unfinished-allow.example.test');
  assert.equal(element('dns-failure-percent').value,75);
  assert.equal(element('site-rule-domains').value,'unfinished-site.example.test');
  assert.equal(policyVersion,1); assert.equal(siteVersion,1);
  assert.equal(accessState.version,2);
  assert.match(element('access-summary').textContent,/Saved rules differ/);
}
formDrafts.clear();
await refreshDashboard(readsAfterWrites(['/api/policies']));
assert.equal(element('website-blocklist').value,'changed.example.test');
assert.equal(policyVersion,2); assert.equal(siteVersion,2);
editor.version=3; editor.websites.blocked_domains=[...names.slice(0,-1),'imported.example.test'];
status.version=3;
await refreshDashboard(readsAfterWrites(['/api/import']));
assert.equal(element('website-blocklist').value.split(', ').length,2000);
assert.ok(element('website-blocklist').value.endsWith('imported.example.test'));
assert.equal(policyVersion,3);
""")


def test_summary_status_handles_empty_blocks_mac_rules_disabled_and_errors():
    run(r"""
await loadAccessStatus();
assert.match(deviceAccessText(devices[0]),/Global website rules, Video/);
status.policy.websites.blocked_domain_count=0; status.policy.blocks=[];
await loadAccessStatus();
assert.equal(deviceAccessText(devices[0]),'Network access allowed');
status.policy.websites.mode='allow_only';
await loadAccessStatus(); assert.match(deviceAccessText(devices[0]),/Global website rules/);
status.policy.mac.denylist=[devices[0].mac_address];
await loadAccessStatus(); assert.match(deviceAccessText(devices[0]),/Network access blocked by the MAC block list/);
status.policy.options.enabled=false; status.enabled=false; status.normal_access=true;
await loadAccessStatus();
assert.equal(deviceAccessText(devices[0]),'Household access rules disabled');
assert.equal(element('normal-access-notice').hidden,false);
assert.equal(element('disable-access').disabled,true);
status.error='Synthetic DNS health failure'; status.applied=false;
await loadAccessStatus();
assert.equal(deviceAccessText(devices[0]),status.error);
assert.equal(element('access-summary').dataset.state,'error');
readError=new Error('Synthetic connection failure');
await assert.rejects(loadAccessStatus(),/Synthetic connection failure/);
assert.equal(element('apply-access').disabled,true);
assert.equal(element('disable-access').disabled,true);
assert.equal(element('normal-access-notice').hidden,true);
assert.match(element('access-summary').textContent,/Access status is out of date/);
readError=null; status.error=null; status.policy=null;
await loadAccessStatus();
assert.equal(deviceAccessText(devices[0]),'Household access rules have not been applied');
""")


def test_a_save_during_a_summary_read_still_gets_a_fresh_status_read():
    run(r"""
let release;
readHook=()=>new Promise(resolve=>release=resolve);
const first=loadAccessStatus();
assert.equal(requests.length,1);
const afterSave=loadAccessStatus();
assert.equal(requests.length,1);
const old=structuredClone(status);
status.version=2; status.applied=false;
readHook=null;
release(old);
await Promise.all([first,afterSave]);
assert.equal(requests.length,2);
assert.equal(accessState.version,2);
assert.match(element('access-summary').textContent,/Saved rules differ/);
""")

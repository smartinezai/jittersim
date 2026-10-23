"""Exercise the update controls' real asynchronous consumers and private state."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")
WORKFLOWS = Path(__file__).parents[2] / "src/jittersim/api/workflows.js"

HARNESS = r"""
const assert=require('node:assert/strict');
const nodes=new Map();
function element(id) {
  if (!nodes.has(id)) nodes.set(id, {id,hidden:false,disabled:false,checked:false,textContent:'',
    attributes:{},listeners:{},classList:{toggle(){}},
    setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];},
    addEventListener(k,v){this.listeners[k]=v;}});
  return nodes.get(id);
}
let adminTokenRevision=0,adminTokenVerified=true,adminTokenChecking=false;
const privateDataLockHooks=[];
const document={hidden:false};
function currentPage(){return 'updates';}
function showMessage(){}
async function confirmWorkflow(){return true;}
const calls=[];
let network=async(path,method)=> path==='/api/updates'
 ? {installed:'1.0.0',available:null,phase:'idle',message:'idle'}
 : {state:'done',job:'job',result:'staged',token_set:true,settings:{daily_check:false,install_automatically:false}};
async function request(path,method,body){calls.push({path,method,body});return await network(path,method,body);}
function deferred(){let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});return {promise,resolve,reject};}
"""


def run(body):
    source=WORKFLOWS.read_text()
    source=source[source.index("let releaseState = null;"):source.index("function installReleaseControls(")]
    program=HARNESS+source+"\n(async()=>{\n"+body+"\nconsole.log('CASE-COMPLETE');\n})().catch(e=>{console.error(e);process.exit(1);});"
    result=subprocess.run([NODE,"-"],input=program,text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    lines=result.stdout.splitlines()
    assert lines[-1]=="CASE-COMPLETE",result.stdout
    return json.loads(lines[-2])


def test_controls_do_not_read_private_state_during_construction():
    actual=run("adminTokenVerified=false; installOnlineControls(); await Promise.resolve(); console.log(JSON.stringify(calls));")
    assert actual==[]


def test_old_online_rejection_does_not_repaint_new_signin():
    actual=run("const held=deferred(); network=()=>held.promise; const old=loadOnlineStatus(); await Promise.resolve(); "
               "adminTokenRevision++; element('online-check-status').textContent='new session'; "
               "held.reject(new Error('old failure')); await old; "
               "console.log(JSON.stringify({text:element('online-check-status').textContent}));")
    assert actual["text"]=="new session"


def test_manual_check_retries_unknown_online_status_before_choosing_transport():
    actual=run("let failed=false; network=async(path,method)=>{ "
               "if(path==='/api/updates/check-online' && method==='GET' && !failed){failed=true;throw new Error('temporary');} "
               "return path==='/api/updates'?{installed:'1.0.0',phase:'idle'}: "
               "{state:'done',result:'staged',job:'job',token_set:true,settings:{daily_check:false,install_automatically:false}};}; "
               "await loadOnlineStatus(); await checkForUpdates(); console.log(JSON.stringify(calls));")
    assert sum(c["path"]=="/api/updates/check-online" and c["method"]=="POST" for c in actual)==1
    assert sum(c["path"]=="/api/updates/check-online" and c["method"]=="GET" for c in actual)==2


def test_updates_poll_reads_external_staging_without_foreground_watch():
    actual=run("await pollReleaseProgress(); console.log(JSON.stringify(calls));")
    assert any(c["path"]=="/api/updates" for c in actual)
    assert any(c["path"]=="/api/updates/check-online" and c["method"]=="GET" for c in actual)
    assert all(c["method"]=="GET" for c in actual)


def test_complete_phase_requires_the_expected_installed_version():
    actual=run("updateWatch={label:'2.0.0',kind:'install'}; network=async()=>"
               "({installed:'1.0.0',phase:'complete',message:'another operation completed'}); "
               "await followUpdate(); console.log(JSON.stringify({outcome:updateOutcome}));")
    assert actual["outcome"]!="Updated to 2.0.0."


@pytest.mark.parametrize("category", ["online", "preference"])
def test_pre_write_read_cannot_replace_a_confirmed_save(category):
    setup = """
const old=deferred();
network=async(path,method)=> {
  if(method==='GET') return await old.promise;
  return path==='/api/update-preferences'
    ? {saved:true,check_staged_updates_on_open:false}
    : {token_set:true,settings:{daily_check:true,install_automatically:false}};
};
"""
    if category == "online":
        body = """
showOnline({state:'none',token_set:true,settings:{daily_check:false,install_automatically:false}});
installOnlineControls(); const pending=loadOnlineStatus(); await Promise.resolve();
const box=element('online-daily-check'); box.checked=true;
await box.listeners.change({target:box});
old.resolve({state:'none',token_set:true,settings:{daily_check:false,install_automatically:false}});
await pending; console.log(JSON.stringify({checked:box.checked}));
"""
        expected = True
    else:
        body = """
autoCheckStagedUpdates=true; installUpdateCheckPreference();
const pending=loadUpdateCheckPreference(); await Promise.resolve();
const box=element('check-staged-updates-on-open'); box.checked=false;
await box.listeners.change();
old.resolve({check_staged_updates_on_open:true}); await pending;
console.log(JSON.stringify({checked:box.checked}));
"""
        expected = False
    assert run(setup+body)["checked"] is expected


@pytest.mark.parametrize("category", ["online", "preference"])
def test_uncertain_save_recovery_reads_after_the_write(category):
    setup = """
const old=deferred(); let gets=0;
network=async(path,method)=> {
  if(method==='POST') throw new Error('lost write reply');
  if(++gets===1) return await old.promise;
  return path==='/api/update-preferences' ? {check_staged_updates_on_open:true}
    : {state:'none',token_set:true,settings:{daily_check:true,install_automatically:false}};
};
"""
    if category == "online":
        body = """
showOnline({state:'none',token_set:true,settings:{daily_check:false,install_automatically:false}});
installOnlineControls(); const pending=loadOnlineStatus(); await Promise.resolve();
const box=element('online-daily-check'); box.checked=true;
const saving=box.listeners.change({target:box}); await Promise.resolve(); await Promise.resolve();
old.resolve({state:'none',token_set:true,settings:{daily_check:false,install_automatically:false}});
await Promise.all([pending,saving]); console.log(JSON.stringify({gets,checked:box.checked}));
"""
    else:
        body = """
autoCheckStagedUpdates=false; installUpdateCheckPreference();
const pending=loadUpdateCheckPreference(); await Promise.resolve();
const box=element('check-staged-updates-on-open'); box.checked=true;
const saving=box.listeners.change(); await Promise.resolve(); await Promise.resolve();
old.resolve({check_staged_updates_on_open:false}); await Promise.all([pending,saving]);
console.log(JSON.stringify({gets,checked:box.checked}));
"""
    actual=run(setup+body)
    assert actual=={"gets":2,"checked":True}


def test_delayed_check_reply_cannot_replace_newly_saved_settings():
    actual=run("""
showOnline({state:'none',token_set:true,settings:{daily_check:false,install_automatically:false}});
installOnlineControls(); const held=deferred();
network=async(path,method)=> {
  if(path==='/api/updates/check-online' && method==='POST') return await held.promise;
  if(path==='/api/updates/online-settings') return {token_set:true,settings:{daily_check:true,install_automatically:false}};
  return {installed:'1.0.0',phase:'idle'};
};
const checking=runOnlineCheck(); await Promise.resolve();
const box=element('online-daily-check'); box.checked=true;
await box.listeners.change({target:box});
held.resolve({state:'done',result:'staged',token_set:true,settings:{daily_check:false,install_automatically:false}});
await checking; console.log(JSON.stringify({checked:box.checked}));
""")
    assert actual["checked"] is True


def test_failed_status_read_retains_last_confirmed_signed_version():
    actual=run("""
knownHealthVersion='0.2.0';
network=async()=>({installed:'2.0.0',phase:'idle'});
await loadReleaseStatus({force:true});
network=async()=>{throw new Error('temporary status failure');};
try {await loadReleaseStatus({force:true});} catch(e) {}
console.log(JSON.stringify({installed:element('update-installed').textContent,available:element('update-available').textContent}));
""")
    assert actual["installed"] == "2.0.0"
    assert "Unknown" in actual["available"]


@pytest.mark.parametrize("category", ["online", "preference"])
def test_pending_setting_save_keeps_controls_disabled(category):
    setup = """
const held=deferred(); network=()=>held.promise;
"""
    if category == "online":
        body = """
showOnline({state:'none',token_set:true,settings:{daily_check:false,install_automatically:false}});
installOnlineControls(); const box=element('online-daily-check'); box.checked=true;
const saving=box.listeners.change({target:box}); await Promise.resolve();
assert.equal(box.disabled,true);
assert.equal(element('online-install-automatically').disabled,true);
held.resolve({token_set:true,settings:{daily_check:true,install_automatically:false}});
await saving; console.log(JSON.stringify({disabled:box.disabled,checked:box.checked,posts:calls.length}));
"""
    else:
        body = """
autoCheckStagedUpdates=true; installUpdateCheckPreference();
const box=element('check-staged-updates-on-open'); box.checked=false;
const saving=box.listeners.change(); await Promise.resolve(); assert.equal(box.disabled,true);
held.resolve({saved:true,check_staged_updates_on_open:false}); await saving;
console.log(JSON.stringify({disabled:box.disabled,checked:box.checked,posts:calls.length}));
"""
    assert run(setup+body)=={"disabled":False,"checked":category=="online","posts":1}


def test_pending_automatic_install_confirmation_serializes_both_settings():
    actual=run("""
showOnline({state:'none',token_set:true,settings:{daily_check:false,install_automatically:false}});
installOnlineControls(); const held=deferred(); confirmWorkflow=()=>held.promise;
const automatic=element('online-install-automatically'); automatic.checked=true;
const saving=automatic.listeners.change({target:automatic}); await Promise.resolve();
assert.equal(automatic.disabled,true); assert.equal(element('online-daily-check').disabled,true);
const daily=element('online-daily-check'); daily.checked=true; await daily.listeners.change({target:daily});
assert.equal(calls.length,0); held.resolve(false); await saving;
console.log(JSON.stringify({disabled:automatic.disabled,checked:automatic.checked,posts:calls.length}));
""")
    assert actual=={"disabled":False,"checked":False,"posts":0}


def test_device_access_loss_discards_held_update_state_without_token_change():
    actual=run("""
const held=deferred(); network=()=>held.promise;
const loading=loadOnlineStatus(); await Promise.resolve();
privateDataLockHooks.forEach(hook=>hook());
held.resolve({state:'done',token_set:true,settings:{daily_check:true,install_automatically:true}});
await loading;
console.log(JSON.stringify({text:element('online-check-status').textContent,disabled:element('online-daily-check').disabled,view:onlineView}));
""")
    assert actual=={"text":"Sign in to read update status.","disabled":True,"view":None}


def test_pre_restore_status_cannot_become_the_restore_result():
    actual=run("""
releaseState={installed:'2.0.0',phase:'idle',rollback_available:true};
const held=deferred(); let gets=0;
network=async(path,method)=> {
  if(method==='POST') return {phase:'queued'};
  if(++gets===1) return await held.promise;
  return {installed:'1.0.0',phase:'complete',message:'current status without an attempt identity'};
};
const old=loadReleaseStatus({force:true}); await Promise.resolve();
await restoreUpdate();
held.resolve({installed:'2.0.0',phase:'complete',message:'old unrelated completion'});
await old;
for(let i=0;i<20&&updateFollowing;i++) await new Promise(resolve=>setImmediate(resolve));
assert.equal(updateFollowing,false);
console.log(JSON.stringify({gets,installed:element('update-installed').textContent,outcome:updateOutcome}));
""")
    assert actual["gets"]==2
    assert actual["installed"]=="1.0.0"
    assert "not confirmed" in actual["outcome"]


def test_signing_in_again_leaves_no_sign_in_text_on_the_save_status_line():
    # That line answers a save. Locking set it to the sign-in text and nothing cleared it on the next sign-in.
    actual=run("""
element('online-setting-status').textContent='Saved.';
privateDataLockHooks.forEach(hook=>hook());
console.log(JSON.stringify({save:element('online-setting-status').textContent,check:element('online-check-status').textContent}));
""")
    assert actual=={"save":"","check":"Sign in to read update status."}

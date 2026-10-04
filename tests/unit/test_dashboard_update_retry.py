"""Identified retry follows one retained operation without repeating a write."""
import pytest
from tests.unit.test_dashboard_online_recovery import run
from tests.unit.test_dashboard_updates_flow import run as run_action

IDENTITY = "a" * 32
OTHER = "b" * 32
ADMISSION = "c" * 64
SETUP = f"""
const identity='{IDENTITY}', other='{OTHER}', admission='{ADMISSION}';
function status(phase='failed') {{return {{installed:'1.0.0',phase,request_id:identity,request_kind:'install',
 retry:{{id:admission,request_id:identity,kind:'install',version:'2.0.0',recovering:false}},message:'Retained operation'}};}}
releaseState=status();
"""


def test_identified_completion_cannot_borrow_same_version_from_another_request():
    actual=run(SETUP+"""
updateWatch={label:'2.0.0',kind:'install',requestId:identity,requestKind:'install',context:updateContext()};
network=async()=>({...status('complete'),request_id:other,installed:'2.0.0'});
await followUpdate();console.log(JSON.stringify({outcome:updateOutcome}));
""")
    assert "not confirmed" in actual["outcome"]


def test_identified_restore_can_confirm_its_own_completion():
    actual=run(SETUP+"""
updateWatch={kind:'restore',requestId:identity,requestKind:'rollback',context:updateContext()};
network=async()=>({...status('rolled-back'),request_kind:'rollback'});
await followUpdate();console.log(JSON.stringify({outcome:updateOutcome}));
""")
    assert actual["outcome"]=="Previous installation restored. Installed version: 1.0.0."


@pytest.mark.parametrize("replacement", ["{...status(),request_id:other}", "{...status(),retry:{...status().retry,id:'d'.repeat(64)}}", "{...status(),request_kind:'rollback'}"])
def test_changed_request_during_confirmation_never_sends_retry(replacement):
    actual=run(SETUP+f"""
const held=deferred();confirmWorkflow=()=>held.promise;
network=async()=>({replacement});
const retrying=retryUpdate();await Promise.resolve();held.resolve(true);await retrying;
console.log(JSON.stringify(calls));
""")
    assert not any(c["method"]=="POST" for c in actual)


def test_double_click_joins_one_confirmation_and_one_retry():
    actual=run(SETUP+"""
const held=deferred();let confirms=0;confirmWorkflow=()=>{confirms++;return held.promise;};
network=async(path,method)=>method==='POST'?{status:status('queued')}:{...status('complete'),installed:'2.0.0'};
const first=retryUpdate();const second=retryUpdate();await Promise.resolve();held.resolve(true);
await Promise.all([first,second]);console.log(JSON.stringify({confirms,calls}));
""")
    assert actual["confirms"]==1
    assert sum(c["method"]=="POST" for c in actual["calls"])==1
    assert next(c["body"] for c in actual["calls"] if c["method"]=="POST")=={"retry_id":ADMISSION}


def test_lost_retry_reply_reads_current_status_without_replay():
    actual=run(SETUP+"""
let reads=0;network=async(path,method)=>{if(method==='POST')throw new Error('lost reply');
 if(++reads===1)return status();return {...status('complete'),installed:'2.0.0'};};
await retryUpdate();console.log(JSON.stringify({calls,outcome:updateOutcome}));
""")
    assert sum(c["method"]=="POST" for c in actual["calls"])==1
    assert actual["outcome"]=="Updated to 2.0.0."


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("revoke", [False, True])
def test_late_retry_reply_or_failure_cannot_repaint_changed_access(failure,revoke):
    change="privateDataLockHooks.forEach(hook=>hook());" if revoke else "adminTokenRevision++;clearUpdatePrivateState();"
    release="held.reject(new Error('old failure'));" if failure else "held.resolve({status:status('queued')});"
    actual=run(SETUP+f"""
const held=deferred();network=async(path,method)=>method==='POST'?held.promise:status();
const pending=retryUpdate();for(let i=0;i<10&&!calls.some(c=>c.method==='POST');i++)await Promise.resolve();
assert.equal(calls.filter(c=>c.method==='POST').length,1);{change}
element('release-status').textContent='new private session';{release}await pending;
console.log(JSON.stringify({{text:element('release-status').textContent,watch:updateWatch,disabled:element('release-retry').disabled,calls}}));
""")
    assert actual["text"]=="new private session"
    assert actual["watch"] is None
    assert actual["disabled"] is True
    assert sum(c["method"]=="GET" for c in actual["calls"])==1


@pytest.mark.parametrize("retry", ["null", "{id:admission,request_id:other,kind:'install'}", "{id:'A'.repeat(64),request_id:identity,kind:'install'}"])
def test_legacy_or_malformed_retry_is_not_offered(retry):
    actual=run(SETUP+f"releaseState.retry={retry};renderUpdates();await retryUpdate();console.log(JSON.stringify({{calls,hidden:element('release-retry').hidden}}));")
    assert actual=={"calls":[],"hidden":True}


@pytest.mark.parametrize("kind", ["install", "rollback"])
def test_queue_identity_is_retained_by_install_and_restore(kind):
    function = "installUpdate" if kind == "install" else "restoreUpdate"
    fields = "available:'2.0.0',installable:true" if kind == "install" else "rollback_available:true"
    actual = run(SETUP+f"""
releaseState={{installed:'1.0.0',phase:'idle',{fields}}};
followUpdate=async()=>{{}};
network=async()=>({{request_id:identity,request_kind:'{kind}',phase:'queued'}});
await {function}(); console.log(JSON.stringify({{watch:updateWatch}}));
""")
    assert actual["watch"]["requestId"] == IDENTITY
    assert actual["watch"]["requestKind"] == kind


def test_wrong_operation_kind_in_queue_reply_cannot_confirm_install():
    actual = run(SETUP+"""
releaseState={installed:'1.0.0',phase:'idle',available:'2.0.0',installable:true};
let watching=null; followUpdate=async()=>{watching=updateWatch;};
network=async()=>({request_id:identity,request_kind:'rollback',phase:'queued'});
await installUpdate();console.log(JSON.stringify({watch:watching}));
""")
    assert actual["watch"]["requestKind"] == "install"


@pytest.mark.parametrize("failure", [False, True])
def test_release_button_old_finally_cannot_unlock_or_show_old_error(failure):
    settled="reject(new Error('old private failure'));" if failure else "resolve({phase:'queued'});"
    run_action(f"""
pi.staged={{version:'2.0.0',tag:'v2.0.0'}};await loadReleaseStatus({{force:true}});renderUpdates();
let resolve,reject;const held=new Promise((r,j)=>{{resolve=r;reject=j;}});
request=async(path,method)=>{{calls.push([method,path]);return held;}};
const pending=element('release-install').click();await Promise.resolve();await Promise.resolve();
adminTokenRevision++;clearUpdatePrivateState();element('release-status').textContent='new session';
{settled}await pending;
assert.equal(element('release-install').disabled,true);
assert.equal(element('release-status').textContent,'new session');
assert.equal(messages.length,0);
""")

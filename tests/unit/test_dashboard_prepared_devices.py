"""Prepared choices stay inert and private; Use has one strict reviewed payload."""

import json
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML
from tests.dashboard_script import NODE, function_source, run_node

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js required")
MAC = "02:00:00:00:00:01"


def review(**changes):
    result = {"pending_device": {"mac_address": MAC, "action": "profile", "profile_id": "slow", "revision": 4},
              "profile": {"id": "slow"}, "configuration_version": 7}
    result.update(changes)
    return result


def payload_result(value):
    return run_node([function_source("preparedUsePayload")],
                    "(() => {try {return preparedUsePayload(" + json.dumps(value) + ");} "
                    "catch (error) {return {error: error.message};}})()")


def test_use_payload_contains_only_the_three_server_owned_review_values():
    assert payload_result(review()) == {"pending_mac": MAC, "expected_pending_revision": 4,
                                        "expected_configuration_version": 7}


@pytest.mark.parametrize("value", [None, {}, review(profile=None), review(profile={"id": "different"}),
    review(configuration_version=True), review(configuration_version=-1),
    review(configuration_version=9007199254740992),
    review(pending_device={"action": "protect"}),
    review(pending_device={"mac_address": MAC, "action": "profile", "profile_id": "slow", "revision": True}),
    review(pending_device={"mac_address": MAC, "action": "profile", "profile_id": "slow", "revision": 9007199254740992}),
    review(pending_device={"mac_address": "bad", "action": "profile", "profile_id": "slow", "revision": 1})])
def test_incomplete_mixed_or_inexact_reviews_fail_closed(value):
    assert "error" in payload_result(value)


@pytest.mark.parametrize("change", ["adminTokenRevision++", "preparedEpoch++", "actionIntentRevision++",
                                     "adminTokenVerified=false", "page='profiles'"])
def test_late_results_do_not_belong_to_a_changed_private_or_action_context(change):
    sources = ["let adminTokenRevision=1, preparedEpoch=1, actionIntentRevision=1, adminTokenVerified=true;",
               "let page='devices'; const currentPage=()=>page;", function_source("preparedOwner"),
               function_source("preparedCurrent")]
    assert run_node(sources, "(() => {const owner=preparedOwner(); " + change + "; return preparedCurrent(owner);})()") is False


def test_invalidation_erases_review_and_cancels_its_confirmation():
    sources = ["let preparedReviewSerial=0, preparedReview={secret:'private'};",
               "const nodes={}; const element=id => nodes[id] || (nodes[id]={textContent:'private'});",
               "let closed=null; let preparedDialog={open:true, close:value=>closed=value};",
               function_source("invalidatePreparedReview")]
    result = run_node(sources, "(() => {invalidatePreparedReview(); return [preparedReviewSerial, preparedReview, closed, "
                      "element('prepared-review').hidden, element('prepared-settings').textContent, "
                      "element('prepared-review-summary').textContent];})()")
    assert result == [1, None, "cancel", True, "", ""]


def run_async(sources, expression):
    program = "\n".join(sources) + "\n(async()=>{ console.log(JSON.stringify(await (" + expression + "))); })().catch(e=>{console.error(e);process.exit(1)});"
    result = subprocess.run([NODE, "-"], input=program, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_save_is_inert_cas_and_never_calls_apply():
    sources = ["let preparedBusy=false, adminTokenVerified=true, adminTokenRevision=1, preparedEpoch=1, preparedEdit={mac_address:'" + MAC + "',revision:8};",
               "const nodes={'prepared-mac':{value:'forged'},'prepared-name':{value:'Name'},'prepared-profile':{value:'slow'},'prepared-save':{}};",
               "const element=id=>nodes[id]; const preparedOwner=()=>({token:1,epoch:1}); const preparedCurrent=()=>true;",
               "const invalidatePreparedReview=()=>{},setButtonBusy=()=>{},setPreparedFormBusy=()=>{},resetPreparedForm=()=>{},preparedMessage=()=>{}; const beginPreparedOperation=()=>({}),finishPreparedOperation=()=>true;",
               "let calls=[]; const request=async(...args)=>calls.push(args); const loadPreparedChoices=async()=>{};",
               "async " + function_source("savePreparedChoice")]
    calls = run_async(sources, "savePreparedChoice({preventDefault(){}}).then(()=>calls)")
    assert calls[0][:3] == ["/api/pending-devices", "POST", {"mac_address": MAC, "action": "profile",
        "profile_id": "slow", "display_name": "Name", "expected_revision": 8}]
    assert len(calls) == 1


def test_use_posts_exact_review_after_explicit_confirmation_and_does_not_retry_failure():
    sources = ["let preparedBusy=false,networkActionBusy=false,helperConfigured=true,adminTokenVerified=true,adminTokenRevision=1,preparedEpoch=1,actionIntentRevision=0,preparedReviewSerial=1;",
               "let preparedReview=" + json.dumps(review()) + "; preparedReview.profile.name='Slow';",
               "const element=()=>({}); const preparedOwner=()=>({token:1,epoch:1}); const preparedCurrent=()=>true,currentPage=()=>'devices';",
               "let confirms=0,calls=[]; const preparedConfirm=async()=>{confirms++;return true;};",
               "const invalidatePreparedReview=()=>{preparedReview=null;preparedReviewSerial++;};",
               "const setActionBusy=()=>{},setButtonBusy=()=>{},preparedMessage=()=>{},refreshAfterSuccess=()=>{}; const beginPreparedOperation=()=>({}),finishPreparedOperation=()=>true;",
               "const request=async(...args)=>{calls.push(args);const e=new Error('changed');e.status=409;throw e;};",
               function_source("preparedUsePayload"), "async " + function_source("usePreparedChoice")]
    result = run_async(sources, "usePreparedChoice().then(()=>({confirms,calls,review:preparedReview}))")
    assert result["confirms"] == 1 and result["review"] is None
    assert len(result["calls"]) == 1
    assert result["calls"][0][:3] == ["/api/connected-devices/apply-profile", "POST", {
        "pending_mac": MAC, "expected_pending_revision": 4, "expected_configuration_version": 7}]


def test_card_adds_no_announcer_or_automatic_discovery():
    card = DASHBOARD_HTML.split('id="prepared-card"', 1)[1].split('<h2>Saved devices</h2>', 1)[0]
    assert 'aria-live' not in card and 'role="status"' not in card and 'role="alert"' not in card
    assert 'prepared-profile' in card and 'prepared-use' in card
    source = function_source("loadPreparedChoices")
    assert '/api/pending-devices' in source and '256' in source
    assert 'apply-profile' not in source and 'connected-devices' not in source
    assert 'slice(start, start + 20)' in function_source("renderPreparedRows")


def test_delayed_list_cannot_repopulate_after_lock():
    sources = ["let preparedBusy=false,adminTokenVerified=true,preparedListSerial=0,preparedRows=[],preparedLoaded=false;",
               "let current=true,rendered=0; const preparedOwner=()=>({}); const preparedCurrent=()=>current;",
               "const currentPage=()=>'devices',invalidatePreparedReview=()=>{},element=()=>({});",
               "const renderPreparedRows=()=>rendered++; const preparedMessage=()=>{throw Error('late error');};",
               "let release; const request=()=>new Promise(resolve=>release=resolve);",
               "async " + function_source("loadPreparedChoices")]
    result = run_async(sources, "(async()=>{const pending=loadPreparedChoices();current=false;preparedListSerial++;"
                       "release({pending_devices:[{secret:'private'}]});await pending;return {preparedRows,rendered,preparedLoaded};})()")
    assert result == {"preparedRows": [], "rendered": 0, "preparedLoaded": False}


def test_private_clear_discards_form_rows_review_and_private_profile_options():
    sources = ["let preparedOperation=null,preparedEpoch=0,preparedListSerial=0,preparedRows=[{secret:'private'}],preparedPage=9,preparedLoaded=true,preparedBusy=true;",
               "let reviewCleared=false,formCleared=false,unbusy=false;",
               "const invalidatePreparedReview=()=>reviewCleared=true,resetPreparedForm=()=>formCleared=true,setPreparedFormBusy=()=>unbusy=true;",
               "const nodes={}; const element=id=>nodes[id]||(nodes[id]={replaceChildren(...values){this.values=values;}});",
               "function Option(text,value){this.text=text;this.value=value;}", function_source("clearPreparedPrivate")]
    result = run_node(sources, "(()=>{clearPreparedPrivate();return {preparedRows,preparedPage,preparedLoaded,preparedBusy,"
                      "reviewCleared,formCleared,unbusy,options:element('prepared-profile').values};})()")
    assert result == {"preparedRows": [], "preparedPage": 0, "preparedLoaded": False, "preparedBusy": False,
                      "reviewCleared": True, "formCleared": True, "unbusy": True,
                      "options": [{"text": "Choose a saved profile", "value": ""}]}


def test_same_token_private_clear_removes_only_prepared_draft_before_restore():
    sources = ["let adminTokenRevision=1,preparedOperation=null,preparedEpoch=0,preparedListSerial=0,preparedRows=[],preparedPage=0,preparedLoaded=true,preparedBusy=false;",
               "const formDrafts=new Map([['prepared-form',{fields:[{key:'prepared-mac',value:'private'}]}],['other',{fields:[]}]]);",
               "const fields=[{id:'prepared-mac',value:'private',tagName:'INPUT'}];",
               "const nodes={}; const element=id=>nodes[id]||(nodes[id]={replaceChildren(){}});",
               "const draftFields=()=>fields,renderDraftNotice=()=>{},setPreparedFormBusy=()=>{},invalidatePreparedReview=()=>{};",
               "const resetPreparedForm=()=>fields.forEach(field=>field.value=''); function Option(){};",
               function_source("clearDraft"),function_source("clearPreparedPrivate"),function_source("restoreDrafts"),
               function_source("rememberDraft")]
    result = run_node(sources, "(()=>{clearPreparedPrivate();restoreDrafts();rememberDraft({id:'prepared-form'});"
                      "return {value:fields[0].value,keys:[...formDrafts.keys()],token:adminTokenRevision};})()")
    assert result == {"value": "", "keys": ["other"], "token": 1}


def test_panic_during_deferred_delete_releases_own_busy_without_refreshing_or_repeating():
    sources = ["let preparedBusy=false,preparedOperation=null,actionIntentRevision=0; const owner={};",
               "let calls=0,messages=0,refreshes=0,release; let button={disabled:true};",
               "const preparedOwner=()=>owner,preparedCurrent=()=>actionIntentRevision===0;",
               "const invalidatePreparedReview=()=>{},preparedConfirm=async()=>true;",
               "const setButtonBusy=(button,busy)=>button.disabled=busy,setPreparedFormBusy=()=>{};",
               "const preparedMessage=()=>messages++,loadPreparedAfterWrite=async()=>refreshes++;",
               "const request=()=>{calls++;return new Promise(resolve=>release=resolve);};",
               function_source("beginPreparedOperation"),function_source("finishPreparedOperation"),
               "async " + function_source("deletePreparedChoice")]
    result = run_async(sources, "(async()=>{const pending=deletePreparedChoice({mac_address:'" + MAC + "',revision:1},button);"
                       "await Promise.resolve();actionIntentRevision++;release({removed:true});await pending;"
                       "return {preparedBusy,disabled:button.disabled,calls,messages,refreshes};})()")
    assert result == {"preparedBusy": False, "disabled": False, "calls": 1, "messages": 0, "refreshes": 0}


def test_old_operation_cannot_release_a_newer_operations_busy_state():
    sources = ["let preparedOperation=null,preparedBusy=false;let released=0;",
               "const setButtonBusy=()=>released++,setPreparedFormBusy=()=>{};",
               function_source("beginPreparedOperation"),function_source("finishPreparedOperation")]
    result = run_node(sources, "(()=>{const old=beginPreparedOperation();const current=beginPreparedOperation();"
                      "return {oldReleased:finishPreparedOperation(old,{}),preparedBusy,released,same:preparedOperation===current};})()")
    assert result == {"oldReleased": False, "preparedBusy": True, "released": 0, "same": True}

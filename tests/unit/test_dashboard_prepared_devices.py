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
               "const invalidatePreparedReview=()=>{},setButtonBusy=()=>{},setPreparedFormBusy=()=>{},resetPreparedForm=()=>{},preparedMessage=()=>{};",
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
               "const setActionBusy=()=>{},setButtonBusy=()=>{},preparedMessage=()=>{},refreshAfterSuccess=()=>{};",
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

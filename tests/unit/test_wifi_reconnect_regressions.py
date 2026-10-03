"""Failure cases found while reviewing Wi-Fi restart confirmation."""

import threading
import time
from unittest.mock import Mock

from jittersim.api.wifi_operations import WifiOperations, WifiOutcome, issue_request_id
from tests.unit.test_dashboard_reconnect import run_node


def request_support_source():
    from tests.unit.test_dashboard_reconnect import SCRIPT
    start = SCRIPT.index('    const PI_NOT_ANSWERING =')
    end = SCRIPT.index('    function showPiNotAnswering(', start)
    return SCRIPT[start:end] + "\nconst recentWrites = new Set();\nfunction notePiNotAnswering() {}\nfunction notePiAnswered() {}\n"


def test_retry_budget_includes_time_inside_requests():
    result = run_node('''
    wifiSleep = sleep;
    let calls = 0;
    try {
      await retryAfterRejoin(async () => {
        calls += 1; clock += 20000;
        throw lostConnectionError(new Error());
      }, {now, deadline: 90000});
    } catch (error) {}
    console.log(JSON.stringify({calls, elapsed: clock}));
    ''')
    assert result['elapsed'] <= 110000
    assert result['calls'] <= 5


def test_probe_answer_after_deadline_is_not_success():
    result = run_node('''
    const back = await waitForPi({sleep, now, limitMs: 1500,
      probe: async () => {clock += 2000;}});
    console.log(JSON.stringify({back}));
    ''')
    assert result['back'] is False


def test_old_token_waiter_does_not_restart_sign_in():
    result = run_node('''
    let adminTokenRevision = 1, checks = 0, release;
    wifiSleep = sleep;
    probePi = () => new Promise(resolve => release = resolve);
    const checkAdminToken = () => {checks += 1;};
    recheckTokenWhenPiAnswers();
    await new Promise(resolve => setTimeout(resolve, 0)); adminTokenRevision += 1;
    release(); await new Promise(resolve => setTimeout(resolve, 0));
    console.log(JSON.stringify({checks}));
    ''')
    assert result['checks'] == 0


def test_close_cancels_restart_waiting_to_begin():
    work = Mock(return_value=WifiOutcome(200, 'done'))
    operations = WifiOperations(delay=.04)
    operations.start('reset-name', work)
    operations.close()
    time.sleep(.08)
    work.assert_not_called()
    assert not operations.busy()


def test_repeat_exact_request_never_replays_finished_write():
    release = threading.Event()
    operations = WifiOperations(delay=0)
    work = Mock(side_effect=lambda: (release.wait(1), WifiOutcome(200, 'done'))[1])
    request_id = issue_request_id()
    first = operations.start('reset-name', work, request_id=request_id)
    release.set()
    deadline = time.monotonic() + 1
    while operations.busy() and time.monotonic() < deadline:
        time.sleep(.005)
    again = operations.start('reset-name', work, request_id=request_id)
    assert again['id'] == first['id']
    assert again['state'] == 'succeeded'
    assert work.call_count == 1


def test_lost_write_only_reads_exact_id_and_refuses_another_outcome():
    result = run_node('''
    wifiSleep = sleep; newWifiRequestId = () => "exact";
    showWifiBanner = () => {}; hideWifiBanner = () => {};
    let writes = 0, urls = [], finished = 0;
    request = async (path, method) => {
      if (method === "POST") {writes++; throw lostConnectionError(new Error());}
      urls.push(path);
      return {operation: {id:"different-browser", state:"succeeded"}};
    };
    const outcome = await runWifiChange("/api/access-point", {ssid:"Intended"}, () => finished++);
    console.log(JSON.stringify({writes, urls, finished, gaveUp:outcome.gaveUp}));
    ''')
    assert result == {'writes': 1, 'urls': ['/api/wifi-operation?id=exact'], 'finished': 0, 'gaveUp': True}


def test_hung_probe_is_cancelled_at_the_elapsed_deadline():
    result = run_node('''
    let aborted = false;
    const back = await waitForPi({limitMs:20, sleep: async () => {}, now:Date.now,
      probe: ({signal}) => new Promise(() => signal.addEventListener("abort", () => aborted=true))});
    console.log(JSON.stringify({back, aborted}));
    ''')
    assert result == {'back': False, 'aborted': True}


def test_exact_outcome_count_is_bounded_and_expired_ids_cannot_replay():
    now = [time.time()]
    operations = WifiOperations(delay=0, clock=lambda: now[0], elapsed_clock=lambda: now[0], request_clock=lambda: now[0])
    first = None
    for index in range(operations.MAX_RESULTS):
        identifier = issue_request_id(now[0])
        operation = operations.reserve('reset-name', identifier)
        assert operation is not None
        first = first or identifier
        operations.finish(identifier, WifiOutcome(200, 'done'))
    extra = issue_request_id(now[0])
    assert operations.reserve('reset-name', extra) is None
    assert operations.last(first)[1]['operation']['state'] == 'succeeded'
    now[0] += operations.RESULT_LIFETIME + 1
    assert operations.last(first)[0] == 410
    assert operations.reserve('reset-name', first) is None
    fresh = issue_request_id(now[0])
    assert operations.reserve('reset-name', fresh) is not None


def test_router_worker_never_runs_when_wifi_reservation_is_refused():
    from types import SimpleNamespace
    from jittersim.api.server import ControlServer

    operations = WifiOperations(delay=0)
    operations.begin('access-point')
    work = Mock()
    server = SimpleNamespace(wifi_operations=operations, _router_response_for=work)
    result = ControlServer._router_execute(server, '/api/router/sync-ap', {})
    assert result[0] == 409
    work.assert_not_called()


def test_repeat_router_identifier_keeps_credential_isolation_and_does_not_write_twice():
    import json
    from jittersim.api.router_operations import RouterOperations

    work = Mock(return_value=(200, 'application/json', json.dumps({'result': {'enabled': True}})))
    operations = RouterOperations(work, 'test-router')
    identifier = issue_request_id()
    payload = {'admin_password': 'secret', 'enabled': True, 'request_id': identifier}
    status, data = operations.start('/api/router/wifi', payload)
    assert status == 202 and data['operation_id'] == identifier
    deadline = time.monotonic() + 1
    while operations.result(identifier, 'secret')[0] == 202 and time.monotonic() < deadline:
        time.sleep(.005)
    assert operations.start('/api/router/wifi', payload)[0] == 200
    assert work.call_count == 1
    assert operations.result(identifier, 'another-secret')[0] == 410
    assert operations.start('/api/router/wifi', {**payload, 'admin_password': 'another-secret'})[0] == 409
    assert 'secret' not in repr(operations._requests)


def test_acceptance_keeps_draft_and_late_write_reply_cannot_clear_it():
    from tests.unit.test_dashboard_reconnect import function_source
    result = run_node('''
    let adminTokenRevision=1, adminTokenVerified=true, adminTokenChecking=false;
    const draft={sequence:1}; const formDrafts=new Map([["ap",draft]]);
    const form={id:"ap",querySelector:()=>null};
    let pendingFormSubmitButton={form};
    const element=()=>({value:"token"}); const setButtonBusy=()=>{};
    let locks=0; const lockDashboardAccess=()=>locks++;
    const recordRead=()=>{}; const renderDraftNotice=()=>{};
    let fetch=async()=>({ok:true,status:202,json:async()=>({pending:true})});
    ''' + request_support_source() + function_source('request') + '''
    await request("/api/access-point","POST",{});
    const retained=formDrafts.get("ap")===draft;
    pendingFormSubmitButton={form};
    let finishBody;
    fetch=async()=>({ok:false,status:403,json:()=>new Promise(resolve=>finishBody=resolve)});
    const late=request("/api/access-point","POST",{}).then(()=>false,()=>true);
    await Promise.resolve();adminTokenRevision++;finishBody({error:"denied",registration_required:true});
    const staleRejected=await late;
    console.log(JSON.stringify({retained,staleRejected,stillRetained:formDrafts.get("ap")===draft,locks}));
    ''')
    assert result == {'retained':True,'staleRejected':True,'stillRetained':True,'locks':0}


def test_old_save_completion_keeps_newer_ap_name_password_and_draft():
    from tests.unit.test_dashboard_reconnect import SCRIPT
    start = SCRIPT.index('    element("access-point-form").addEventListener("submit"')
    end = SCRIPT.index('\n    });', start) + len('\n    });')
    result = run_node('''
    let listener,finish,release,wifiChangePending=false,pendingFormSubmitButton=null,adminTokenRevision=1;
    const fields={"access-point-form":{addEventListener:(kind,fn)=>listener=fn},
      "access-point-ssid":{value:"Original"},"access-point-password":{value:"old-secret"}};
    const element=id=>fields[id];const window={confirm:()=>true};
    const formDrafts=new Map([["access-point-form",{sequence:1}]]);
    const clearDraft=id=>formDrafts.delete(id);const showMessage=()=>{};
    runWifiChange=async(path,body,callback)=>{finish=callback;return new Promise(resolve=>release=resolve);};
    ''' + SCRIPT[start:end] + '''
    const pending=listener({preventDefault(){}});
    fields["access-point-ssid"].value="New name";fields["access-point-password"].value="new-secret";
    formDrafts.set("access-point-form",{sequence:2});
    finish({state:"succeeded",message:"old done"});release({operation:{state:"succeeded"}});await pending;
    console.log(JSON.stringify({name:fields["access-point-ssid"].value,
      password:fields["access-point-password"].value,retained:formDrafts.has("access-point-form")}));
    ''')
    assert result == {'name':'New name','password':'new-secret','retained':True}


def test_unexpired_pre_restart_identifier_is_refused_by_fresh_generation(monkeypatch):
    from jittersim.api import wifi_operations
    identifier = issue_request_id()
    assert wifi_operations.valid_request_id(identifier)
    monkeypatch.setattr(wifi_operations, '_REQUEST_KEY', b'fresh-process-generation' * 2)
    fresh = WifiOperations(delay=0)
    work = Mock(return_value=WifiOutcome(200, 'done'))
    assert fresh.start('reset-name', work, request_id=identifier) is None
    work.assert_not_called()
    assert fresh.reserve('reset-name', issue_request_id()) is not None


def test_thread_start_failure_records_failure_and_releases_slot(monkeypatch):
    operations = WifiOperations(delay=0)
    identifier = issue_request_id()
    work = Mock(return_value=WifiOutcome(200, 'done'))
    monkeypatch.setattr(threading.Thread, 'start', Mock(side_effect=RuntimeError('cannot create thread')))
    result = operations.start('reset-name', work, request_id=identifier)
    assert result['state'] == 'failed'
    assert operations.last(identifier)[1]['operation']['status'] == 503
    assert not operations.busy()
    assert operations.reserve('access-point') is not None
    work.assert_not_called()


def test_partial_start_cancels_queued_worker_before_replacement(monkeypatch):
    original_start = threading.Thread.start
    def partial_start(thread):
        original_start(thread)
        raise RuntimeError('failed after creation')
    operations = WifiOperations(delay=.03)
    work = Mock(return_value=WifiOutcome(200, 'old'))
    monkeypatch.setattr(threading.Thread, 'start', partial_start)
    operations.start('reset-name', work, request_id=issue_request_id())
    assert not operations.busy()
    assert operations.reserve('access-point') is not None
    time.sleep(.06)
    work.assert_not_called()


def test_partial_start_after_admission_keeps_slot_until_helper_finishes(monkeypatch):
    original_start = threading.Thread.start
    entered, release = threading.Event(), threading.Event()
    def work():
        entered.set()
        release.wait(1)
        return WifiOutcome(200, 'done')
    def partial_start(thread):
        original_start(thread)
        assert entered.wait(1)
        raise RuntimeError('failed after work began')
    operations = WifiOperations(delay=0)
    monkeypatch.setattr(threading.Thread, 'start', partial_start)
    operations.start('reset-name', work)
    assert operations.busy()
    assert operations.reserve('access-point') is None
    operations.close()
    assert operations.last()[1]['operation']['state'] == 'uncertain'
    release.set()
    deadline = time.monotonic() + 1
    while operations.busy() and time.monotonic() < deadline:
        time.sleep(.005)
    assert not operations.busy()


def test_wall_clock_rollback_does_not_extend_outcome_retention():
    wall, elapsed = [1000.0], [0.0]
    operations = WifiOperations(clock=lambda: wall[0], elapsed_clock=lambda: elapsed[0])
    identifier = operations.begin('reset-name')
    operations.finish(identifier, WifiOutcome(200, 'done'))
    wall[0] -= 100000
    elapsed[0] += operations.RESULT_LIFETIME + 1
    assert operations.last(identifier)[0] == 410



def test_router_worker_start_failure_leaves_exact_failed_result_and_no_busy_slot(monkeypatch):
    from jittersim.api.router_operations import RouterOperations
    work = Mock()
    operations = RouterOperations(work, 'test-router')
    identifier = issue_request_id()
    monkeypatch.setattr(threading.Thread, 'start', Mock(side_effect=RuntimeError('thread creation failed')))
    status, result = operations.start('/api/router/sync-ap', {'admin_password':'secret','request_id':identifier})
    assert status == 503
    assert result['operation_id'] == identifier
    assert operations.result(identifier, 'secret')[0] == 503
    assert not operations.status('secret')['busy']
    work.assert_not_called()


def test_legacy_synchronous_ap_call_keeps_complete_helper_response():
    import json
    from jittersim.api.server import ControlServer
    from jittersim.config.storage import ConfigRepository
    repository = ConfigRepository(':memory:')
    server = ControlServer(('127.0.0.1', 0), repository)
    try:
        expected = {'ok':True,'action':'configure-ap','commands_run':3,'detail':'original response'}
        helper = Mock()
        helper.configure_access_point.return_value = expected
        server.helper_client = helper
        status, _, body = server.write_response_for('/api/access-point', {'ssid':'Legacy','password':'legacy-password'})
        assert status == 200
        assert json.loads(body) == {'result':expected}
        helper.configure_access_point.assert_called_once_with(ssid='Legacy',password='legacy-password')
        assert not server.wifi_operations.busy()
    finally:
        server.server_close()
        repository.close()


def test_router_creation_failure_releases_reserved_wifi_slot(monkeypatch):
    import json
    from jittersim.api.server import ControlServer
    from jittersim.config.storage import ConfigRepository
    repository = ConfigRepository(':memory:')
    server = ControlServer(('127.0.0.1', 0), repository)
    try:
        server.helper_client = Mock()
        monkeypatch.setattr(threading.Thread, 'start', Mock(side_effect=RuntimeError('cannot create worker')))
        identifier = issue_request_id()
        status, _, body = server.write_response_for('/api/router/sync-ap', {
            'admin_password':'secret','expected_fingerprint':'a'*64,'background':True,'request_id':identifier})
        assert status == 503
        assert json.loads(body)['operation_id'] == identifier
        assert not server.wifi_operations.busy()
        assert server.wifi_operations.last(identifier)[1]['operation']['state'] == 'failed'
        server.helper_client.sync_access_point_from_router.assert_not_called()
    finally:
        server.server_close()
        repository.close()


def test_request_timeout_aborts_a_stalled_response_body():
    from tests.unit.test_dashboard_reconnect import function_source
    result = run_node('''
    let adminTokenRevision=1, adminTokenVerified=true, adminTokenChecking=false;
    let pendingFormSubmitButton=null, aborted=false;
    const element=()=>({value:"token"});const recordRead=()=>{};
    const fetch=async(path,options)=>({ok:true,status:200,json:()=>new Promise((resolve,reject)=>{
      options.signal.addEventListener("abort",()=>{aborted=true;reject(new Error("aborted body"));});
    })});
    ''' + request_support_source() + function_source('request') + '''
    const failed=await request("/api/wifi-operation?id=exact","GET",undefined,{timeoutMs:10})
      .then(()=>false,error=>error.connectionLost===true);
    console.log(JSON.stringify({failed,aborted}));
    ''')
    assert result == {'failed':True,'aborted':True}



def test_finished_exact_outcome_cannot_be_rewritten_by_later_refusal():
    operations = WifiOperations(delay=0)
    identifier = issue_request_id()
    operations.reserve('router-copy', identifier)
    operations.finish(identifier, WifiOutcome(200, 'copied', ssid='Home', security='WPA2'))
    before = operations.last(identifier)
    operations.finish(identifier, WifiOutcome(410, 'router result expired'))
    assert operations.last(identifier) == before
    assert operations.completed_action() == ('/api/router/sync-ap', 200)
    assert operations.completed_action() is None


def test_expired_router_repeat_keeps_confirmed_wifi_outcome_without_another_write():
    import json
    from jittersim.api.server import ControlServer
    from jittersim.config.storage import ConfigRepository
    from tests.unit.test_wifi_operations import GatedHelper
    repository = ConfigRepository(':memory:')
    server = ControlServer(('127.0.0.1', 0), repository)
    helper = GatedHelper()
    helper.release.set()
    server.helper_client = helper
    server.wifi_operations.delay = 0
    try:
        identifier = issue_request_id()
        payload = {'admin_password':'secret','expected_fingerprint':'a'*64,
                   'background':True,'request_id':identifier}
        assert server.write_response_for('/api/router/sync-ap', payload)[0] == 202
        deadline = time.monotonic() + 5
        while server.router_operations.result(identifier, 'secret')[0] == 202 and time.monotonic() < deadline:
            time.sleep(.005)
        assert server.router_operations.result(identifier, 'secret')[0] == 200
        before = server.wifi_operations.last(identifier)
        assert before[1]['operation']['state'] == 'succeeded'
        server.router_operations._requests[identifier]['finished'] -= server.router_operations.RESULT_LIFETIME + 1
        status, _, body = server.write_response_for('/api/router/sync-ap', payload)
        assert status == 410 and 'expired' in json.loads(body)['error'].lower()
        assert server.wifi_operations.last(identifier) == before
        assert helper.events.count('sync') == 1
    finally:
        server.server_close()
        repository.close()


def test_router_copy_refuses_an_identifier_used_for_a_different_action():
    from jittersim.api.server import ControlServer
    from jittersim.config.storage import ConfigRepository
    repository = ConfigRepository(':memory:')
    server = ControlServer(('127.0.0.1', 0), repository)
    helper = Mock()
    helper.preview_router_wifi.return_value = {
        'ssid':'Home','security':'WPA2','band':'2.4 GHz','fingerprint':'a'*64,'legacy':False}
    server.helper_client = helper
    try:
        identifier = issue_request_id()
        payload = {'admin_password':'secret','background':True,'request_id':identifier}
        assert server.write_response_for('/api/router/preview-ap', payload)[0] == 202
        deadline = time.monotonic() + 5
        while server.router_operations.result(identifier, 'secret')[0] == 202 and time.monotonic() < deadline:
            time.sleep(.005)
        assert server.router_operations.result(identifier, 'secret')[0] == 200
        status, _, _ = server.write_response_for('/api/router/sync-ap', {**payload,'expected_fingerprint':'a'*64})
        assert status == 409
        helper.preview_router_wifi.assert_called_once_with(admin_password='secret')
        helper.sync_access_point_from_router.assert_not_called()
        assert not server.wifi_operations.busy()
    finally:
        server.server_close()
        repository.close()

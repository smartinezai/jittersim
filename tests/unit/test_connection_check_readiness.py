"""Current settings must arrive before connection-check controls accept input."""

import pytest

from tests.unit.test_dashboard_connection_check import CHECK_SCRIPT, FAKE_NODES
from tests.unit.test_dashboard_speed_test import (
    NEEDS_NODE,
    SPEED_TEST_SCRIPT,
    SPEED_TEST_STUBS,
    run_node,
)

HARNESS = FAKE_NODES + SPEED_TEST_STUBS + SPEED_TEST_SCRIPT + CHECK_SCRIPT + """
document.body = fakeNode('body');
const originalCreate = document.createElement;
document.createElement = tag => {
  const node = originalCreate(tag);
  node.isConnected = true;
  node.showModal = () => { node.open = true; };
  node.close = () => { node.open = false; };
  node.remove = () => {
    node.isConnected = false;
    document.body.children = document.body.children.filter(child => child !== node);
  };
  return node;
};
function element(id) {
  const find = node => node.id === id ? node : node.children.map(find).find(Boolean);
  return find(document.body);
}
function connCheckRenderHistory() {}
function connCheckShowRunning() {}
function connCheckPoll() {}
function settings(defaultProbes = 100) {
  return {limits: {min_probes:20, max_probes:300, default_probes:defaultProbes,
    probes_per_second:10}, results:[], active:[]};
}
function blocked() {
  return element('conn-check-probes').disabled && element('conn-check-begin').disabled;
}
"""


@NEEDS_NODE
def test_initial_load_blocks_start_then_submits_the_entered_probe_count():
    actual = run_node(HARNESS + """
(async () => {
  const opening = connCheckOpen({mac_address:'aa'});
  const loadingBlocked = blocked();
  const closeAvailable = !element('conn-check-close').disabled;
  await connCheckStart();
  const requestsWhileLoading = pending.length;
  pending[0].resolve(settings());
  await opening;
  const ready = !blocked();
  const defaultProbes = element('conn-check-probes').value;
  element('conn-check-probes').value = '20';
  const starting = connCheckStart();
  const submitted = pending[1].body.probes;
  pending[1].resolve({id:'run'});
  await starting;
  console.log(JSON.stringify({loadingBlocked, closeAvailable, requestsWhileLoading,
    ready, defaultProbes, submitted}));
})();
""")
    assert actual == {
        "loadingBlocked": True, "closeAvailable": True, "requestsWhileLoading": 1,
        "ready": True, "defaultProbes": "100", "submitted": 20,
    }


@NEEDS_NODE
def test_failed_reload_keeps_controls_blocked_even_with_previous_settings():
    actual = run_node(HARNESS + """
(async () => {
  const first = connCheckOpen({mac_address:'aa'});
  pending[0].resolve(settings());
  await first;
  const reopening = connCheckOpen({mac_address:'aa'});
  pending[1].reject(new Error('Settings unavailable'));
  let error;
  try { await reopening; } catch (caught) { error = caught.message; }
  await connCheckStart();
  console.log(JSON.stringify({blocked:blocked(),
    closeAvailable:!element('conn-check-close').disabled,
    requests:pending.length, error}));
})();
""")
    assert actual == {"blocked": True, "closeAvailable": True, "requests": 2,
                      "error": "Settings unavailable"}


@NEEDS_NODE
@pytest.mark.parametrize("new_sign_in", [False, True])
@pytest.mark.parametrize("old_arrives_first", [False, True])
def test_old_response_cannot_enable_or_overwrite_current_controls(new_sign_in, old_arrives_first):
    actual = run_node(HARNESS + """
(async () => {
  const old = connCheckOpen({mac_address:'aa'});
  """ + ("connCheckClearPrivateState();" if new_sign_in else "") + """
  const current = connCheckOpen({mac_address:'bb'});
  let blockedAfterOld = null;
  if (""" + str(old_arrives_first).lower() + """) {
    pending[0].resolve(settings(100));
    await old;
    blockedAfterOld = blocked();
    pending[1].resolve(settings(200));
    await current;
  } else {
    pending[1].resolve(settings(200));
    await current;
    element('conn-check-probes').value = '20';
    pending[0].resolve(settings(100));
    await old;
  }
  console.log(JSON.stringify({blockedAfterOld, ready:!blocked(),
    probes:element('conn-check-probes').value, device:connCheckState.device.mac_address}));
})();
""")
    assert actual == {"blockedAfterOld": True if old_arrives_first else None,
                      "ready": True, "probes": "200" if old_arrives_first else "20",
                      "device": "bb"}

"""Exercise router credential errors with served handlers and offline responses."""

import shutil
import subprocess

import pytest

from tests.unit.test_floating_credentials import HARNESS, served_functions


def run_case(scenario):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required")
    setup = r"""
      const routerIds = new Set(['router-admin-password', 'check-router-password',
        'clear-router-password', 'remember-router-password', 'router-password-errors',
        'router-password-error-link']);
      element('router-credentials').contains = node => Boolean(node && routerIds.has(node.id));
      const documentBody = {id: 'body'};
      document.body = documentBody;
      element('router-admin-password').setAttribute('aria-describedby', 'credential-router-status');
      enter('admin-token', 'fixture token');
      enter('router-admin-password', 'fixture password');
      element('check-router-password').focus();
    """
    program = HARNESS + served_functions() + setup
    program += "\n(async () => {\n" + scenario
    program += "\nconsole.log('CASE-COMPLETE');\n})().catch(e => {console.error(e); process.exitCode = 1;});"
    result = subprocess.run([node, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("CASE-COMPLETE"), result.stdout


def test_old_router_check_cannot_repaint_after_admin_token_change():
    run_case(r"""
      const pending = deferred();
      network = () => pending.promise;
      const checking = checkRouterPassword();
      await new Promise(resolve => setImmediate(resolve));
      enter('admin-token', 'new fixture token');
      element('credential-router-status').textContent = 'New credential state';
      element('other-task').focus();
      pending.resolve({ok: false, status: 502,
        json: async () => ({error: 'Router login failed. Check the password.'})});
      await checking;
      assert.equal(element('credential-router-status').textContent, 'New credential state');
      assert.equal(document.activeElement, element('other-task'));
    """)


def test_rejected_then_edited_accepted_password_preserves_values_and_links_error():
    run_case(r"""
      tick('remember-router-password', true);
      const rejected = 'Router login failed. Check the router admin password and try again.';
      network = async () => ({ok: false, status: 502, json: async () => ({error: rejected})});
      await checkRouterPassword();
      assert.equal(routerPasswordRejected, true);
      assert.equal(element('router-admin-password').value, 'fixture password');
      assert.equal(element('remember-router-password').checked, true);
      assert.equal(element('router-admin-password').getAttribute('aria-invalid'), 'true');
      assert.equal(element('router-admin-password').getAttribute('aria-describedby'), 'credential-router-status');
      assert.equal(element('router-password-error-link').textContent,
        element('credential-router-status').textContent);
      assert.equal(element('router-password-errors').hidden, false);
      assert.equal(document.activeElement, element('router-password-errors'));
      let prevented = false;
      element('router-password-error-link').listeners.get('click')({preventDefault() {prevented = true;}});
      assert.equal(prevented, true);
      assert.equal(document.activeElement, element('router-admin-password'));
      const requests = networkCalls.length;
      await checkRouterPassword();
      assert.equal(networkCalls.length, requests, 'rejected password must remain blocked');
      assert.equal(element('router-admin-password').getAttribute('aria-invalid'), 'true');
      enter('router-admin-password', 'corrected fixture password');
      assert.equal(element('router-password-errors').hidden, true);
      assert.equal(element('router-password-error-link').textContent, '');
      assert.equal(element('router-admin-password').getAttribute('aria-invalid'), undefined);
      assert.match(element('credential-router-status').textContent, /Password changed/);
      network = async () => ({ok: true, json: async () => ({result: {ok: true}})});
      await checkRouterPassword();
      assert.equal(routerPasswordVerified, true);
      assert.equal(element('router-admin-password').value, 'corrected fixture password');
      assert.equal(element('remember-router-password').checked, true);
      assert.equal(element('router-password-errors').hidden, true);
      assert.equal(element('router-admin-password').getAttribute('aria-invalid'), undefined);
      assert.match(element('credential-router-status').textContent, /accepted/);
    """)


def test_blank_password_and_clear_keep_existing_focus_and_remember_behaviour():
    run_case(r"""
      tick('remember-router-password', true);
      enter('router-admin-password', '');
      await checkRouterPassword();
      assert.equal(networkCalls.length, 0);
      assert.equal(element('router-admin-password').getAttribute('aria-invalid'), 'true');
      assert.equal(document.activeElement, element('router-password-errors'));
      assert.match(element('router-password-error-link').textContent, /Enter the router admin password/);
      element('clear-router-password').listeners.get('click')();
      assert.equal(element('router-password-errors').hidden, true);
      assert.equal(element('router-password-error-link').textContent, '');
      assert.equal(element('router-admin-password').getAttribute('aria-invalid'), undefined);
      assert.equal(element('router-admin-password').value, '');
      assert.equal(element('remember-router-password').checked, true);
      assert.equal(document.activeElement, element('router-admin-password'));
    """)


@pytest.mark.parametrize("failure", [
    "Object.assign(new Error('Could not contact the router'), {status: 502})",
    "Object.assign(new Error('network helper is not configured'), {status: 503})",
    "Object.assign(new Error('Device verification is busy'), {status: 409, code: 'helper-busy'})",
    "Object.assign(new Error('Admin token rejected'), {status: 401})",
    "Object.assign(new Error('Device is not authorized'), {status: 403})",
    "lostConnectionError(new Error('The Pi is offline'))",
])
def test_other_failures_offer_recovery_without_rejecting_router_password(failure):
    run_case("routerRequest = async () => {throw " + failure + ";};" + r"""
      tick('remember-router-password', true);
      await checkRouterPassword();
      assert.equal(routerPasswordRejected, false);
      assert.equal(element('router-admin-password').getAttribute('aria-invalid'), undefined);
      assert.equal(element('router-admin-password').value, 'fixture password');
      assert.equal(element('remember-router-password').checked, true);
      assert.equal(element('router-password-errors').hidden, false);
      assert.equal(element('router-password-error-link').textContent,
        element('credential-router-status').textContent);
      assert.equal(document.activeElement, element('router-password-errors'));
      assert.equal(element('check-router-password').disabled, false);
    """)


def test_throttled_check_preserves_accepted_state_and_wait_policy():
    run_case(r"""
      network = async () => ({ok: true, json: async () => ({result: {ok: true}})});
      await checkRouterPassword();
      network = async () => ({ok: false, status: 429,
        json: async () => ({error: 'Wait 12 seconds before checking again.', retry_after: 12})});
      await checkRouterPassword();
      assert.equal(routerPasswordVerified, true);
      assert.equal(routerPasswordRejected, false);
      assert.equal(element('router-admin-password').getAttribute('aria-invalid'), undefined);
      assert.equal(element('router-password-errors').hidden, false);
      assert.equal(element('check-router-password').disabled, true);
      assert.equal(element('check-router-password').textContent, 'Wait 12 s');
      const requests = networkCalls.length;
      await checkRouterPassword();
      assert.equal(networkCalls.length, requests);
      assert.equal(element('router-password-errors').hidden, false);
    """)


def test_permitted_retry_clears_old_error_before_the_reply():
    run_case(r"""
      network = async () => ({ok: false, status: 503, json: async () => ({error: 'Helper unavailable'})});
      await checkRouterPassword();
      assert.equal(element('router-password-errors').hidden, false);
      const pending = deferred();
      network = () => pending.promise;
      const checking = checkRouterPassword();
      assert.equal(element('router-password-errors').hidden, true);
      assert.equal(element('router-password-error-link').textContent, '');
      await new Promise(resolve => setImmediate(resolve));
      pending.resolve({ok: true, json: async () => ({result: {ok: true}})});
      await checking;
      assert.equal(element('router-password-errors').hidden, true);
      assert.equal(routerPasswordVerified, true);
    """)


@pytest.mark.parametrize("move_during_check", [False, True])
def test_background_or_abandoned_check_does_not_take_focus(move_during_check):
    run_case(r"""
      const elsewhere = element('other-task');
      if (!MOVE) elsewhere.focus();
      const pending = deferred();
      network = () => pending.promise;
      const checking = checkRouterPassword();
      elsewhere.focus();
      await new Promise(resolve => setImmediate(resolve));
      pending.resolve({ok: false, status: 502,
        json: async () => ({error: 'Router login failed. Check the password.'})});
      await checking;
      assert.equal(document.activeElement, elsewhere);
      assert.equal(element('router-password-errors').hidden, false);
    """.replace("MOVE", str(move_during_check).lower()))


def test_old_router_rejection_cannot_repaint_after_password_edit():
    run_case(r"""
      const pending = deferred();
      network = () => pending.promise;
      const checking = checkRouterPassword();
      await new Promise(resolve => setImmediate(resolve));
      enter('router-admin-password', 'new fixture password');
      element('other-task').focus();
      const status = element('credential-router-status').textContent;
      pending.resolve({ok: false, status: 502,
        json: async () => ({error: 'Router login failed. Check the password.'})});
      await checking;
      assert.equal(element('credential-router-status').textContent, status);
      assert.equal(element('router-password-errors').hidden, true);
      assert.equal(element('router-admin-password').getAttribute('aria-invalid'), undefined);
      assert.equal(routerPasswordRejected, false);
      assert.equal(document.activeElement, element('other-task'));
    """)


def test_router_summary_preserves_description_and_does_not_add_live_regions():
    from jittersim.api.server import DASHBOARD_HTML

    assert '<div id="router-password-errors" class="form-error" tabindex="-1"' in DASHBOARD_HTML
    assert 'aria-labelledby="router-password-errors-title" hidden>' in DASHBOARD_HTML
    assert '<a id="router-password-error-link" href="#router-admin-password">' in DASHBOARD_HTML
    assert 'placeholder="Password for 192.168.0.1" aria-describedby="credential-router-status"' in DASHBOARD_HTML
    summary = DASHBOARD_HTML.split('<div id="router-password-errors"', 1)[1].split('</div>', 1)[0]
    assert 'role=' not in summary
    assert 'aria-live=' not in summary

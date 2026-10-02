"""Check the sidebar copy action as credentials and router work change."""

import shutil
import subprocess

import pytest

from tests.unit.test_floating_credentials import HARNESS, served_functions


def test_copy_requires_a_checked_token_and_helper_and_recovers_after_password_edits():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the served dashboard")
    scenario = r"""
      enter("admin-token", "fixture token");
      const copy = element("sync-router-ap");
      assert.equal(copy.disabled, true);
      assert.equal(copy.title, "Check the admin token first");
      adminTokenVerified = true;
      updateCredentialStatus();
      assert.equal(copy.disabled, true);
      assert.equal(copy.title, "Needs the Pi helper");
      helperConfigured = true;
      updateCredentialStatus();
      assert.equal(copy.disabled, false);
      assert.equal(copy.title, "Enter the router password first");
      const status = element("router-action-message");
      status.textContent = "Enter the router admin password first.";
      enter("router-admin-password", "rejected password");
      assert.equal(status.textContent, "");
      routerPasswordRejected = true;
      updateCredentialStatus();
      assert.equal(copy.disabled, true);
      assert.equal(copy.title, "Router password rejected. Enter it again.");
      enter("router-admin-password", "replacement password");
      assert.equal(copy.disabled, false);
      assert.equal(copy.title, "Copy the router Wi-Fi name, password and security to the Pi");
      routerActionPending = true;
      status.textContent = "Checking the router Wi-Fi security...";
      enter("router-admin-password", "another password");
      assert.equal(status.textContent, "Checking the router Wi-Fi security...");
      updateCredentialStatus();
      assert.equal(copy.disabled, true);
      routerActionPending = false;
      routerWifiRemoteBusy = true;
      updateCredentialStatus();
      assert.equal(copy.disabled, true);
      routerWifiRemoteBusy = false;
      routerWifiPolling = true;
      updateCredentialStatus();
      assert.equal(copy.disabled, true);
      routerWifiPolling = false;
      routerPasswordChecking = true;
      updateCredentialStatus();
      assert.equal(copy.disabled, true);
      routerPasswordChecking = false;
      updateCredentialStatus();
      assert.equal(copy.disabled, false);
      enter("admin-token", "replacement token");
      assert.equal(copy.disabled, true);
      assert.equal(copy.title, "Check the admin token first");
      console.log("CASE-COMPLETE");
    """
    program = HARNESS + served_functions() + "\n" + scenario
    result = subprocess.run([node, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "CASE-COMPLETE" in result.stdout

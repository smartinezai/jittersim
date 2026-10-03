"""Delayed branding replies must not outlive the sign-in that requested them."""

import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML


CASES = {
    "late_success_after_logout": """
      const pending = deferred(); request = () => pending.promise;
      const loading = loadBrand(); applyBrand(false);
      pending.resolve(brand); await loading;
      assert.equal(document.title, "Sign in");
      assert.equal(element("measure-command").textContent, "the measure command");
      assert.equal(brandInfo, null);
    """,
    "old_rejection_after_new_sign_in": """
      const pending = deferred(); request = () => pending.promise;
      const old = loadBrand(); adminTokenRevision++;
      request = async () => brand; await loadBrand();
      pending.reject(new Error("Token changed")); await old;
      applyBrand(false);
      assert.equal(element("measure-command").textContent, "the measure command");
      assert.equal(element("access-point-ssid").value, "");
      assert.equal(document.title, "Sign in");
    """,
    "newest_reply_wins": """
      const pending = deferred(); request = () => pending.promise;
      const old = loadBrand(); request = async () => brand; await loadBrand();
      pending.resolve({...brand, name:"Old name"}); await old;
      assert.equal(document.title, brand.name);
    """,
    "failed_current_reply_clears_brand": """
      request = async () => brand; await loadBrand();
      request = async () => {throw new Error("Rejected")}; await loadBrand();
      assert.equal(document.title, "Sign in");
      assert.equal(element("measure-command").textContent, "the measure command");
      assert.equal(element("access-point-ssid").value, "");
    """,
}


@pytest.mark.parametrize("case", CASES)
def test_brand_reply_ownership(case):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to exercise the served functions")
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
    start = script.index("let brandInfo =")
    functions = script[start:script.index("const privateDataPanelIds =", start)]
    harness = r"""
      const assert = require("node:assert/strict");
      let adminTokenRevision = 0, adminTokenVerified = true;
      const nodes = new Map();
      function element(id) {
        if (!nodes.has(id)) nodes.set(id, {value:"",textContent:"",setAttribute(){}});
        return nodes.get(id);
      }
      const document = {title:"Sign in", activeElement:null};
      const brand = {name:"JitterSim", command:"jittersim", default_ssid:"JitterSim"};
      let request;
      function deferred() {
        let resolve, reject;
        const promise = new Promise((yes,no) => {resolve=yes;reject=no});
        return {promise,resolve,reject};
      }
    """
    result = subprocess.run([node, "-"], input=harness + functions +
        "\n(async () => {" + CASES[case] +
        "console.log('COMPLETE')})().catch(e => {console.error(e);process.exitCode=1});",
        text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "COMPLETE" in result.stdout

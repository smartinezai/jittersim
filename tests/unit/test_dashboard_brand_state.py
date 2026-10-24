"""Delayed branding replies must not outlive the sign-in that requested them."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML


CASES = {
    "navigation_preserves_cleared_ssid_and_brand_state": """
      request = async () => brand; await loadBrand();
      showPage("router", true);
      const ssid = element("access-point-ssid");
      assert.equal(ssid.value, brand.default_ssid);
      ssid.value = "";
      document.activeElement = links[0];
      const loadedBrand = brandInfo, serial = brandRequestSerial;
      const command = element("measure-command");
      command.textContent = "Unchanged command text";
      showPage("devices", true);
      assert.equal(document.title, "Devices · " + brand.name);
      showPage("router", true);
      assert.equal(document.title, "Router and Wi-Fi · " + brand.name);
      assert.equal(ssid.value, "");
      assert.equal(command.textContent, "Unchanged command text");
      assert.equal(brandInfo, loadedBrand);
      assert.equal(brandRequestSerial, serial);
      adminTokenVerified = false;
      showPage("profiles", true);
      assert.equal(document.title, "Sign in");
      assert.equal(brandInfo, loadedBrand);
      assert.equal(brandRequestSerial, serial);
    """,
    "all_pages_use_static_region_labels": """
      request = async () => brand; await loadBrand();
      for (const [id, label] of Object.entries(labels)) {
        links.forEach(link => {link.textContent = "Private device token 42 Update available"});
        showPage(id, false);
        assert.equal(document.title, label + " · " + brand.name);
        assert.equal(document.body.dataset.section, id);
        assert.deepEqual(sections.filter(s => !s.hidden).map(s => s.dataset.page), [id]);
      }
    """,
    "initial_deep_link_and_pending_brand_use_current_page": """
      const pending = deferred(); request = () => pending.promise;
      const loading = loadBrand();
      window.location.hash = "#measurements";
      showPage(pageFromHash(window.location.hash), false);
      assert.equal(document.title, "Sign in");
      showPage("traffic", true);
      pending.resolve(brand); await loading;
      assert.equal(document.title, "Traffic · " + brand.name);
    """,
    "pending_brand_refresh_survives_signed_in_navigation": """
      request = async () => brand; await loadBrand();
      const pending = deferred(); request = () => pending.promise;
      const loading = loadBrand();
      showPage("access", true);
      assert.equal(document.title, "Access and rules · " + brand.name);
      pending.resolve(brand); await loading;
      assert.equal(document.title, "Access and rules · " + brand.name);
      const lockedReply = deferred(); request = () => lockedReply.promise;
      const lockedLoading = loadBrand();
      adminTokenVerified = false; applyBrand(false);
      showPage("updates", true);
      lockedReply.resolve(brand); await lockedLoading;
      assert.equal(document.title, "Sign in");
      assert.equal(brandInfo, null);
    """,
    "history_and_unknown_hash_follow_resolved_page": """
      request = async () => brand; await loadBrand();
      window.location.hash = "#profiles"; followLocation();
      assert.equal(document.title, "Profiles · " + brand.name);
      const scrollCount = scrolls.length;
      followLocation(); assert.equal(scrolls.length, scrollCount);
      window.location.hash = "#devices"; followLocation();
      assert.equal(document.title, "Devices · " + brand.name);
      window.location.hash = "#profiles"; followLocation();
      assert.equal(document.title, "Profiles · " + brand.name);
      window.location.hash = "#private-token"; followLocation();
      assert.equal(document.title, "Devices · " + brand.name);
      assert.deepEqual(historyWrites, []);
    """,
    "clicks_and_programmatic_tabs_preserve_history": """
      request = async () => brand; await loadBrand();
      let prevented = 0;
      const event = {button:0, preventDefault(){prevented++}};
      assert.equal(followTabLink(event, "setup"), true);
      assert.equal(document.title, "Router and Wi-Fi · " + brand.name);
      assert.equal(window.location.hash, "#router");
      followTabLink(event, "setup");
      assert.equal(prevented, 2);
      assert.equal(followTabLink({...event, ctrlKey:true}, "updates"), false);
      assert.equal(prevented, 2);
      assert.equal(document.title, "Router and Wi-Fi · " + brand.name);
      showTab("rules");
      assert.equal(document.title, "Schedules and limits · " + brand.name);
      assert.deepEqual(historyWrites, [["push", "#router"], ["replace", "#schedules"]]);
    """,
    "blocked_access_and_navigation_after_logout_stay_neutral": """
      request = async () => brand; await loadBrand();
      adminTokenVerified = false; applyBrand(false);
      showPage("diagnostics", true);
      assert.equal(document.title, "Sign in");
      assert.equal(brandInfo, null);
      await loadBrand();
      assert.equal(document.title, "Sign in");
      adminTokenVerified = true; await loadBrand();
      assert.equal(document.title, "Diagnostics · " + brand.name);
    """,
    "no_resolved_section_preserves_brand_fallback": """
      document.body.dataset.section = "unknown";
      request = async () => brand; await loadBrand();
      assert.equal(document.title, brand.name);
    """,
    "late_success_after_logout": """
      const pending = deferred(); request = () => pending.promise;
      const loading = loadBrand(); applyBrand(false);
      pending.resolve(brand); await loading;
      assert.equal(document.title, "Sign in");
      assert.equal(element("measure-command").textContent, "the measure command");
      assert.equal(brandInfo, null);
      assert.equal(element("update-token-command").textContent, "the setup command shown after sign-in");
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
    "private_command_is_cleared_on_sign_out": """
      applyBrand(false);
      assert.equal(element("update-token-command").textContent, "the setup command shown after sign-in");
      request = async () => brand; await loadBrand();
      assert.equal(element("update-token-command").textContent, "sudo " + brand.command + " update set-token");
      applyBrand(false);
      assert.equal(element("update-token-command").textContent, "the setup command shown after sign-in");
    """,
    "newest_reply_wins": """
      const pending = deferred(); request = () => pending.promise;
      const old = loadBrand(); request = async () => brand; await loadBrand();
      pending.resolve({...brand, name:"Old name"}); await old;
      assert.equal(document.title, "Devices · " + brand.name);
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
    navigation = re.findall(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL)[1]
    functions += navigation[:navigation.index("    function cardStartsOpen(")]
    for name in ("showPage", "followLocation"):
        functions += re.search(
            r"\n    function " + name + r"\(.*?\n    }\n", navigation, re.DOTALL
        ).group(0)
    functions += script[script.index("const TAB_PAGES ="):script.index("function noticeAnchor(")]
    labels = dict(re.findall(
        r'<section[^>]* data-page="([^"]+)" aria-label="([^"]+)"', DASHBOARD_HTML
    ))
    harness = "const labels = " + json.dumps(labels) + ";\n" + r"""
      const assert = require("node:assert/strict");
      let adminTokenRevision = 0, adminTokenVerified = true;
      const nodes = new Map();
      function element(id) {
        if (!nodes.has(id)) nodes.set(id, {value:"",textContent:"",attributes:{},
          setAttribute(key,value){this.attributes[key]=value},
          getAttribute(key){return this.attributes[key]},
          removeAttribute(key){delete this.attributes[key]}});
        return nodes.get(id);
      }
      const sections = Object.entries(labels).map(([id,label]) => {
        const node = element("page-" + id);
        node.dataset = {page:id}; node.setAttribute("aria-label", label);
        return node;
      });
      const links = Object.keys(labels).map(id => {
        const node = element("link-" + id); node.dataset = {pageLink:id}; return node;
      });
      const document = {title:"Sign in", activeElement:null, body:{dataset:{section:"devices"}},
        getElementById:id => nodes.get(id) || null,
        querySelectorAll:selector => selector === "[data-page]" ? sections : links};
      const historyWrites = [], scrolls = [];
      const window = {location:{hash:""}, scrollTo(...args){scrolls.push(args)}, history:{
        pushState(state,title,hash){historyWrites.push(["push",hash]);window.location.hash=hash},
        replaceState(state,title,hash){historyWrites.push(["replace",hash]);window.location.hash=hash}
      }};
      const phoneLayout = {matches:false};
      function revealNavLink() {}
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

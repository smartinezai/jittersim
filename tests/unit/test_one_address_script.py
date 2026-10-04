"""The nudge and the announcement notice on the dashboard page, run in node."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[2] / "src/jittersim/api/trust.js").read_text()
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")


def run_page(location_host, data):
    source = re.search(r"function showOneAddress.*?\n}\n", SCRIPT, re.DOTALL).group(0)
    program = """
const els = {};
for (const id of ['one-address-url', 'one-address-nudge', 'one-address-current',
                  'one-address-link', 'one-address-problem']) {
  els[id] = {hidden: true, textContent: '', href: ''};
}
function element(id) { return els[id]; }
const window = {location: {host: %s, hostname: %s}};
%s
showOneAddress(%s);
console.log(JSON.stringify(els));
""" % (
        json.dumps(location_host), json.dumps(location_host.split(":")[0]), source,
        json.dumps(data))
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


GOOD = {"address": "https://netlab.local:8080", "announce": {"state": "published", "message": "ok"}}


@needs_node
def test_opened_at_the_one_address_there_is_no_nudge():
    els = run_page("netlab.local:8080", GOOD)
    assert els["one-address-nudge"]["hidden"] is True
    assert els["one-address-url"]["textContent"] == "https://netlab.local:8080"
    assert els["one-address-problem"]["hidden"] is True


@needs_node
def test_the_case_of_the_host_name_does_not_matter():
    els = run_page("NetLab.Local:8080", GOOD)
    assert els["one-address-nudge"]["hidden"] is True


@needs_node
@pytest.mark.parametrize("host", ["192.168.0.23:8080", "192.168.50.1:8080", "pi.home.arpa:8080",
                                  "pi-lab.local:8080"])
def test_opened_at_another_address_the_one_address_is_offered(host):
    els = run_page(host, GOOD)
    assert els["one-address-nudge"]["hidden"] is False
    assert els["one-address-current"]["textContent"] == host
    assert els["one-address-link"]["href"] == "https://netlab.local:8080/"


@needs_node
def test_a_link_never_carries_the_token_or_anything_but_the_origin():
    els = run_page("192.168.0.23:8080", {**GOOD, "address": "https://netlab.local:8080/?token=x#y"})
    assert els["one-address-link"]["href"] == "https://netlab.local:8080/"


@needs_node
@pytest.mark.parametrize("address", ["http://netlab.local:8080", "javascript:alert(1)", "not a url", ""])
def test_an_address_that_is_not_https_is_ignored(address):
    els = run_page("192.168.0.23:8080", {**GOOD, "address": address})
    assert els["one-address-nudge"]["hidden"] is True
    assert els["one-address-link"]["href"] == ""
    assert els["one-address-url"]["textContent"] == ""


@needs_node
@pytest.mark.parametrize("state,shown", [
    ("published", False), ("partial", False), ("conflict", True), ("unavailable", True),
    ("stopped", True), ("waiting", True)])
def test_a_problem_with_the_announcement_is_shown_in_words(state, shown):
    announce = {"state": state, "message": "Another device already uses netlab.local."}
    els = run_page("netlab.local:8080", {**GOOD, "announce": announce})
    assert els["one-address-problem"]["hidden"] is (not shown)
    if shown:
        assert "already uses netlab.local" in els["one-address-problem"]["textContent"]
        assert "Diagnostics" in els["one-address-problem"]["textContent"]
        assert "jittersim" not in els["one-address-problem"]["textContent"].lower()


@needs_node
def test_a_missing_announcement_report_is_not_an_error():
    els = run_page("netlab.local:8080", {"address": "https://netlab.local:8080"})
    assert els["one-address-problem"]["hidden"] is True

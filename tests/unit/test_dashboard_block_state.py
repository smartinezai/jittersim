"""Run the dashboard's Block globally and Add to profile button logic in node."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
ENFORCE = "Apply the configuration to enforce it."


def served_functions():
    start = SCRIPT.index("function blockButtonState(")
    return SCRIPT[start : SCRIPT.index("function renderObservations(", start)]


def function_body(name):
    """Return the source of one top-level dashboard function."""

    match = re.search(r"\n    (?:async )?function " + re.escape(name) + r"\(", SCRIPT)
    assert match, name
    following = re.search(r"\n    (?:async )?function \w+\(", SCRIPT[match.end() :])
    end = match.end() + following.start() if following else len(SCRIPT)
    return SCRIPT[match.start() : end]


def run_node(program):
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


BLOCK_ADD = {
    "label": "Block globally",
    "path": "/api/observations/add-global-block",
    "busy": "Adding facebook.com to the global block list...",
    "done": "Added facebook.com to the global block list. " + ENFORCE,
}
BLOCK_REMOVE = {
    "label": "Unblock",
    "path": "/api/observations/remove-global-block",
    "busy": "Removing facebook.com from the global block list...",
    "done": "Removed facebook.com from the global block list. " + ENFORCE,
}

CASES = {
    "an_unblocked_name_offers_block_globally": (
        "blockButtonState({domain:'facebook.com', globally_blocked:false})",
        BLOCK_ADD,
    ),
    "a_blocked_name_offers_unblock": (
        "blockButtonState({domain:'facebook.com', globally_blocked:true})",
        BLOCK_REMOVE,
    ),
    "a_missing_flag_counts_as_not_blocked": (
        "blockButtonState({domain:'facebook.com'})",
        BLOCK_ADD,
    ),
    "only_a_real_true_counts_as_blocked": (
        "blockButtonState({domain:'facebook.com', globally_blocked:'true'})",
        BLOCK_ADD,
    ),
    "a_name_in_the_profile_is_already_in": (
        "profileButtonState('facebook.com', "
        "{name:'Slow', website_domains:['a.test','facebook.com']})",
        {"label": "Already in Slow", "disabled": True},
    ),
    "a_name_missing_from_the_profile_can_be_added": (
        "profileButtonState('facebook.com', {name:'Slow', website_domains:['a.test']})",
        {"label": "Add to profile", "disabled": False},
    ),
    "a_parent_entry_does_not_cover_a_subdomain": (
        "profileButtonState('www.facebook.com', {name:'Slow', website_domains:['facebook.com']})",
        {"label": "Add to profile", "disabled": False},
    ),
    "a_subdomain_entry_does_not_cover_the_parent": (
        "profileButtonState('facebook.com', {name:'Slow', website_domains:['www.facebook.com']})",
        {"label": "Add to profile", "disabled": False},
    ),
    "a_profile_without_a_website_list_can_be_added_to": (
        "profileButtonState('facebook.com', {name:'Slow'})",
        {"label": "Add to profile", "disabled": False},
    ),
    "no_profile_disables_the_button": (
        "profileButtonState('facebook.com', undefined)",
        {"label": "Add to profile", "disabled": True},
    ),
    "a_null_profile_disables_the_button": (
        "profileButtonState('facebook.com', null)",
        {"label": "Add to profile", "disabled": True},
    ),
}


@NODE
@pytest.mark.parametrize("name", CASES)
def test_button_states(name):
    expression, expected = CASES[name]
    output = run_node(served_functions() + "\nconsole.log(JSON.stringify(" + expression + "));\n")
    assert json.loads(output) == expected


@NODE
@pytest.mark.parametrize(
    "count, text",
    [(0, "0 queries"), (1, "1 query"), (2, "2 queries"), (1000, "1000 queries")],
)
def test_query_count_text_uses_the_singular_for_one(count, text):
    output = run_node(served_functions() + "\nconsole.log(queryCountText(" + str(count) + "));\n")
    assert output == text


def run_change(domain, blocked, fail):
    """Click the block button once with stubbed requests and report what happened."""

    request = "Promise.reject(new Error('the helper is busy'))" if fail else "Promise.resolve({})"
    program = (
        served_functions()
        + "\nconst calls = [];\n"
        + "function request(path, method, body) { calls.push(['request', path, method, body]);"
        + " return " + request + "; }\n"
        + "function showMessage(text, kind) { calls.push(['message', text, kind]); }\n"
        + "function refreshAfterSuccess(text) { calls.push(['success', text]); }\n"
        + "function setButtonBusy(button, busy) { button.busy = busy; }\n"
        + function_body("changeGlobalBlock")
        + "\n(async () => {\n"
        + "  const observation = {domain: '" + domain + "', globally_blocked: "
        + ("true" if blocked else "false") + "};\n"
        + "  const button = {busy: null};\n"
        + "  let changes = 0;\n"
        + "  await changeGlobalBlock(observation, button, () => { changes += 1; });\n"
        + "  console.log(JSON.stringify({calls, blocked: observation.globally_blocked,"
        + " busy: button.busy, changes}));\n"
        + "})();\n"
    )
    return json.loads(run_node(program))


@NODE
def test_blocking_sends_the_add_request_and_remembers_the_new_state():
    result = run_change("facebook.com", blocked=False, fail=False)
    assert result["calls"] == [
        ["message", "Adding facebook.com to the global block list...", "working"],
        ["request", "/api/observations/add-global-block", "POST", {"domain": "facebook.com"}],
        ["success", "Added facebook.com to the global block list. " + ENFORCE],
    ]
    assert result["blocked"] is True
    assert result["busy"] is False
    assert result["changes"] == 1


@NODE
def test_unblocking_sends_the_remove_request_and_remembers_the_new_state():
    result = run_change("facebook.com", blocked=True, fail=False)
    assert result["calls"] == [
        ["message", "Removing facebook.com from the global block list...", "working"],
        ["request", "/api/observations/remove-global-block", "POST", {"domain": "facebook.com"}],
        ["success", "Removed facebook.com from the global block list. " + ENFORCE],
    ]
    assert result["blocked"] is False
    assert result["busy"] is False
    assert result["changes"] == 1


@NODE
@pytest.mark.parametrize("blocked", [False, True])
def test_a_failed_change_keeps_the_state_and_shows_the_error(blocked):
    result = run_change("facebook.com", blocked=blocked, fail=True)
    assert result["calls"][-1] == ["message", "the helper is busy", "error"]
    assert not any(call[0] == "success" for call in result["calls"])
    assert result["blocked"] is blocked
    assert result["busy"] is False
    assert result["changes"] == 1


def test_the_success_text_matches_the_save_access_rules_button():
    # Both only save the list. Enforcing it waits for Apply.
    assert "Access rules saved. Apply the configuration to enforce them." in SCRIPT
    assert ENFORCE in served_functions()


def test_each_row_shows_the_badge_and_both_buttons_from_the_state_helpers():
    body = function_body("renderObservations")
    assert "blockButtonState(observation)" in body
    assert 'badge.className = "observation-badge";' in body
    assert 'badge.textContent = "Blocked";' in body
    assert re.search(r"changeGlobalBlock\(\s*observation,\s*blockButton", body)
    assert "profileButtonState(observation.domain, selectedProfile)" in body
    assert "queryCountText(observation.query_count)" in body
    assert '" queries"' not in body
    assert "innerHTML" not in body


def test_the_profile_buttons_follow_the_profile_select_of_their_card():
    body = function_body("renderObservations")
    assert 'targetProfile.addEventListener("change"' in body
    assert "profiles.find((profile) => profile.id === targetProfile.value)" in body


def test_the_badge_has_a_style_and_the_remove_path_is_a_write_path():
    assert ".observation-badge {" in DASHBOARD_HTML
    assert "/api/observations/remove-global-block" in served_functions()

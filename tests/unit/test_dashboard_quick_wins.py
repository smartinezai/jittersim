"""One word per idea, one message at a time, and no internal codes where a name will do."""

import re

import pytest

from jittersim.api.server import DASHBOARD_HTML, _connected_device_payload

# Words the glossary retired. Each idea has one name now: Protected, Start, Stop, Everyone.
RETIRED = (
    "Unaffected by profiles",
    "Unaffected by all profiles",
    "Make unaffected",
    "Remove protection",
    "Start with saved settings",
    "Re-apply saved settings",
    "Re-applied",
    "Stop general profile",
    "General profile",
    "Saved device rules paused",
    "Unnamed client",
    "Base profile",
)


@pytest.mark.parametrize("phrase", RETIRED)
def test_the_page_no_longer_uses_the_retired_word(phrase):
    assert phrase not in DASHBOARD_HTML


def test_the_new_words_are_on_the_buttons():
    assert re.search(r'id="apply-button"[^>]*>Start</button>', DASHBOARD_HTML)
    assert re.search(r'id="general-profile-stop"[^>]*>Stop for everyone</button>', DASHBOARD_HTML)
    assert 'protectedRow ? "Stop protecting" : "Protect"' in DASHBOARD_HTML
    assert '<h2 id="unaffected-title">Protected devices</h2>' in DASHBOARD_HTML


def test_menus_show_profile_names_and_add_the_code_only_when_two_profiles_share_a_name():
    shared = 'profiles.filter((other) => other.name === profile.name).length > 1 ? profile.name + " (" + profile.id + ")" : profile.name'
    assert DASHBOARD_HTML.count("option.textContent = " + shared + ";") == 3
    assert 'option.textContent = profile.name + " (" + profile.id + ")";' not in DASHBOARD_HTML


def test_a_row_names_the_profile_not_its_code():
    assert '"Profile: " + (device.assigned_profile_id ? savedProfileName(device.assigned_profile_id)' in DASHBOARD_HTML
    assert "(override: \" + savedProfileName(device.group_profile_id)" in DASHBOARD_HTML


def test_a_client_without_a_name_is_announced_by_its_host_name_or_address():
    base = {"mac_address": "aa:bb:cc:dd:ee:01", "ipv4_address": "192.168.50.24"}
    kwargs = {"assignments_by_address": {}, "labels_by_mac": {}}
    named = _connected_device_payload({**base, "hostname": "pixel-8"}, **kwargs)
    assert named["display_name"] == "pixel-8"
    by_address = _connected_device_payload({**base, "hostname": None}, **kwargs)
    assert by_address["display_name"] == "192.168.50.24"
    by_mac = _connected_device_payload({"mac_address": base["mac_address"]}, **kwargs)
    assert by_mac["display_name"] == "aa:bb:cc:dd:ee:01"
    saved = _connected_device_payload(
        {**base, "hostname": "pixel-8"}, assignments_by_address={},
        labels_by_mac={base["mac_address"]: {"display_name": "Anna's phone", "device_type": "phone"}})
    assert saved["display_name"] == "Anna's phone"


def test_a_result_shows_in_the_toast_and_nowhere_a_screen_reader_would_read_it_again():
    # The message line stays for scripts but is hidden and silent.
    line = re.search(r'<p id="message"[^>]*>', DASHBOARD_HTML).group(0)
    assert "hidden" in line and 'aria-hidden="true"' in line and 'aria-live="off"' in line
    assert "role=" not in line
    status = re.search(r'<p id="token-check-status"[^>]*>', DASHBOARD_HTML).group(0)
    assert 'aria-live="off"' in status and "role=" not in status
    toast = re.search(r'<div id="toast"[^>]*>', DASHBOARD_HTML).group(0)
    assert 'role="status"' in toast
    # Every message, with or without a kind, reaches the toast.
    body = re.search(r"function showMessage\(message, kind\) \{.*?\n    \}", DASHBOARD_HTML, re.DOTALL).group(0)
    assert 'showToast(message, kind || "info");' in body
    assert "if (kind) showToast" not in body


def test_the_toast_is_small_leaves_by_itself_and_keeps_its_dismiss_button():
    css = re.search(r"\.toast \{(.*?)\}", DASHBOARD_HTML, re.DOTALL).group(1)
    assert re.search(r"bottom: 1rem;\s*right: 1rem;", css)
    assert "width: min(22rem," in css and "max-height: 40vh" in css
    body = re.search(r"function showToast\(message, kind\) \{.*?\n    \}", DASHBOARD_HTML, re.DOTALL).group(0)
    assert 'if (kind !== "working")' in body
    assert 'kind === "error" ? 15000 : 6000' in body
    assert 'id="toast-dismiss"' in DASHBOARD_HTML


def test_no_edit_scroll_animates_for_a_person_who_asked_for_less_motion():
    assert 'behavior: "smooth"' not in DASHBOARD_HTML
    # The two edit buttons and the reveal helper all ask the browser first.
    assert DASHBOARD_HTML.count('prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"') == 2
    assert 'behavior: reduceMotion ? "auto" : "smooth"' in DASHBOARD_HTML


def test_the_hints_point_at_the_profiles_page():
    assert "Create a group below" not in DASHBOARD_HTML
    assert "Create a saved profile below" not in DASHBOARD_HTML
    assert '"Create a group on the Profiles page"' in DASHBOARD_HTML

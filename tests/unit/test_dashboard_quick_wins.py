"""One word per idea, one message at a time, and no internal codes where a name will do."""

import re

import pytest

from jittersim.api.server import DASHBOARD_HTML, _connected_device_payload
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository
from jittersim.engine.eligibility import device_protection_states

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
    assert DASHBOARD_HTML.count("option.textContent = profileLabels.get(profile.id);") == 3
    assert 'profiles.filter((other) => other.name === profile.name)' not in DASHBOARD_HTML



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
    assert "hidden" in line and 'aria-hidden="true"' in line
    assert "role=" not in line and "aria-live" not in line
    status = re.search(r'<p id="token-check-status"[^>]*>', DASHBOARD_HTML).group(0)
    assert "role=" not in status and "aria-live" not in status
    toast = re.search(r'<div id="toast"[^>]*>', DASHBOARD_HTML).group(0)
    assert 'role=' not in toast
    # Two persistent regions say every result: status for most and alert for errors. Neither
    # changes its aria-live, and nothing else in the page carries an aria-live attribute.
    assert 'id="action-announcer" class="action-announcer" role="status" aria-atomic="true"' in DASHBOARD_HTML
    assert 'id="action-alert" class="action-announcer" role="alert" aria-atomic="true"' in DASHBOARD_HTML
    assert "aria-live" not in re.sub(r"<script>.*?</script>", "", DASHBOARD_HTML, flags=re.DOTALL)
    # Every message, with or without a kind, reaches the toast.
    body = re.search(r"function showMessage\(message, kind\) \{.*?\n    \}", DASHBOARD_HTML, re.DOTALL).group(0)
    assert 'showToast(message, kind || "info");' in body
    assert "if (kind) showToast" not in body


def test_the_toast_is_small_leaves_by_itself_and_keeps_its_dismiss_button():
    css = re.search(r"\.toast \{(.*?)\}", DASHBOARD_HTML, re.DOTALL).group(1)
    assert re.search(r"bottom: 1rem;\s*right: 1rem;", css)
    assert "width: min(22rem," in css and "max-height: 40vh" in css
    body = re.search(r"function showToast\(message, kind\) \{.*?\n    \}", DASHBOARD_HTML, re.DOTALL).group(0)
    assert 'kind === "working" || kind === "error" ? 0' in body
    assert 'scheduleToastDismissal();' in body
    assert 'id="toast-dismiss"' in DASHBOARD_HTML


def test_no_edit_scroll_animates_for_a_person_who_asked_for_less_motion():
    assert 'behavior: "smooth"' not in DASHBOARD_HTML
    # Both edit buttons use the merged helper that honors reduced motion.
    assert DASHBOARD_HTML.count('behavior: typeof scrollBehavior === "function" ? scrollBehavior()') == 2
    assert 'return reduce ? "auto" : "smooth";' in DASHBOARD_HTML
    assert 'behavior: reduceMotion ? "auto" : "smooth"' in DASHBOARD_HTML



def test_the_hints_point_at_the_profiles_page():
    assert "Create a group below" not in DASHBOARD_HTML
    assert "Create a saved profile below" not in DASHBOARD_HTML
    assert '"Create a group on the Profiles page."' in DASHBOARD_HTML
    assert 'groups.length ? "Choose group..." : "No groups"' in DASHBOARD_HTML


def test_no_label_a_screen_reader_reads_still_says_re_apply():
    # The help icon beside Start is read out loud, so its label follows the same word.
    assert "re-apply" not in DASHBOARD_HTML.lower()
    assert 'aria-label="Start help"' in DASHBOARD_HTML


def test_a_service_list_note_names_the_profile_set_for_everyone_not_a_general_profile():
    note = re.search(r'<p class="form-note">While a list is on[^<]*</p>', DASHBOARD_HTML).group(0)
    assert "general profile" not in note
    assert "profile for Everyone" in note


def test_the_notes_about_a_paused_saved_device_say_protected_and_not_unaffected(tmp_path):
    repository = ConfigRepository(tmp_path / "settings.db")
    try:
        repository.save_profile(Profile(id="slow", name="Slow", upload=Impairment(delay_ms=100)))
        repository.save_device(Device("laptop", "Laptop", "192.168.50.20", "slow", "aa:bb:cc:dd:ee:01"))
        repository.save_device(Device("tablet", "Tablet", "192.168.50.21", "slow"))
        repository.save_unaffected_device(UnaffectedDevice("aa:bb:cc:dd:ee:01", "Laptop", 1))
        states = device_protection_states(
            repository, {"association_status_available": True, "devices": []})
    finally:
        repository.close()
    notes = [state["effective_note"] for state in states.values() if state["effective_note"]]
    assert len(notes) == 2, states
    assert not [note for note in notes if "naffected" in note], notes
    assert any("Protected list" in note for note in notes), notes


def test_the_token_hint_does_not_promise_a_command_the_setup_notes_do_not_have():
    # The setup notes show how to read the token on the Pi. They have no ssh command for a Mac.
    hint = re.search(r'adminTokenHelp\.textContent =(.*?);\n', DASHBOARD_HTML, re.DOTALL).group(1)
    assert "ssh" not in hint.lower()
    assert "setup notes" in hint and "stored on the Pi" in hint


def test_the_router_page_does_not_send_a_phone_to_a_left_panel_it_does_not_have():
    # On a phone the Copy button sits in the Controls sheet. The sidebar is the same place on a desktop.
    assert "left panel" not in DASHBOARD_HTML
    assert "Copy router Wi-Fi to Pi in the sidebar (on a phone, open Controls first)" in DASHBOARD_HTML


def test_the_traffic_legend_is_a_named_group_because_a_plain_div_cannot_carry_a_label():
    assert '<div class="traffic-legend" role="group" aria-label="Graph legend">' in DASHBOARD_HTML

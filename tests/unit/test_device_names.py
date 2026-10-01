import pytest

from jittersim.config.device_names import (
    UNKNOWN_DEVICE_NAME,
    is_automatic_device_name,
    resolve_device_name,
)


@pytest.mark.parametrize(
    "name",
    ["Client 192.168.50.124", "Client 10.0.0.1", " Client 192.168.50.7 "],
)
def test_automatic_names_are_recognized(name):
    assert is_automatic_device_name(name)


@pytest.mark.parametrize(
    "name",
    ["", None, "Alice phone", "Client", "Client 192.168.50", "Client 192.168.50.1.9",
     "Client abc", "client 192.168.50.124x", "My Client 192.168.50.124"],
)
def test_other_names_are_not_automatic(name):
    assert not is_automatic_device_name(name)


def test_saved_label_wins_over_everything():
    assert resolve_device_name(
        saved_label="Sam iPhone", saved_name="Alice phone", hostname="iPhone-12"
    ) == "Sam iPhone"


def test_a_name_the_user_chose_beats_the_hostname():
    assert resolve_device_name(
        saved_name="Kitchen tablet", hostname="Galaxy-Tab"
    ) == "Kitchen tablet"


def test_the_automatic_placeholder_loses_to_the_hostname():
    assert resolve_device_name(
        saved_name="Client 192.168.50.124", hostname="MacBook-Pro"
    ) == "MacBook-Pro"


def test_the_automatic_placeholder_alone_is_unknown():
    assert resolve_device_name(saved_name="Client 192.168.50.124") == UNKNOWN_DEVICE_NAME


def test_nothing_known_is_unknown():
    assert resolve_device_name() == UNKNOWN_DEVICE_NAME


def test_blank_and_non_text_values_are_ignored_and_names_are_trimmed():
    assert resolve_device_name(saved_label="  ", saved_name=None, hostname="  Host  ") == "Host"
    assert resolve_device_name(saved_label=5, saved_name=[], hostname=None) == UNKNOWN_DEVICE_NAME


BIDI_CONTROLS = [
    "‎", "‏",
    "‪", "‫", "‬", "‭", "‮",
    "⁦", "⁧", "⁨", "⁩",
]
UNUSABLE_CHARACTERS = ["\x7f", "\x07", "\n"] + BIDI_CONTROLS


@pytest.mark.parametrize("name", ["Client ١٩٢.١٦٨.٥٠.١", "Client １９２.１６８.５０.１"])
def test_digits_from_other_scripts_do_not_make_an_automatic_name(name):
    assert not is_automatic_device_name(name)


@pytest.mark.parametrize("character", UNUSABLE_CHARACTERS)
def test_a_name_with_a_hidden_control_character_falls_through_to_the_next_source(character):
    assert resolve_device_name(
        saved_label="Sam" + character + "iPhone", saved_name="Alice phone", hostname="iPhone-12"
    ) == "Alice phone"
    assert resolve_device_name(
        saved_name="Alice" + character + "phone", hostname="iPhone-12"
    ) == "iPhone-12"
    assert resolve_device_name(
        saved_name="Client 192.168.50.124", hostname="iPhone" + character + "12"
    ) == UNKNOWN_DEVICE_NAME


@pytest.mark.parametrize(
    "label",
    ["Sam's 📱 phone", "Café tablet", "Zoë 👨‍👩‍👧 laptop", "مكتب", "日本語の名前"],
)
def test_emoji_and_accents_in_a_name_still_work(label):
    assert resolve_device_name(saved_label=label, hostname="iPhone-12") == label
    assert resolve_device_name(saved_name=label, hostname="iPhone-12") == label
    assert resolve_device_name(hostname=label) == label

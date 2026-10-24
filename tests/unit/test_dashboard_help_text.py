"""Check that the profile form and every ID field explain themselves with a help tooltip."""

from html.parser import HTMLParser

from jittersim.api.server import DASHBOARD_HTML

VOID_TAGS = {"input", "br", "img", "meta", "link", "hr"}
ID_PATTERN_PREFIX = "[a-z0-9][a-z0-9_"


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []

    @property
    def classes(self):
        return (self.attrs.get("class") or "").split()

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self, skip_help=True):
        parts = []
        for child in self.children:
            if isinstance(child, Node):
                if child.tag == "select" or (skip_help and "help-icon" in child.classes):
                    continue
                parts.append(child.text(skip_help))
            else:
                parts.append(child)
        return " ".join("".join(parts).split())

    def has_ancestor(self, predicate):
        node = self.parent
        while node is not None:
            if predicate(node):
                return True
            node = node.parent
        return False

    def nearest(self, tag):
        node = self.parent
        while node is not None:
            if node.tag == tag:
                return node
            node = node.parent
        return None


class Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", [], None)
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in VOID_TAGS:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(Node(tag, attrs, self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.current = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def parse_dashboard():
    builder = Builder()
    builder.feed(DASHBOARD_HTML)
    return builder.root


PAGE = parse_dashboard()


def help_icon(node):
    for child in node.walk():
        if child is not node and "help-icon" in child.classes:
            return child
    return None


def profile_form():
    return next(node for node in PAGE.walk() if node.attrs.get("id") == "profile-form")


def profile_form_labels():
    """Return every label in the Save profile form with the Upload or Download heading above it."""

    labels = []
    heading = ""
    for node in profile_form().walk():
        if node.tag == "h3":
            heading = node.text()
        elif node.tag == "label":
            labels.append((heading, node))
    return labels


def field_name(label):
    """The visible label text without the unit or hint in brackets."""

    return label.text().split(" (")[0]


def in_randomness_card(label):
    return label.has_ancestor(lambda node: "randomness-field" in node.classes)


def all_help_icons():
    return [node for node in PAGE.walk() if "help-icon" in node.classes]


def id_inputs():
    """The text inputs that hold an ID use the shared ID pattern."""

    found = [
        node
        for node in PAGE.walk()
        if node.tag == "input" and (node.attrs.get("pattern") or "").startswith(ID_PATTERN_PREFIX)
    ]
    return found


def describe(label):
    control = next((node for node in label.walk() if node.tag in ("input", "select")), None)
    return "%s (%s)" % (label.text(), control.attrs.get("id") if control else "no control")


def test_every_profile_form_field_has_a_help_icon():
    missing = [
        describe(label)
        for _heading, label in profile_form_labels()
        if not in_randomness_card(label) and help_icon(label) is None
    ]
    assert missing == []


def test_every_randomness_card_title_keeps_its_help_icon():
    titles = [node for node in profile_form().walk() if "randomness-title" in node.classes]
    assert len(titles) == 14
    assert [node.text() for node in titles if help_icon(node) is None] == []


def test_every_help_text_is_a_full_sentence_of_sensible_length():
    icons = all_help_icons()
    assert icons
    problems = []
    for icon in icons:
        text = icon.attrs.get("data-help") or ""
        aria = icon.attrs.get("aria-label") or ""
        if not 40 <= len(text) <= 260:
            problems.append("length %d: %s" % (len(text), text))
        if not text.endswith("."):
            problems.append("no full stop: %s" % text)
        if "\\" in text or '"' in text:
            problems.append("backslash or quote: %s" % text)
        if aria.split()[-1:] != ["help"]:
            problems.append("aria-label must end with help: %s" % aria)
    assert problems == []


EXPECTED_PROFILE_FIELDS = {
    "Profile ID": 1,
    "Name": 1,
    "Delay": 2,
    "Jitter": 2,
    "Loss": 2,
    "Bandwidth": 2,
    "Random seed": 1,
    "Website mode": 1,
    "Profile domains": 1,
    "Reorder": 2,
    "Duplicate": 2,
    "Corrupt": 2,
}


def test_the_fields_people_asked_about_have_help_in_both_directions():
    labels = [label for _heading, label in profile_form_labels() if not in_randomness_card(label)]
    for name, count in EXPECTED_PROFILE_FIELDS.items():
        matching = [label for label in labels if field_name(label) == name]
        assert len(matching) == count, name
        for label in matching:
            assert help_icon(label) is not None, name


def test_upload_and_download_get_a_copy_in_each_set():
    by_heading = {}
    for heading, label in profile_form_labels():
        if not in_randomness_card(label):
            by_heading.setdefault(heading, []).append(field_name(label))
    for name in ("Delay", "Jitter", "Loss", "Bandwidth"):
        assert name in by_heading["Upload"]
        assert name in by_heading["Download"]
    for name in ("Reorder", "Duplicate", "Corrupt"):
        assert name in by_heading["Upload variation"]
        assert name in by_heading["Download variation"]


NAME_SENTENCES = ("is what people read", "are what people read")
# A schedule has no name so its ID is the title in the list.
OTHER_SENTENCES = {
    "schedule-id": ("schedule list", "list below"),
}
CHARACTER_RULE = "Use only lowercase letters and digits and hyphens and underscores."


def id_help(field):
    icon = help_icon(field.nearest("label"))
    return None if icon is None else icon.attrs.get("data-help") or ""


def test_every_id_field_explains_what_an_id_is_next_to_a_name():
    inputs = id_inputs()
    assert len(inputs) >= 9
    problems = []
    for field in inputs:
        name = field.attrs.get("id")
        text = id_help(field)
        if text is None:
            problems.append("%s has no help icon" % name)
            continue
        # The verb in "names this schedule" must not count as the contrast with a Name.
        sentences = OTHER_SENTENCES.get(name, NAME_SENTENCES)
        if "ID" not in text or not any(sentence in text for sentence in sentences):
            problems.append("%s help must mention ID and say %s: %s" % (name, sentences, text))
    assert problems == []


def test_every_id_pattern_field_states_the_allowed_characters():
    patterned = [field for field in id_inputs() if field.attrs.get("pattern")]
    assert len(patterned) >= 8
    problems = [
        field.attrs.get("id")
        for field in patterned
        if CHARACTER_RULE not in (id_help(field) or "")
    ]
    assert problems == []


def test_the_device_id_help_says_each_device_needs_its_own_ip_address():
    field = next(node for node in id_inputs() if node.attrs.get("id") == "device-id")
    assert "Each device needs its own IP address." in id_help(field)


def test_reorder_help_says_it_needs_a_delay_and_jitter_alone_is_not_enough():
    labels = [label for _heading, label in profile_form_labels() if field_name(label) == "Reorder"]
    assert len(labels) == 2
    for label in labels:
        text = help_icon(label).attrs.get("data-help")
        assert "unless the Delay in the same direction is above 0" in text
        assert "Jitter alone is not enough." in text


def test_no_two_different_fields_share_a_help_text():
    keys_by_text = {}
    for icon in all_help_icons():
        owner = icon.parent
        kind = "randomness card"
        if "randomness-title" not in owner.classes:
            owner = owner.nearest("label") or owner
            kind = "field"
        keys_by_text.setdefault(icon.attrs.get("data-help"), set()).add((kind, field_name(owner)))
    shared = {text: keys for text, keys in keys_by_text.items() if len(keys) > 1}
    assert shared == {}

    icons = [help_icon(field.nearest("label")) for field in id_inputs()]
    id_texts = [icon.attrs.get("data-help") for icon in icons if icon is not None]
    assert len(set(id_texts)) == len(id_texts)


def test_global_block_editor_describes_the_address_limit_as_permanent_help():
    editor = next(node for node in PAGE.walk() if node.attrs.get("id") == "website-blocklist")
    note_id = editor.attrs["aria-describedby"]
    notes = [node for node in PAGE.walk() if node.attrs.get("id") == note_id]
    assert len(notes) == 1
    note = notes[0]
    assert note.parent is editor.nearest("label").parent.parent
    assert note.tag == "p" and "policy-note" in note.classes
    assert "hidden" not in note.attrs and "role" not in note.attrs
    assert note.text() == (
        "While JitterSim runs, the global block list blocks up to 4096 resolved addresses. "
        "With household access rules on, DNS still refuses every listed name. "
        "With those rules off, some listed sites may remain reachable above this limit."
    )

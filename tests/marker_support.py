"""Let a child process announce itself without a polling parent reading half a marker.

A test that starts a child and waits for it to write a pid or a group number into a
file sees the file as soon as it exists. `Path.write_text` creates the file empty
first and fills it a moment later, so a parent that polls `exists()` and then reads
can find an empty marker. The child instead writes a temporary name and renames it
into place, so the marker either does not exist or holds its whole contents.
"""

# Source code for a `python -c` child: put it in front of the child's own source and
# call `publish_marker(path, text)` where the child used to call `write_text`.
PUBLISH_MARKER = (
    "def publish_marker(path, text):\n"
    "    import os\n"
    "    temporary = path + '.partial'\n"
    "    with open(temporary, 'w') as handle:\n"
    "        handle.write(text)\n"
    "    os.replace(temporary, path)\n"
)

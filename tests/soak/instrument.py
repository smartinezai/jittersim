"""Count what the process under test writes, and who asked for it.

`install()` must run before jittersim is imported. It wraps `sqlite3.connect` so every
connection reports its statements, and wraps the Python calls that write files (`open` and
`os.open` in a write mode, `os.replace`, `os.rename`, `os.fsync`). Each write is booked under a
task: the dashboard route when a request handler is on the stack, else the thread and the first
jittersim function that is not the storage layer. The storage function (what was written) is
kept next to it.

A database transaction is booked as "changed" when SQLite reports changed rows between BEGIN
and COMMIT and as "unchanged" otherwise. A commit without changed rows usually costs no disk
write in SQLite, but an UPDATE that stores the same values does dirty pages and counts as changed.
"""

import builtins
import io
import os
import sqlite3
import sys
import threading
import weakref
from collections import Counter

_REQUEST_METHODS = frozenset(("do_GET", "do_POST", "do_HEAD", "do_PUT", "do_DELETE"))
_PLUMBING = frozenset(("wrapper", "_commit", "transaction", "__enter__", "__exit__", "_transaction"))
_WRITE_WORDS = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER")

counts = Counter()
_lock = threading.Lock()
_state = threading.local()
_ignored_prefixes = []
_installed = False
_originals = {}


def ignore_paths_under(prefix):
    """Do not count file writes below this folder (the harness' own output)."""

    _ignored_prefixes.append(os.path.realpath(str(prefix)))


def _thread_label():
    name = threading.current_thread().name
    # "Thread-12 (process_request_thread)" and "Thread-12" become one label.
    head = name.split(" ")[0]
    if head.startswith("Thread-") and head[7:].isdigit():
        return "thread"
    return name.rstrip("0123456789-_") or name


def task():
    """Return (task, storage function) for the code that is writing now."""

    frame = sys._getframe(2)
    route = where = storage = None
    while frame is not None:
        code = frame.f_code
        name = code.co_filename.replace(os.sep, "/")
        if route is None and code.co_name in _REQUEST_METHODS:
            handler = frame.f_locals.get("self")
            route = "%s %s" % (code.co_name[3:], str(getattr(handler, "path", "?")).split("?")[0])
        if "/jittersim/" in name and "/tests/" not in name:
            short = name.split("/jittersim/", 1)[1]
            if short in ("config/storage.py", "config/workflows.py"):
                if code.co_name not in _PLUMBING:
                    storage = code.co_name  # the outermost storage call wins
            elif where is None:
                where = "%s:%s" % (short, code.co_name)
        frame = frame.f_back
    if route is not None:
        return route, storage or where or "?"
    return "%s %s" % (_thread_label(), where or "?"), storage or "-"


def _book(kind, amount=1, key=None):
    owner, what = key or task()
    with _lock:
        counts[(kind, owner, what)] += amount


def _ignored(path):
    try:
        real = os.path.realpath(os.fspath(path))
    except TypeError:
        return False
    return any(real.startswith(prefix) for prefix in _ignored_prefixes)


def _trace(connection_ref):
    def trace(statement):
        connection = connection_ref()
        if connection is None:
            return
        word = statement.lstrip()[:8].upper()
        if word.startswith("BEGIN"):
            _state.begin = connection.total_changes
            return
        if word.startswith(_WRITE_WORDS):
            _book("sql write statements")
            return
        if word.startswith("COMMIT"):
            before = getattr(_state, "begin", None)
            _state.begin = None
            changed = before is None or connection.total_changes != before
            _book("db commits changed" if changed else "db commits unchanged")
    return trace


class _Connection(sqlite3.Connection):
    """A plain connection that a weak reference can point at (the C type cannot)."""


def _wrap_connect(real):
    def connect(*args, **kwargs):
        kwargs.setdefault("factory", _Connection)
        connection = real(*args, **kwargs)
        try:
            connection.set_trace_callback(_trace(weakref.ref(connection)))
        except Exception:  # a connection the trace cannot follow is still a connection
            pass
        return connection
    return connect


def _writes(mode=None, flags=None):
    if mode is not None:
        return isinstance(mode, str) and any(letter in mode for letter in "wax+")
    return bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC))


def _wrap_open(real):
    def opened(file, mode="r", *args, **kwargs):
        if not isinstance(file, int) and _writes(mode=mode) and not _ignored(file):
            _book("file opens for writing")
        return real(file, mode, *args, **kwargs)
    return opened


def _wrap_os_open(real):
    def opened(path, flags, *args, **kwargs):
        if _writes(flags=flags) and not _ignored(path):
            _book("file opens for writing")
        return real(path, flags, *args, **kwargs)
    return opened


def _wrap_rename(real, kind):
    def renamed(source, target, *args, **kwargs):
        if not _ignored(target):
            _book(kind)
        return real(source, target, *args, **kwargs)
    return renamed


def _wrap_fsync(real):
    def synced(fd):
        _book("fsync calls")
        return real(fd)
    return synced


def install():
    """Wrap the write calls once. Safe to call twice."""

    global _installed
    if _installed:
        return
    _installed = True
    _originals.update({"connect": sqlite3.connect, "open": io.open, "os_open": os.open,
                       "replace": os.replace, "rename": os.rename, "fsync": os.fsync})
    sqlite3.connect = _wrap_connect(sqlite3.connect)
    builtins.open = io.open = _wrap_open(io.open)
    os.open = _wrap_os_open(os.open)
    os.replace = _wrap_rename(os.replace, "file replaces")
    os.rename = _wrap_rename(os.rename, "file replaces")
    os.fsync = _wrap_fsync(os.fsync)


def uninstall():
    """Put the real calls back (for a test that installed the wrappers in its own process)."""

    global _installed
    if not _installed:
        return
    sqlite3.connect = _originals["connect"]
    builtins.open = io.open = _originals["open"]
    os.open, os.replace, os.rename, os.fsync = (
        _originals["os_open"], _originals["replace"], _originals["rename"], _originals["fsync"])
    _installed = False


def snapshot():
    with _lock:
        return [{"kind": kind, "task": owner, "what": what, "count": value}
                for (kind, owner, what), value in sorted(counts.items())]

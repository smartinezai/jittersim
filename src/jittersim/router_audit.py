"""Keep bounded router-operation evidence without request or response text."""

import json
import logging.handlers
import os
import stat
import time
from pathlib import Path


class PrivateRotatingHandler(logging.handlers.RotatingFileHandler):
    def _open(self):
        descriptor = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                             | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "a", encoding="utf-8")

    def handleError(self, record):
        # Storage failure must not print records or break an operation.
        pass


class RouterAudit:
    ACTIONS = frozenset({"router-check", "router-wifi", "router-status",
                         "router-sync-ap", "router-preview-ap", "router-wifi-credentials"})
    OUTCOMES = frozenset({"start", "success", "failure", "refused"})

    def __init__(self, handler):
        self.handler = handler
        try:
            value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            self.boot_id = value if len(value) == 36 and all(c in "0123456789abcdef-" for c in value) else None
        except OSError:
            self.boot_id = None
        self.process = os.getpid()

    @classmethod
    def open(cls, directory: Path):
        try:
            directory.mkdir(mode=0o700, parents=False, exist_ok=True)
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                return None
            handler = PrivateRotatingHandler(directory / "operations.jsonl", maxBytes=65536,
                                             backupCount=1, encoding="utf-8")
            return cls(handler)
        except (OSError, AttributeError):
            return None

    def record(self, action, enabled, outcome, elapsed, operation=0):
        if action not in self.ACTIONS or outcome not in self.OUTCOMES:
            return
        data = {"time": time.time(), "action": action, "outcome": outcome,
                "boot_id": self.boot_id, "process": self.process, "operation": operation,
                "elapsed_ms": min(3600000, max(0, int(elapsed * 1000)))}
        if action == "router-wifi" and type(enabled) is bool:
            data["requested_enabled"] = enabled
        message = json.dumps(data, separators=(",", ":"))
        self.handler.handle(logging.LogRecord("router-audit", logging.INFO, "", 0,
                                             message, (), None))

"""The router login for runs that nobody attends. Only the owner of the file can read it."""

import os
import tempfile
from pathlib import Path
from typing import Optional


class RouterLoginStore:
    """The router login for runs that nobody attends. Only its owner can read the file."""

    def __init__(self, path) -> None:
        self._path = Path(path)

    def save(self, password: str) -> None:
        if not isinstance(password, str) or not password or len(password) > 1024:
            raise ValueError("router password is invalid")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self._path.parent, prefix=".router-login-")
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(password)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def load(self) -> Optional[str]:
        try:
            if self._path.stat().st_mode & 0o077:
                # A file that others can read is not trusted.
                return None
            return self._path.read_text(encoding="utf-8") or None
        except OSError:
            return None

    def clear(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass

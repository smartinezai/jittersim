"""Read bounded socket input under one total deadline."""

import io
import socket
import time
from typing import Any, Optional


class DeadlineReader(io.RawIOBase):
    """Bound all input reads to one deadline, including a slow byte stream."""

    def __init__(self, source: io.RawIOBase, connection: socket.socket, deadline: float,
                 prefix: bytes = b"") -> None:
        super().__init__()
        self._source = source
        self._connection = connection
        self._deadline = deadline
        self._prefix = prefix

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> Optional[int]:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("request input timed out")
        self._connection.settimeout(remaining)
        if self._prefix:
            count = min(len(buffer), len(self._prefix))
            buffer[:count] = self._prefix[:count]
            self._prefix = self._prefix[count:]
            return count
        return self._source.readinto(buffer)

    def extend_to(self, deadline: float) -> None:
        """Give a long upload its own, later deadline once its request was accepted."""
        self._deadline = deadline

    def close(self) -> None:
        try:
            self._source.close()
        finally:
            super().close()


def read_line(connection: socket.socket, limit: int, deadline: float) -> bytes:
    """Read one bounded line without renewing the timeout for each arriving byte."""

    data = bytearray()
    while len(data) < limit:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("helper response deadline expired")
        connection.settimeout(remaining)
        chunk = connection.recv(min(4096, limit - len(data)))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        newline = data.find(b"\n")
        if newline >= 0:
            return bytes(data[:newline + 1])
    return bytes(data)

"""Transport abstraction base."""

from __future__ import annotations

import abc


class Transport(abc.ABC):
    """Abstract transport interface.

    HTTP layer must not know whether it is plain TCP or TLS.
    """

    @abc.abstractmethod
    def recv(self, n: int) -> bytes:
        """Receive up to n bytes. May raise BlockingIOError."""
        ...

    @abc.abstractmethod
    def send(self, data: bytes) -> int:
        """Send data, return bytes sent. May raise BlockingIOError."""
        ...

    @abc.abstractmethod
    def close(self) -> None:
        """Close transport."""
        ...

    @abc.abstractmethod
    def fileno(self) -> int:
        """Return file descriptor for selector."""
        ...

    @property
    def closed(self) -> bool:  # type: ignore[no-untyped-def]
        return False

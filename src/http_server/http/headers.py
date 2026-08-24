"""Case-insensitive HTTP headers abstraction."""

from __future__ import annotations

from typing import Iterable, Iterator


class Headers:
    """HTTP headers with case-insensitive lookup.

    Header names are treated case-insensitively per RFC 9110.
    Original casing of the first occurrence is preserved for serialization,
    but lookup is always lower-cased.

    Each header name maps to a single value string. Duplicate names are
    handled by the parser (fail-closed for Content-Length) — this container
    simply keeps the last value if duplicates are set.
    """

    def __init__(self, initial: Iterable[tuple[str, str]] | None = None) -> None:
        # lower -> (original_name, value)
        self._store: dict[str, tuple[str, str]] = {}
        if initial:
            for name, value in initial:
                self.set(name, value)

    # -- mutation --

    def set(self, name: str, value: str) -> None:
        """Add or replace header."""
        self._validate_name(name)
        self._validate_value(value)
        key = name.lower()
        self._store[key] = (name, value)

    def add(self, name: str, value: str) -> None:
        """Alias for set — kept for simple API."""
        self.set(name, value)

    # -- access --

    def get(self, name: str, default: str | None = None) -> str | None:
        entry = self._store.get(name.lower())
        if entry is None:
            return default
        return entry[1]

    def get_original_name(self, name: str) -> str | None:
        """Return original casing if exists."""
        entry = self._store.get(name.lower())
        return entry[0] if entry else None

    def __getitem__(self, name: str) -> str:
        val = self.get(name)
        if val is None:
            raise KeyError(name)
        return val

    def __setitem__(self, name: str, value: str) -> None:
        self.set(name, value)

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        return name.lower() in self._store

    def __len__(self) -> int:
        return len(self._store)

    def __iter__(self) -> Iterator[str]:
        for _key, (orig, _val) in self._store.items():
            yield orig

    def items(self) -> Iterator[tuple[str, str]]:
        for _key, (orig, val) in self._store.items():
            yield (orig, val)

    def items_lower(self) -> Iterator[tuple[str, str]]:
        """Iterate with lower-cased names."""
        for key, (_orig, val) in self._store.items():
            yield (key, val)

    def to_dict(self) -> dict[str, str]:
        """Return dict with original-cased keys."""
        return {orig: val for _, (orig, val) in self._store.items()}

    def __repr__(self) -> str:
        return f"Headers({self.to_dict()!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Headers):
            # Compare lower-cased mapping
            if len(self._store) != len(other._store):
                return False
            for k, (_, v) in self._store.items():
                ov = other._store.get(k)
                if ov is None or ov[1] != v:
                    return False
            return True
        if isinstance(other, dict):
            # Compare case-insensitively if dict provided
            lower_self = {k: v for k, (_, v) in self._store.items()}
            # Actually store values: self._store is lower->(orig,val)
            lower_self2 = {k: val for k, (_, val) in self._store.items()}
            lower_other = {str(k).lower(): v for k, v in other.items()}
            return lower_self2 == lower_other
        return NotImplemented

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name:
            raise ValueError("header name must not be empty")
        # Disallow control chars, spaces, colon per RFC
        for ch in name:
            if ch == ":" or ch <= " " or ord(ch) >= 127:
                raise ValueError(f"invalid header name: {name!r}")

    @staticmethod
    def _validate_value(value: str) -> None:
        # Header values MUST NOT contain CRLF (injection)
        if "\r" in value or "\n" in value:
            raise ValueError(f"invalid header value (CRLF injection): {value!r}")

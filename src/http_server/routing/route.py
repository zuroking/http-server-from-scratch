"""Route representation for Phase 5."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


_TOKEN_RE = re.compile(r"[A-Za-z0-9!#$%&'*+\-.^_`|~]+")
_PARAM_RE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _split_path(path: str) -> list[str]:
    """Split path into segments, normalizing.

    - "/" -> []
    - "/users/42" -> ["users", "42"]
    - "/users/" -> ["users"] (trailing slash normalized)
    - "users" (no leading slash) is still split, validation elsewhere requires leading slash.
    """
    if path == "/":
        return []
    # Strip leading/trailing slashes, then split
    # Keep empty check for root
    stripped = path.strip("/")
    if not stripped:
        return []
    return stripped.split("/")


@dataclass(frozen=True, slots=True)
class Route:
    """Single route definition.

    Attributes:
        method: HTTP method upper-cased (e.g., "GET").
        pattern: Original pattern string (e.g., "/users/{id}").
        handler: Callable handling the request.
        param_names: Tuple of parameter names extracted from pattern.
    """

    method: str
    pattern: str
    handler: Callable[..., Any]
    param_names: tuple[str, ...] = field(default=(), init=False)
    _segments: tuple[str, ...] = field(default=(), init=False, repr=False)
    _is_param: tuple[bool, ...] = field(default=(), init=False, repr=False)

    def __post_init__(self) -> None:
        # Validate method
        if not isinstance(self.method, str) or not self.method:
            raise ValueError("method must be non-empty string")
        method_upper = self.method.upper()
        # Validate token (RFC)
        if not _TOKEN_RE.fullmatch(method_upper):
            raise ValueError(f"Invalid method: {self.method!r}")
        object.__setattr__(self, "method", method_upper)

        # Validate pattern
        if not isinstance(self.pattern, str) or not self.pattern:
            raise ValueError("pattern must be non-empty string")
        if not self.pattern.startswith("/"):
            raise ValueError(f"pattern must start with '/': {self.pattern!r}")
        # Reject pattern containing query or whitespace
        if "?" in self.pattern or " " in self.pattern or "\n" in self.pattern or "\r" in self.pattern:
            raise ValueError(f"Invalid pattern: {self.pattern!r}")
        # Reject double slashes? Allow but normalize? We'll reject "//" inside except root
        if "//" in self.pattern:
            raise ValueError(f"pattern must not contain '//': {self.pattern!r}")

        # Validate handler
        if not callable(self.handler):
            raise TypeError("handler must be callable")

        # Parse segments
        segments = _split_path(self.pattern)
        # For pattern "/" we keep empty
        # Validate each segment
        param_names: list[str] = []
        is_param: list[bool] = []
        for seg in segments:
            m = _PARAM_RE.match(seg)
            if m:
                name = m.group(1)
                if name in param_names:
                    raise ValueError(f"Duplicate param name {name!r} in pattern {self.pattern!r}")
                param_names.append(name)
                is_param.append(True)
            else:
                # Literal segment must not contain '{' or '}'
                if "{" in seg or "}" in seg:
                    raise ValueError(f"Invalid segment {seg!r} in pattern {self.pattern!r}")
                if not seg:
                    raise ValueError(f"Empty segment in pattern {self.pattern!r}")
                # Literal must be valid path segment (no spaces)
                if " " in seg:
                    raise ValueError(f"Invalid segment {seg!r}")
                is_param.append(False)

        object.__setattr__(self, "param_names", tuple(param_names))
        object.__setattr__(self, "_segments", tuple(segments))
        object.__setattr__(self, "_is_param", tuple(is_param))

    def match(self, path: str) -> dict[str, str] | None:
        """Try to match path against this route pattern.

        Args:
            path: Request path (e.g., "/users/42", without query string). Should start with "/".

        Returns:
            dict of params if matched, empty dict for exact match without params, or None if not matched.
        """
        if not isinstance(path, str):
            return None
        if not path.startswith("/"):
            return None
        # Normalize path same way as pattern
        path_segments = _split_path(path)
        if len(path_segments) != len(self._segments):
            return None
        params: dict[str, str] = {}
        for pat_seg, is_p, path_seg in zip(self._segments, self._is_param, path_segments):
            if is_p:
                # Capture param; path_seg must be non-empty
                if not path_seg:
                    return None
                # Extract param name from pattern segment
                # pat_seg is like "{id}"
                name = pat_seg[1:-1]
                params[name] = path_seg
            else:
                if pat_seg != path_seg:
                    return None
        return params

    def __repr__(self) -> str:
        return f"Route({self.method} {self.pattern} -> {self.handler!r})"

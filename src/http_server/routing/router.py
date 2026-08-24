"""Router with exact and param routes, 404/405 handling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from http_server.http.errors import MethodNotAllowedError, NotFoundError
from http_server.http.request import Request
from http_server.routing.route import Route


@dataclass(frozen=True, slots=True)
class RouteMatch:
    """Result of successful routing."""

    route: Route
    handler: Callable[..., Any]
    params: dict[str, str]


class Router:
    """HTTP router supporting exact and {param} routes.

    Two-stage matching:
      1. Find all routes with matching path (param-aware)
      2. Among them, find method match -> handler, else 405 with Allow
      3. If no path matches -> 404

    Supports HEAD fallback to GET if HEAD not explicitly registered (optional).
    """

    def __init__(self) -> None:
        self._routes: list[Route] = []

    # -- registration --

    def add(self, method: str, path: str, handler: Callable[..., Any]) -> Route:
        """Register route.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: Pattern like "/" or "/users/{id}"
            handler: Callable

        Returns:
            Created Route

        Raises:
            ValueError: duplicate method+pattern or invalid args
        """
        # Normalize method upper for duplicate check
        method_upper = method.upper() if isinstance(method, str) else method
        # Check duplicate
        for r in self._routes:
            if r.method == method_upper and r.pattern == path:
                raise ValueError(f"Duplicate route {method_upper} {path}")
        route = Route(method=method, pattern=path, handler=handler)
        self._routes.append(route)
        return route

    # Alias for decorator style: router.route("GET", "/path")
    def route(self, method: str, path: str):  # type: ignore[no-untyped-def]
        def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
            self.add(method, path, handler)
            return handler

        return decorator

    def add_route(self, route: Route) -> None:
        """Add pre-built Route."""
        for r in self._routes:
            if r.method == route.method and r.pattern == route.pattern:
                raise ValueError(f"Duplicate route {route.method} {route.pattern}")
        self._routes.append(route)

    # -- matching helpers --

    def _find_candidates(self, path: str) -> list[tuple[Route, dict[str, str]]]:
        candidates: list[tuple[Route, dict[str, str]]] = []
        for route in self._routes:
            params = route.match(path)
            if params is not None:
                candidates.append((route, params))
        return candidates

    def allowed_methods(self, path: str) -> set[str]:
        """Return set of methods allowed for path (based on pattern match)."""
        candidates = self._find_candidates(path)
        return {route.method for route, _ in candidates}

    def find_all(self, path: str) -> list[Route]:
        """Return all routes matching path regardless of method."""
        return [route for route, _ in self._find_candidates(path)]

    # -- main resolve --

    def match(self, method: str, path: str) -> tuple[Callable[..., Any], dict[str, str]]:
        """Match method and path.

        Args:
            method: HTTP method
            path: Request path without query (e.g., "/users/42")

        Returns:
            (handler, params)

        Raises:
            NotFoundError (404) if no path matches
            MethodNotAllowedError (405) with allowed attribute if path matches but method doesn't
        """
        if not isinstance(method, str) or not method:
            raise ValueError("method must be non-empty string")
        if not isinstance(path, str) or not path:
            raise ValueError("path must be non-empty string")

        # Strip query string if mistakenly passed (defensive)
        if "?" in path:
            path = path.split("?", 1)[0]
        if not path.startswith("/"):
            path = "/" + path

        candidates = self._find_candidates(path)
        if not candidates:
            raise NotFoundError(f"No route for {method} {path}")

        method_upper = method.upper()
        # Exact method match
        for route, params in candidates:
            if route.method == method_upper:
                return route.handler, params

        # HEAD fallback to GET if HEAD not found but GET exists
        if method_upper == "HEAD":
            for route, params in candidates:
                if route.method == "GET":
                    return route.handler, params

        # No method match -> 405
        allowed = sorted({route.method for route, _ in candidates})
        raise MethodNotAllowedError(f"Method {method} not allowed for {path}", allowed=allowed)

    def resolve(self, request: Request) -> RouteMatch:
        """Resolve Request object to RouteMatch.

        Convenience wrapper around match(method, path).
        """
        handler, params = self.match(request.method, request.path)
        # Find route object for completeness
        # Retrieve route that produced handler+params
        for route, p in self._find_candidates(request.path):
            if route.handler is handler and p == params:
                return RouteMatch(route=route, handler=handler, params=params)
        # Fallback (should not happen) - find by handler
        for route in self._routes:
            if route.handler is handler:
                return RouteMatch(route=route, handler=handler, params=params)
        # Generic
        # Create dummy route
        dummy = Route(method=request.method, pattern=request.path, handler=handler)
        return RouteMatch(route=dummy, handler=handler, params=params)

    # Compatibility helpers for tests expecting different APIs

    def lookup(self, method: str, path: str) -> tuple[Callable[..., Any], dict[str, str]] | None:
        """Try match, return None instead of raising for 404, raise for 405 still."""
        try:
            return self.match(method, path)
        except NotFoundError:
            return None

    def match_or_none(self, method: str, path: str) -> tuple[Callable[..., Any], dict[str, str]] | None:
        try:
            return self.match(method, path)
        except (NotFoundError, MethodNotAllowedError):
            return None

    def has_route(self, method: str, path: str) -> bool:
        try:
            self.match(method, path)
            return True
        except Exception:
            return False

    def __len__(self) -> int:
        return len(self._routes)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._routes)

    def clear(self) -> None:
        self._routes.clear()

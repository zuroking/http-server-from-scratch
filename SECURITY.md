# Security Policy

> Russian version: [SECURITY_ru.md](SECURITY_ru.md)

## Reporting a vulnerability

This is an educational HTTP server — it is intentionally *not* hardened for the open internet without a reverse proxy, but implementation bugs that break its documented guarantees are treated as security issues.

**Please do not report security vulnerabilities through public GitHub issues.**

Report them privately via [GitHub Security Advisories] ("Security" tab → "Report a vulnerability") or directly to the maintainer at **zuroking69@gmail.com**.

Include as much of the following as you can:

- The affected component (`http/parser.py`, `http/response.py`, `routing/`, `connection.py`, `transport/`, `tls_connection.py`, `event_loop.py`) or `ServerConfig` limit
- Step-by-step reproduction or a proof of concept (e.g., raw `curl`/`nc` bytes, or a minimal `socket` script)
- Expected vs. actual behavior, and which documented invariant from [ARCHITECTURE.md](ARCHITECTURE.md) §31 is violated
- Threat-model context from the scope below

You will get an initial response within 7 days. Fixes for confirmed issues are released as soon as practical, and you will be credited in the release notes unless you prefer to stay anonymous.

## Scope: what counts as a vulnerability

Useful reference — the project's security guarantees and explicit non-goals are documented in [ARCHITECTURE.md](ARCHITECTURE.md) §18-19, §31 and [README.md](README.md#security-model--limitations). Reports contradicting that documented scope will be closed as working-as-documented — but if you believe a documented decision is wrong, an `ARCHITECTURE.md` issue is welcome.

**In-scope examples:**

- Parser bypass leading to unbounded memory growth: limits (`max_request_line_size`, `max_headers_size`, `max_header_count`, `max_body_size`) not enforced *during* accumulation
- Ambiguous framing accepted instead of fail-closed: duplicate `Content-Length` with different values, `Transfer-Encoding: chunked` treated as `Content-Length`, or `Content-Length > max_body_size` waiting for body instead of immediate `413`
- Leftover bytes after `DONE` lost (pipelined `GET /second` in same `recv` vanishes) or pipelining executed concurrently
- `CRLF`/`header injection` via `Response` headers, reason phrase, or `http_version` not raising `ValueError`
- `Connection` keep-alive violating HTTP/1.0 vs 1.1 semantics, or `EVENT_WRITE` permanently registered causing busy wakeups
- `TLSTransport` leaking `SSLWantReadError/WriteError` into `parser`/`router`, or custom crypto/TLS implementation
- `Router` failing to distinguish `404` vs `405` or generating non-sorted `Allow` header, or `{param}` matching across `/`
- Timeouts (`request_timeout`, `keep_alive_timeout`) not applied or bypassable via slowloris

**Out of scope:**

- HTTP/2, WebSockets, `chunked` request bodies, compression, caching, proxying — explicitly not implemented (see ARCHITECTURE.md §32)
- DoS requiring already-privileged local execution (e.g., filling the disk where logs are written)
- Weak handler logic (e.g., a demo `"/users/{id}"` handler returning 500) — file an issue, not a security report
- Missing `ARCHITECTURE_ru.md` translation or badge count — docs issue

## Supported versions

Only the latest tagged release receives security fixes. Use a tagged release, not an arbitrary commit from `main`, when reporting.

## Hardening notes for deployers

If you put this server behind the open internet, front it with a hardened reverse proxy (Nginx, Caddy, Cloudflare). Do not expose `cert.pem`/`key.pem` and never commit them — they are blocked by `.gitignore`.

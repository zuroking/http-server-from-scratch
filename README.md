# http-server-from-scratch

![Python](https://img.shields.io/badge/python-3.12+-blue)
![Tests](https://img.shields.io/badge/tests-171%20passing-brightgreen)
![Type check](https://img.shields.io/badge/mypy-strict-blueviolet)
![License](https://img.shields.io/badge/license-MIT-green)

**Languages:** **English** · [Русский](README_RU.md)

An educational HTTP/1.1 server built from scratch on raw TCP sockets — `selectors.DefaultSelector`, a hand-written incremental parser, a response serializer, a state machine, and `ssl.SSLContext` for TLS. It ships with keep-alive, timeouts, routing with `{param}`, and a real event loop, all without `Flask`, `FastAPI`, `http.server`, or `asyncio`. The whole stack — TCP, HTTP, routing, TLS — is implemented by hand.

> **What this is (and isn't).** This server does **plain HTTP/1.1** (and minimal HTTP/1.0 compatibility). It handles `Content-Length` bodies, not chunked request bodies. It keeps connections alive and preserves pipelined bytes, but it does not do HTTP/2, WebSockets, compression, caching, or proxying — all explicitly out of scope. See [Security model & limitations](#security-model--limitations) and [Architecture](#architecture).

---

## Table of contents

- [Quickstart](#quickstart)
- [Highlights](#highlights)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the server](#running-the-server)
- [Routing](#routing)
- [Security model & limitations](#security-model--limitations)
- [Testing](#testing)
- [Development](#development)
- [Performance notes](#performance-notes)
- [Roadmap](#roadmap)
- [FAQ](#faq)
- [Contributing](#contributing)
- [License](#license)

## Quickstart

If you just want to see it run, here's the whole loop end to end:

```bash
# 1. install
uv sync --dev          # or: pip install -e ".[dev]"

# 2. run plain HTTP
uv run python -m http_server --host 127.0.0.1 --port 8080

# 3. in another shell
curl http://127.0.0.1:8080/
curl http://127.0.0.1:8080/users/42

# 4. run HTTPS (generate a dev cert first)
./scripts/generate_dev_certificate.sh  # -> cert.pem / key.pem
uv run python -m http_server --host 127.0.0.1 --port 8443 --tls --cert cert.pem --key key.pem
curl --insecure https://127.0.0.1:8443/
```

No accounts, no daemons, no external services — one process, one event loop, one thread.

## Highlights

- **Transport-agnostic parser.** `HttpParser` is a pure state machine (`REQUEST_LINE → HEADERS → BODY → DONE`) that only sees `bytes` via `feed()`. Limits (`max_request_line_size`, `max_headers_size`, `max_header_count`, `max_body_size`) are enforced *while* bytes accumulate, not after.
- **Fail-closed framing.** Duplicate `Content-Length` with different values, `Transfer-Encoding: chunked` → `501`, `Content-Length > limit` → `413` without waiting for the body. Ambiguous framing is never guessed.
- **Correct keep-alive & pipelining bytes.** `Connection` owns input/output buffers, `last_activity`, and `should_close`. HTTP/1.1 defaults to keep-alive, HTTP/1.0 to close; `Connection: close/keep-alive` overrides. Pipelined bytes after `DONE` are preserved in `parser.get_remaining()` for the next request (pipelining itself is not executed).
- **Level-triggered selector done right.** `EVENT_WRITE` is registered only when `output` is non-empty and removed after drain — no busy wakeups. `EVENT_READ` tracks request line/headers/body, next keep-alive request, and TLS handshake wants.
- **TLS without magic.** `TLSTransport` wraps `ssl.SSLContext` in non-blocking mode; `SSLWantReadError/WriteError` are translated to `want_read/want_write` in `tls_connection.py`. No custom crypto.
- **Minimal routing.** Exact + single-segment `{param}` (`fullmatch` `[A-Za-z_][A-Za-z0-9_]*`), two-stage `404` vs `405` with sorted `Allow`. `HEAD` returns same headers as `GET` with `send_body=False`; `OPTIONS` may list allowed methods.
- **Typed end to end.** `ServerConfig` (`dataclass`, frozen), case-insensitive `Headers`, `Request`/`Response` with explicit `serialize(http_version, send_body)`, `mypy --strict` clean, `ruff` clean.

## Architecture

A deliberately explicit data flow:

```text
TCP bytes → Transport (Plain/TLS) → Connection (state machine) → HttpParser → Request → Router → Handler/Middleware → Response → Serializer → Transport → TCP bytes
```

The table below is the default `ServerConfig`.

| Component | Value | Notes |
|---|---|---|
| `max_request_line_size` | 8192 | bytes, checked during accumulation |
| `max_headers_size` | 32768 | bytes, total headers block |
| `max_header_count` | 100 | headers |
| `max_body_size` | 1048576 | bytes (1 MiB), `Content-Length` validated before reading body |
| `request_timeout` | 5.0 | seconds to receive full request |
| `keep_alive_timeout` | 5.0 | idle keep-alive seconds |
| `host` / `port` | 127.0.0.1 / 8080 | `port=0` random for tests |
| `EVENT_WRITE` | dynamic | only when `output` non-empty |
| `TLS` | `ssl.SSLContext` | non-blocking, WantRead/Write → selector |

**Connection state machine (plain TCP):**

```text
ACCEPTED → READING → PROCESSING → WRITING → KEEP_ALIVE → READING
                          ↓
                       CLOSING → CLOSED
TLS inserts HANDSHAKING after ACCEPTED.
```

Full spec, invariants, and diagrams live in [ARCHITECTURE.md](ARCHITECTURE.md) (1820 lines, 35 sections). Key: §2 parsing, §14-17 connection/keep-alive/timeouts, §31 invariants. §33 lists open questions.

## Project structure

```text
http-server-from-scratch/
├── pyproject.toml          # deps + console entry point
├── config.py               # ServerConfig (limits, timeouts)
├── src/http_server/
│   ├── cli.py              # argparse: --host/--port/--tls/--cert/--key/--timeouts/--limits
│   ├── server.py           # bind/listen/accept → EventLoop
│   ├── connection.py       # Connection state machine, keep-alive, leftover
│   ├── event_loop.py       # selectors.DefaultSelector + O(n) timeout
│   ├── tls_connection.py   # non-blocking TLS handshake
│   ├── transport/
│   │   ├── base.py         # Transport ABC
│   │   ├── plain.py        # PlainTransport (non-blocking socket)
│   │   └── tls.py          # TLSTransport (ssl.SSLContext)
│   ├── http/
│   │   ├── parser.py       # incremental parser
│   │   ├── request.py      # Request (method/target/path/query/headers/body + extensible fields)
│   │   ├── response.py     # Response serializer (Content-Length, HEAD, 204/304/1xx, injection)
│   │   ├── headers.py      # case-insensitive Headers
│   │   └── errors.py       # 400/404/405/413/414/431/500/501/505
│   ├── routing/
│   │   ├── route.py        # Route pattern {param}
│   │   └── router.py       # two-stage 404/405 + Allow
│   └── middleware/
│       ├── base.py         # chain_middleware
│       └── logging.py      # LoggingMiddleware (http_server.access)
├── tests/
│   ├── unit/               # parser/headers/request/response/router/transport/connection/tls (146)
│   └── integration/        # real TCP/TLS sockets, keep-alive, timeouts (25)
├── examples/
│   ├── basic_server.py     # plain HTTP on 8080
│   └── tls_server.py       # HTTPS on 8443 (needs cert.pem/key.pem)
└── scripts/
    └── generate_dev_certificate.sh  # OpenSSL wrapper
```

Dependency direction is strict: `CLI → Server → EventLoop → Connection → {Transport, Parser, Response} → Router/Middleware`.

## Requirements

- **Python** 3.12 or newer
- **OS**: Linux, macOS, Windows — `selectors.DefaultSelector` abstracts `epoll/kqueue/IOCP`
- No dependencies outside stdlib (TLS integration tests generate certs via `cryptography` in `tmp_path`)

## Installation

```bash
git clone https://github.com/ZuroKing/http-server-from-scratch.git
cd http-server-from-scratch
uv sync --dev
```

or with pip:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

This registers the `http_server` console command and pulls in dev tools (`pytest`, `mypy`, `ruff`).

## Configuration

All settings live in [`src/http_server/config.py`](src/http_server/config.py) as a frozen `dataclass`. Edit the defaults there or pass CLI flags.

### `ServerConfig`

| Option | Default | Description |
|---|---|---|
| `host` | `127.0.0.1` | bind address |
| `port` | `8080` | bind port (`0` random for tests) |
| `max_request_line_size` | `8192` | bytes |
| `max_headers_size` | `32768` | bytes |
| `max_header_count` | `100` | headers |
| `max_body_size` | `1048576` | bytes (1 MiB) |
| `request_timeout` | `5.0` | seconds to receive full request |
| `keep_alive_timeout` | `5.0` | idle keep-alive seconds |

CLI mirrors the same names:

```bash
python -m http_server --host 127.0.0.1 --port 8080 --request-timeout 10 --max-body-size 2097152
python -m http_server --host 127.0.0.1 --port 8443 --tls --cert cert.pem --key key.pem
```

## Running the server

### As a library

```python
from http_server.config import ServerConfig
from http_server.routing.router import Router
from http_server.http.response import Response
from http_server.server import Server

router = Router()
router.add("GET", "/", lambda req: Response(status_code=200, body=b"Hello"))
router.add("GET", "/users/{id}", lambda req: Response(status_code=200, body=f"hi {req.route_params['id']}".encode()))

config = ServerConfig(host="127.0.0.1", port=8080)
server = Server(host="127.0.0.1", port=8080, router=router, config=config)
server.start()  # blocks; for tests use server.start_in_thread()
```

### CLI

```bash
python -m http_server --help
python -m http_server --host 127.0.0.1 --port 8080
python -m http_server --host 127.0.0.1 --port 8443 --tls --cert cert.pem --key key.pem --keep-alive-timeout 10
```

## Routing

```python
router.add("GET", "/", home)
router.add("GET", "/users/{id}", user)   # /users/42 → {"id": "42"}
router.add("POST", "/users", create_user)
```

- `path` matches → handler
- `path` matches but `method` doesn't → `405 Method Not Allowed` + `Allow: GET, POST` (sorted)
- Nothing matches → `404 Not Found`

`HEAD` is served as `GET` with `send_body=False` (same `Content-Length`, no body); `OPTIONS` may list allowed methods.

## Security model & limitations

Full threat model: [ARCHITECTURE.md](ARCHITECTURE.md) §18-19, §31 and [SECURITY.md](SECURITY.md).

**Protected against (by design):** unbounded memory growth (limits during accumulation), oversized request line/headers/body, excessive header count, invalid/conflicting `Content-Length`, `chunked` → `501`, malformed request line/headers/version, `CRLF` header injection → `ValueError`, ambiguous framing (fail-closed), leftover bytes preserved, timeouts (slowloris).

**Explicitly not a security boundary:** no auth, no rate limiting beyond timeouts, no protection against an attacker who already controls the machine, no HTTP/2. For production, front with a hardened reverse proxy and never commit `cert.pem`/`key.pem` (blocked by `.gitignore`).

## Testing

```bash
uv run pytest -v                 # 171 tests (146 unit + 25 integration), ~7 s
uv run pytest tests/unit -v
uv run pytest tests/integration -v  # real sockets, port=0, real TLS via tmp_path certs
```

Unit tests cover the failure modes that matter: malformed line, oversized line/headers/body, `Content-Length` invalid/conflicting, `chunked`, version, `CRLF` injection, `HEAD`/`204`/`304`/`1xx`, routing `404`/`405`/`Allow`, `keep-alive` vs `close`, `EVENT_WRITE` dynamics, `SSLWantRead/Write`. Integration uses `socket.socketpair()` / `Server.start_in_thread()` and generates self-signed certs via `cryptography`.

## Development

Common tasks are wrapped in `pyproject.toml`:

```bash
uv sync --dev
uv run pytest -v
uv run mypy --strict src/
uv run ruff check src/
```

The codebase is fully typed. When touching `parser.py`, `connection.py`, `event_loop.py`, `server.py`, `transport/`, `tls_connection.py`, `routing/`, read the relevant `ARCHITECTURE.md` section first — several invariants there (`parser` transport-agnostic, `fail-closed`, `EVENT_WRITE` only when needed, `TLS` only via `SSLContext`) are enforced by tests and easy to break invisibly. See [CLAUDE.md](CLAUDE.md) for the full working conventions.

## Performance notes

On the development machine (Windows 11, Python 3.12, i7), `pytest` finishes in ~7 s for 171 tests, including real TCP/TLS integration. The server is single-threaded and level-triggered — throughput is bounded by Python's loop, not by `select` itself. For keep-alive, `EVENT_WRITE` is only registered when `output` is non-empty, so idle connections cost one `select` wakeup per timeout check. TLS adds one extra `do_handshake` round-trip.

## Roadmap

Rough ordering, not promises:

- **Static file handler** with traversal protection (`GET /../../secret.txt` → 403) and `ARCHITECTURE_ru.md` bilingual spec.
- **Graceful shutdown** and final defaults tuning (`request_timeout` / `keep_alive_timeout`).
- **Extended middleware** (auth, timing) and `AccessLog` format finalization.
- **Longer limits** configurability via `ServerConfig` + CLI without breaking invariants.

## FAQ

**Why no Flask/FastAPI/asyncio?** Because they hide the event loop, parsing, and framing this project is meant to expose. The point is `recv() → buffer → state machine → router → serialize → send()` without a framework.

**Why `selectors` and not `epoll` directly?** Cross-platform abstraction over `epoll/kqueue/IOCP` while keeping the level-triggered model visible.

**Does it handle pipelining?** It preserves pipelined bytes (`GET /second` arriving in the same `recv` as the first request) but processes one request at a time — correct keep-alive without concurrent execution.

**Is it production-ready?** No. It's a portfolio project that passes its own limits and timeout tests, but lacks HTTP/2, robust logging, metrics, and hardening a production server needs. Front it with Nginx/Caddy.

**How do I make it faster?** Tune `max_*` limits and timeouts for your workload, and put it behind a reverse proxy that does caching/compression. The single-threaded loop is the bottleneck by design.

## Contributing

Contributions are welcome — see [ARCHITECTURE.md](ARCHITECTURE.md) for the design record and invariants. For anything large, open an issue first so we can agree on the approach.

## License

[MIT](LICENSE) © 2026 ZuroKing

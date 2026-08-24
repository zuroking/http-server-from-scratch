# CLAUDE.md

Guidance for Claude Code working in this repository.

## Project

`http-server-from-scratch` — учебный HTTP/1.1 сервер поверх raw TCP sockets (Python ≥ 3.12) без `Flask`, `FastAPI`, `Django`, `Werkzeug`, `http.server`, `asyncio`, сторонних HTTP server frameworks. Разрешён только `ssl.SSLContext` для TLS.

Ручная реализация: non-blocking `selectors.DefaultSelector` + event loop, incremental HTTP parser, response serializer, routing с `{param}`, `Connection` state machine, keep-alive, timeouts, `PlainTransport`/`TLSTransport`. Портфолио-проект #6. Источник истины — **ARCHITECTURE.md** (1820 строк, 35 разделов). Перед изменением `parser.py`, `connection.py`, `event_loop.py`, `server.py`, `transport/`, `tls_connection.py`, `routing/` читать §2, §14-17, §31. §33 — открытые вопросы, не блокирующие.

## Commands

```bash
# install
uv sync --dev                      # или pip install -e ".[dev]"
# tests — всегда реальный вывод, не резюме
uv run pytest -v                   # 171 passed (146 unit + 25 integration) — baseline
uv run pytest tests/unit -v        # только unit
uv run pytest tests/integration -v # реальные TCP/TLS сокеты
# single file
uv run pytest tests/unit/test_parser.py -v
# type/lint — должны оставаться чистыми
uv run mypy --strict src/
uv run ruff check src/
# run server
uv run python -m http_server --host 127.0.0.1 --port 8080
uv run python -m http_server --host 127.0.0.1 --port 8443 --tls --cert cert.pem --key key.pem
# manual checks
curl --insecure https://127.0.0.1:8443/
openssl s_client -connect 127.0.0.1:8443 -quiet
```

`pyproject.toml` — `hatchling`, `pythonpath = ["src"]`, `testpaths = ["tests"]`. Зависимостей вне stdlib нет.

## Non-negotiable architectural invariants

Нарушение любого из них — баг, даже если тесты зелёные (ARCHITECTURE.md §31):

- **Парсер transport-agnostic**: `http/parser.py` принимает только `bytes` через `feed()`, никогда не вызывает `socket.recv()`, не знает о `selector`/`TLS`. Состояния `REQUEST_LINE→HEADERS→BODY→DONE` (`Enum`), лимиты (`max_request_line_size`, `max_headers_size`, `max_header_count`, `max_body_size`) проверяются **во время накопления** буфера, не после.
- **Fail-closed**: неоднозначный `Content-Length` (дубликаты, `Transfer-Encoding: chunked` → `501`), `Content-Length > max_body_size` → `413` немедленно, без ожидания тела.
- **Leftover preservation**: после `DONE` остаток буфера (pipelined `GET /second …`) сохраняется в `parser.get_remaining()` — `Connection` переносит его в новый парсер. Pipelining не исполняется, но байты не теряются.
- **Запрещённые фреймворки**: `Flask`, `FastAPI`, `http.server`, `asyncio`, `epoll/kqueue` напрямую — запрещены. Только `selectors.DefaultSelector`.
- **EVENT_WRITE только при необходимости**: `Connection.want_write()` → `selector.modify(EVENT_WRITE)` только когда `output` не пуст; после дрэйна снимается.
- **TLS только через `ssl.SSLContext`**: своя криптография запрещена; `SSLWantReadError/WriteError` транслируются в `want_read/want_write` в `tls_connection.py`, не попадают в `parser`/`router`.
- **Router двухэтапный**: `path` match → `404`, `path` найден но `method` нет → `405` + `Allow: GET, ...` (отсортирован). Параметры `{id}` — один сегмент, `fullmatch` `[A-Za-z_][A-Za-z0-9_]*`.
- **Response serializer** (аналогично парсеру, без `socket`) добавляет `Content-Length` по `len(body)` (байты, не символы), `HEAD` → `send_body=False` сохраняет `Content-Length`, `204/304/1xx` → `Content-Length: 0` и пустое тело, CRLF-инъекции (`\r`/`\n` в header/reason/version) → `ValueError`.
- **Зависимости направлены**: `CLI → Server → EventLoop → Connection → {Transport, Parser, Response} → Router/Middleware`. Handler никогда не управляет сокетом.

## Repository hygiene

- `.gitignore` блокирует `cert.pem`, `key.pem`, `*.pem`, `*.enc`, `__pycache__/`, `.venv/`, `.pytest_cache/`, `.mypy_cache/`, `.benchmarks/`, `*.enc.*`. Никогда не коммитить реальные сертификаты, временные файлы с секретами.
- Тесты используют `tmp_path` и `port=0` (random), никогда реальные пути `~/.secure_vault`.
- Не удалять `*_example.md` вручную — они удаляются владельцем после генерации.

## Testing protocol

- Только реальный `pytest -v` вывод — не фабриковать.
- Парсер тестируется без сокетов (`parser.feed(bytes)`), интеграция — реальные `socket.socketpair()` / `Server.start_in_thread()` + `port=0`.
- TLS тесты генерируют self-signed cert на лету через `cryptography` в `tmp_path` (см. `tests/unit/test_tls.py`).
- Каждый failure mode из ARCHITECTURE.md §19 имеет регрессию (malformed line, oversized line/headers/body, invalid/conflicting Content-Length, chunked, version, timeout).
- После изменения `parser.py`/`connection.py`/`tls_connection.py` обязателен `pytest -v` и security review по §30.

## Documentation conventions

- Билингвальные пары: `README ↔ README_RU`, `ELI5 ↔ ELI5_ru`, `ARCHITECTURE ↔ ARCHITECTURE_ru` (пока только `ARCHITECTURE.md`), `SECURITY ↔ SECURITY_ru`. Меняешь смысл в одном — зеркаль в другом.
- `ARCHITECTURE.md` — источник правки; §14 фиксирует отклонения spec vs implementation (сейчас пусто) — держать актуальным.
- Новые spec-изменения — новый раздел/тэг, не тихая перезапись истории.

## Environment notes

- Dev-машина — Windows (PowerShell 5.1), код кросс-платформенный: `selector` абстрагирует `epoll/kqueue`. Не использовать `Set-Content`/`Get-Content` для UTF-8 Cyrillic — портит кодировку, использовать file-editing инструменты.
- Порт `0` в `ServerConfig` разрешён только для тестов (random), `CLI` валидирует `1..65535`.
- Логирование — `logging` stdlib, `http_server.access` для `LoggingMiddleware`, чувствительность headers/body не логируется.

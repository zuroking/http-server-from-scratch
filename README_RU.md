# http-server-from-scratch

![Python](https://img.shields.io/badge/python-3.12+-blue)
![Tests](https://img.shields.io/badge/tests-171%20passing-brightgreen)
![Type check](https://img.shields.io/badge/mypy-strict-blueviolet)
![License](https://img.shields.io/badge/license-MIT-green)

**Языки:** [English](README.md) · **Русский**

Учебный HTTP/1.1 сервер с нуля на raw TCP сокетах — `selectors.DefaultSelector`, ручной инкрементальный парсер, сериализатор ответов, стейт-машина и `ssl.SSLContext` для TLS. Поставляется с keep-alive, таймаутами, роутингом с `{param}` и реальным event loop, без `Flask`, `FastAPI`, `http.server` и `asyncio`. Весь стек — TCP, HTTP, роутинг, TLS — написан вручную.

> **Что это такое (и чем не является).** Сервер делает **ровно HTTP/1.1** (и минимальную совместимость с HTTP/1.0). Работает с `Content-Length` телами, а не с chunked телами запросов. Держит соединения alive и сохраняет pipelined байты, но не делает HTTP/2, WebSockets, сжатие, кеширование или проксирование — всё это вне скоупа. См. [Модель безопасности и ограничения](#модель-безопасности-и-ограничения) и [Архитектуру](#архитектура).

---

## Оглавление

- [Быстрый старт](#быстрый-старт)
- [Ключевые особенности](#ключевые-особенности)
- [Архитектура](#архитектура)
- [Структура проекта](#структура-проекта)
- [Требования](#требования)
- [Установка](#установка)
- [Конфигурация](#конфигурация)
- [Запуск сервера](#запуск-сервера)
- [Роутинг](#роутинг)
- [Модель безопасности и ограничения](#модель-безопасности-и-ограничения)
- [Тестирование](#тестирование)
- [Разработка](#разработка)
- [Заметки о производительности](#заметки-о-производительности)
- [Дорожная карта](#дорожная-карта)
- [FAQ](#faq)
- [Участие](#участие)
- [Лицензия](#лицензия)

## Быстрый старт

Если хочется просто посмотреть как это работает — весь цикл от начала до конца:

```bash
# 1. установка
uv sync --dev          # или: pip install -e ".[dev]"

# 2. запуск plain HTTP
uv run python -m http_server --host 127.0.0.1 --port 8080

# 3. в другом терминале
curl http://127.0.0.1:8080/
curl http://127.0.0.1:8080/users/42

# 4. запуск HTTPS (сначала сгенерировать dev-сертификат)
./scripts/generate_dev_certificate.sh  # -> cert.pem / key.pem
uv run python -m http_server --host 127.0.0.1 --port 8443 --tls --cert cert.pem --key key.pem
curl --insecure https://127.0.0.1:8443/
```

Никаких аккаунтов, демонов и внешних сервисов — один процесс, один event loop, один поток.

## Ключевые особенности

- **Транспортно-независимый парсер.** `HttpParser` — чистая стейт-машина (`REQUEST_LINE → HEADERS → BODY → DONE`), видит только `bytes` через `feed()`. Лимиты (`max_request_line_size`, `max_headers_size`, `max_header_count`, `max_body_size`) проверяются *во время* накопления, а не после.
- **Fail-closed фрейминг.** Дубли `Content-Length` с разными значениями, `Transfer-Encoding: chunked` → `501`, `Content-Length > limit` → `413` без ожидания тела. Неоднозначность никогда не угадывается.
- **Корректный keep-alive и байты pipelining.** `Connection` владеет input/output буферами, `last_activity` и `should_close`. HTTP/1.1 по умолчанию keep-alive, HTTP/1.0 — close; `Connection: close/keep-alive` переопределяет. Байты pipelined после `DONE` сохраняются в `parser.get_remaining()` для следующего запроса (сам pipelining не исполняется).
- **Level-triggered selector правильно.** `EVENT_WRITE` регистрируется только когда `output` не пуст и снимается после дрэйна — без лишних пробуждений. `EVENT_READ` отслеживает request line/headers/body, следующий keep-alive запрос и TLS handshake wants.
- **TLS без магии.** `TLSTransport` оборачивает `ssl.SSLContext` в non-blocking режиме; `SSLWantReadError/WriteError` транслируются в `want_read/want_write` в `tls_connection.py`. Своей криптографии нет.
- **Минимальный роутер.** Точное совпадение + односегментный `{param}` (`fullmatch` `[A-Za-z_][A-Za-z0-9_]*`), двухэтапный `404` vs `405` с сортированным `Allow`. `HEAD` отдаёт те же заголовки, что и `GET` с `send_body=False`; `OPTIONS` может перечислить разрешённые методы.
- **Типизация насквозь.** `ServerConfig` (`dataclass`, frozen), case-insensitive `Headers`, `Request`/`Response` с явным `serialize(http_version, send_body)`, `mypy --strict` чист, `ruff` чист.

## Архитектура

Заведомо явный поток данных:

```text
TCP bytes → Transport (Plain/TLS) → Connection (state machine) → HttpParser → Request → Router → Handler/Middleware → Response → Serializer → Transport → TCP bytes
```

Таблица — дефолтный `ServerConfig`.

| Компонент | Значение | Примечание |
|---|---|---|
| `max_request_line_size` | 8192 | байт, проверяется во время накопления |
| `max_headers_size` | 32768 | байт, общий блок заголовков |
| `max_header_count` | 100 | заголовков |
| `max_body_size` | 1048576 | байт (1 MiB), `Content-Length` валидируется до чтения тела |
| `request_timeout` | 5.0 | секунд на получение полного запроса |
| `keep_alive_timeout` | 5.0 | секунд idle keep-alive |
| `host` / `port` | 127.0.0.1 / 8080 | `port=0` — random для тестов |
| `EVENT_WRITE` | динамический | только когда `output` не пуст |
| `TLS` | `ssl.SSLContext` | неблокирующий, WantRead/Write → selector |

**Стейт-машина Connection (plain TCP):**

```text
ACCEPTED → READING → PROCESSING → WRITING → KEEP_ALIVE → READING
                          ↓
                       CLOSING → CLOSED
TLS вставляет HANDSHAKING после ACCEPTED.
```

Полная спека, инварианты и диаграммы — в [ARCHITECTURE.md](ARCHITECTURE.md) (1820 строк, 35 разделов). Ключевые: §2 парсинг, §14-17 connection/keep-alive/timeouts, §31 инварианты. §33 — открытые вопросы.

## Структура проекта

```text
http-server-from-scratch/
├── pyproject.toml          # зависимости + консольная точка входа
├── config.py               # ServerConfig (лимиты, таймауты)
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
│   │   ├── parser.py       # инкрементальный парсер
│   │   ├── request.py      # Request (method/target/path/query/headers/body + расширяемые поля)
│   │   ├── response.py     # Response serializer (Content-Length, HEAD, 204/304/1xx, инъекции)
│   │   ├── headers.py      # case-insensitive Headers
│   │   └── errors.py       # 400/404/405/413/414/431/500/501/505
│   ├── routing/
│   │   ├── route.py        # Route паттерн {param}
│   │   └── router.py       # двухэтапный 404/405 + Allow
│   └── middleware/
│       ├── base.py         # chain_middleware
│       └── logging.py      # LoggingMiddleware (http_server.access)
├── tests/
│   ├── unit/               # parser/headers/request/response/router/transport/connection/tls (146)
│   └── integration/        # реальные TCP/TLS сокеты, keep-alive, таймауты (25)
├── examples/
│   ├── basic_server.py     # plain HTTP на 8080
│   └── tls_server.py       # HTTPS на 8443 (нужен cert.pem/key.pem)
└── scripts/
    └── generate_dev_certificate.sh  # OpenSSL wrapper
```

Направление зависимостей строгое: `CLI → Server → EventLoop → Connection → {Transport, Parser, Response} → Router/Middleware`.

## Требования

- **Python** 3.12 или новее
- **ОС**: Linux, macOS, Windows — `selectors.DefaultSelector` абстрагирует `epoll/kqueue/IOCP`
- Зависимостей вне stdlib нет (TLS-интеграция генерирует сертификаты через `cryptography` в `tmp_path`)

## Установка

```bash
git clone https://github.com/ZuroKing/http-server-from-scratch.git
cd http-server-from-scratch
uv sync --dev
```

или через pip:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Регистрирует консольную команду `http_server` и подтягивает dev-инструменты (`pytest`, `mypy`, `ruff`).

## Конфигурация

Все настройки живут в [`src/http_server/config.py`](src/http_server/config.py) как замороженный `dataclass`. Правьте дефолты там или передавайте CLI-флаги.

### `ServerConfig`

| Параметр | Дефолт | Описание |
|---|---|---|
| `host` | `127.0.0.1` | адрес bind |
| `port` | `8080` | порт bind (`0` — random для тестов) |
| `max_request_line_size` | `8192` | байт |
| `max_headers_size` | `32768` | байт |
| `max_header_count` | `100` | заголовков |
| `max_body_size` | `1048576` | байт (1 MiB) |
| `request_timeout` | `5.0` | секунд на получение полного запроса |
| `keep_alive_timeout` | `5.0` | секунд idle keep-alive |

CLI дублирует те же имена:

```bash
python -m http_server --host 127.0.0.1 --port 8080 --request-timeout 10 --max-body-size 2097152
python -m http_server --host 127.0.0.1 --port 8443 --tls --cert cert.pem --key key.pem
```

## Запуск сервера

### Как библиотека

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
server.start()  # блокирует; для тестов server.start_in_thread()
```

### CLI

```bash
python -m http_server --help
python -m http_server --host 127.0.0.1 --port 8080
python -m http_server --host 127.0.0.1 --port 8443 --tls --cert cert.pem --key key.pem --keep-alive-timeout 10
```

## Роутинг

```python
router.add("GET", "/", home)
router.add("GET", "/users/{id}", user)   # /users/42 → {"id": "42"}
router.add("POST", "/users", create_user)
```

- Совпал `path` → хэндлер
- Совпал `path`, но не `method` → `405 Method Not Allowed` + `Allow: GET, POST` (отсортирован)
- Ничего не совпало → `404 Not Found`

`HEAD` отдаётся как `GET` с `send_body=False` (тот же `Content-Length`, без тела); `OPTIONS` может перечислить разрешённые методы.

## Модель безопасности и ограничения

Полная threat model: [ARCHITECTURE.md](ARCHITECTURE.md) §18-19, §31 и [SECURITY_ru.md](SECURITY_ru.md).

**Защита по дизайну:** неограниченный рост памяти (лимиты во время накопления), oversized line/headers/body, excessive header count, invalid/conflicting `Content-Length`, `chunked` → `501`, malformed line/headers/version, `CRLF` инъекция → `ValueError`, неоднозначный фрейминг (fail-closed), сохранение leftover, таймауты (slowloris).

**Явно не граница безопасности:** нет auth, нет rate limiting кроме таймаутов, нет защиты от атакующего, уже контролирующего машину, нет HTTP/2. Для продакшена — ставить за hardened reverse proxy и никогда не коммитить `cert.pem`/`key.pem` (заблокированы `.gitignore`).

## Тестирование

```bash
uv run pytest -v                 # 171 тест (146 unit + 25 integration), ~7 с
uv run pytest tests/unit -v
uv run pytest tests/integration -v  # реальные сокеты, port=0, реальные TLS через tmp_path
```

Юнит-тесты покрывают критичные failure mode: malformed line, oversized line/headers/body, `Content-Length` invalid/conflicting, `chunked`, version, `CRLF` инъекция, `HEAD`/`204`/`304`/`1xx`, роутинг `404`/`405`/`Allow`, `keep-alive` vs `close`, `EVENT_WRITE` динамика, `SSLWantRead/Write`. Интеграция — `socket.socketpair()` / `Server.start_in_thread()` и self-signed cert через `cryptography`.

## Разработка

Общие задачи завернуты в `pyproject.toml`:

```bash
uv sync --dev
uv run pytest -v
uv run mypy --strict src/
uv run ruff check src/
```

Код полностью типизирован. При правке `parser.py`, `connection.py`, `event_loop.py`, `server.py`, `transport/`, `tls_connection.py`, `routing/` читайте соответствующий раздел `ARCHITECTURE.md` — несколько инвариантов там (`parser` transport-agnostic, `fail-closed`, `EVENT_WRITE` только когда нужно, `TLS` только через `SSLContext`) enforced тестами и легко ломаются незаметно. См. [CLAUDE.md](CLAUDE.md).

## Заметки о производительности

На dev-машине (Windows 11, Python 3.12, i7) `pytest` проходит ~7 с на 171 тесте, включая реальные TCP/TLS интеграции. Сервер однопоточный и level-triggered — пропускная способность ограничена питоновским циклом, а не `select`. Для keep-alive `EVENT_WRITE` регистрируется только когда `output` не пуст, поэтому idle соединения стоят один `select` wakeup на проверку таймаута. TLS добавляет один лишний `do_handshake` round-trip.

## Дорожная карта

Примерный порядок, не обещания:

- **Static file handler** с защитой от traversal (`GET /../../secret.txt` → 403) и `ARCHITECTURE_ru.md` билингва.
- **Graceful shutdown** и финальная полировка дефолтов (`request_timeout` / `keep_alive_timeout`).
- **Расширенный middleware** (auth, timing) и финализация формата `AccessLog`.
- **Длинные лимиты** конфигурируемые через `ServerConfig` + CLI без поломки инвариантов.

## FAQ

**Почему без Flask/FastAPI/asyncio?** Потому что они прячут event loop, парсинг и фрейминг, которые этот проект и должен показать. Смысл — `recv() → buffer → state machine → router → serialize → send()` без фреймворка.

**Почему `selectors`, а не `epoll` напрямую?** Кроссплатформенная абстракция над `epoll/kqueue/IOCP` с сохранением level-triggered модели.

**Обрабатывается ли pipelining?** Сохраняются pipelined байты (`GET /second` в том же `recv`, что и первый запрос), но исполняется один запрос за раз — корректный keep-alive без конкуренции.

**Готов ли к продакшену?** Нет. Это портфолио-проект, проходящий свои лимиты и таймауты, но без HTTP/2, метрик и харденинга.

**Как сделать быстрее?** Настроить `max_*` лимиты и таймауты под нагрузку и поставить перед ним reverse proxy с кешем/сжатием. Однопоточный цикл — bottleneck по дизайну.

## Участие

Контрибьюции приветствуются — см. [ARCHITECTURE.md](ARCHITECTURE.md) как design record и инварианты. Для крупных изменений сначала откройте issue.

## Лицензия

[MIT](LICENSE) © 2026 ZuroKing

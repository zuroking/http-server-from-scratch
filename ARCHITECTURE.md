# ARCHITECTURE.md — http-server-from-scratch

## 0. Цель проекта

`http-server-from-scratch` — учебный HTTP-сервер, реализованный поверх raw TCP sockets без использования Flask, FastAPI, `http.server` и `asyncio`.

Основная цель проекта — получить практическое понимание внутреннего устройства веб-сервера:

- TCP socket lifecycle;
- non-blocking I/O;
- event-driven architecture;
- ручной HTTP parsing;
- HTTP response serialization;
- routing;
- keep-alive;
- connection state machines;
- timeouts;
- TLS поверх raw sockets.

Проект является портфолио-проектом #6.

Главный принцип:

```text
TCP bytes
    ↓
HTTP parser
    ↓
HTTP request
    ↓
Router
    ↓
Handler / Middleware
    ↓
HTTP response
    ↓
HTTP serializer
    ↓
TCP bytes
```

Приоритеты проекта:

1. корректность протокола;
2. безопасность и предсказуемость;
3. понятная архитектура;
4. тестируемость;
5. только после этого — производительность.

---

# 1. Модель конкурентности

## 1.1. Выбор

Используется:

```python
selectors.DefaultSelector
```

и non-blocking sockets.

Сервер работает в одном основном event loop и одном потоке.

### Отклонённые альтернативы

#### Thread-per-connection

Не используется.

Причина: модель скрывает устройство event-driven I/O и добавляет ненужную сложность с синхронизацией состояния.

#### `asyncio`

Не используется.

Причина: `asyncio` скрывает большую часть механики non-blocking I/O, которую данный проект предназначен продемонстрировать.

#### Прямой `epoll` / `kqueue`

Не используется в основной реализации.

Причина: `selectors.DefaultSelector` предоставляет подходящую кроссплатформенную абстракцию, сохраняя event-driven модель.

---

## 1.2. Правила регистрации событий

### `EVENT_READ`

`EVENT_READ` регистрируется, когда соединение способно принимать данные:

- request line;
- headers;
- request body;
- следующий request в keep-alive connection;
- TLS handshake, если transport требует чтения.

### `EVENT_WRITE`

`EVENT_WRITE` регистрируется **только** если существуют данные, которые нужно отправить, либо текущая TLS operation требует write readiness.

После полного опустошения write-buffer `EVENT_WRITE` снимается.

Нельзя постоянно держать `EVENT_WRITE` зарегистрированным.

Причина: level-triggered selector будет постоянно сообщать о готовности socket к записи, создавая бессмысленные пробуждения event loop.

Connection должен динамически изменять набор интересующих событий через:

```python
selector.modify(...)
```

---

# 2. HTTP Parser

## 2.1. Источник данных

HTTP parser получает raw bytes, поступающие через:

```python
socket.recv()
```

Данные читаются чанками.

Не используются:

- `socket.makefile()`;
- `http.server`;
- сторонние HTTP parsers.

---

## 2.2. Incremental parsing

Парсер должен корректно работать, когда один HTTP request разбит между несколькими `recv()`.

Например:

```text
recv #1:
GET /inde

recv #2:
x HTTP/1.1\r\nHost: localhost\r\n\r\n
```

Парсер обязан воспринимать это как один request.

Общий цикл:

```text
recv()
  ↓
append to input buffer
  ↓
check limits
  ↓
try parse
  ↓
need more data / complete / error
```

Лимиты проверяются **по мере накопления буфера**, а не после получения полного request.

---

## 2.3. Состояния parser

Parser использует явную state machine:

```text
REQUEST_LINE
    ↓
HEADERS
    ↓
BODY
    ↓
DONE
```

При ошибке:

```text
REQUEST_LINE / HEADERS / BODY
            ↓
          ERROR
```

Parser является чистым компонентом.

Он:

- принимает bytes;
- хранит своё состояние;
- формирует `Request`;
- сообщает о необходимости дополнительных данных;
- сообщает о protocol error.

Parser не:

- управляет socket;
- изменяет selector;
- вызывает handlers;
- занимается routing;
- знает о TCP/TLS transport.

---

# 3. Request Parsing

## 3.1. Request line

Поддерживается формат:

```text
METHOD SP TARGET SP HTTP/VERSION CRLF
```

Например:

```text
GET /hello HTTP/1.1
```

Из request line извлекаются:

- method;
- target;
- HTTP version.

Некорректный request line:

```text
400 Bad Request
```

Неподдерживаемая версия HTTP:

```text
505 HTTP Version Not Supported
```

---

## 3.2. Request target

Target разделяется на:

```text
/path?query=value
```

и преобразуется в:

```text
path = "/path"
query_string = "query=value"
```

Routing выполняется по `path`, а query string передаётся отдельно.

---

# 4. HTTP Headers

Headers разбираются вручную из raw bytes.

Пример:

```text
Host: example.com
Content-Length: 42
Connection: keep-alive
```

Header names обрабатываются без учёта регистра.

Parser должен обнаруживать:

- malformed header names;
- malformed header values;
- отсутствующий разделитель `:`;
- некорректный `Content-Length`.

---

## 4.1. Header limits

Настройки должны включать:

- максимальный размер request line;
- максимальный общий размер headers;
- максимальное количество headers.

Лимиты проверяются до неограниченного накопления данных.

При превышении:

```text
431 Request Header Fields Too Large
```

Если request target превышает отдельный лимит:

```text
414 URI Too Long
```

---

# 5. Request Body

Первоначальная версия поддерживает request body только через:

```text
Content-Length
```

Правила:

```text
отсутствует Content-Length
    → body = empty

Content-Length: 0
    → body = empty

Content-Length: N
    → ждать ровно N bytes
```

Если body превышает установленный лимит:

```text
413 Content Too Large
```

---

## 5.1. Invalid Content-Length

Некорректное значение:

```text
Content-Length: abc
```

→

```text
400 Bad Request
```

---

## 5.2. Ambiguous framing

Конфликтующие или неоднозначные значения `Content-Length` должны приводить к:

```text
400 Bad Request
```

Сервер не должен угадывать границы request body.

---

## 5.3. Chunked request bodies

```text
Transfer-Encoding: chunked
```

для request body не поддерживается в первой версии.

Сервер отвечает:

```text
501 Not Implemented
```

Он не должен трактовать chunked body как обычный `Content-Length` body.

---

# 6. Request Object

После успешного parsing создаётся структурированный объект:

```text
Request
├── method
├── target
├── path
├── query_string
├── http_version
├── headers
└── body
```

При необходимости request context также может содержать:

- client address;
- route parameters;
- metadata middleware.

---

# 7. Response Serializer

Response serializer является отдельным компонентом, симметричным parser.

Он преобразует структурированный response в raw bytes:

```text
HTTP/1.1 STATUS_CODE REASON\r\n
Header: value\r\n
Header: value\r\n
\r\n
BODY
```

Например:

```text
HTTP/1.1 200 OK\r\n
Content-Type: text/plain\r\n
Content-Length: 5\r\n
\r\n
Hello
```

Serializer:

- создаёт status line;
- сериализует headers;
- добавляет необходимые framing headers;
- сериализует body.

Serializer не знает о:

- socket;
- selector;
- TCP;
- TLS;
- connection state.

---

# 8. HTTP Methods

Первая версия поддерживает:

```text
GET
HEAD
POST
PUT
DELETE
OPTIONS
```

`HEAD` возвращает те же response headers, что и соответствующий `GET`, но без отправки response body.

`OPTIONS` может возвращать список методов, поддерживаемых найденным маршрутом.

Неизвестный или неподдерживаемый method:

```text
501 Not Implemented
```

---

# 9. Routing

## 9.1. Supported route types

Поддерживаются:

1. exact routes;
2. простые named path parameters.

Пример:

```python
router.add("GET", "/", home)
router.add("GET", "/users/{id}", user)
router.add("POST", "/users", create_user)
```

Запрос:

```text
/users/42
```

сопоставляется с:

```text
/users/{id}
```

и создаёт:

```python
{"id": "42"}
```

Полноценный regex-router не нужен для первой версии.

---

# 10. 404 vs 405

Router выполняет двухэтапный matching.

```text
request path
    ↓
найти все routes, совпадающие по path
    ↓
нет совпадений
    ↓
404 Not Found
```

Если path совпал:

```text
найдены routes
    ↓
проверить method
    ↓
method найден
    ↓
handler
```

Если path найден, но method отсутствует:

```text
405 Method Not Allowed
```

При `405` сервер обязан отправить:

```text
Allow: GET, HEAD, OPTIONS
```

где перечисляются разрешённые методы всех маршрутов, совпавших по path.

---

# 11. Middleware

Middleware реализуется как HTTP-level chain.

Middleware может:

- проверять request;
- изменять request context;
- изменять response;
- выполнять logging;
- выполнять timing;
- обрабатывать ошибки;
- выполнять простую authentication/authorization logic.

Middleware не имеет доступа к:

- socket lifecycle;
- selector;
- transport internals.

---

# 12. Transport Abstraction

HTTP-слой не должен знать, работает ли соединение поверх обычного TCP или TLS.

Архитектура:

```text
Event Loop
    ↓
Connection
    ├── Transport
    │     ├── PlainSocketTransport
    │     └── TLSTransport
    │
    ├── HTTP Parser
    └── HTTP Response Serializer
```

`Transport` отвечает за low-level I/O.

HTTP parser и response serializer не взаимодействуют с transport напрямую.

---

## 12.1. PlainSocketTransport

Для обычного HTTP используется non-blocking TCP socket.

Основные операции:

```text
recv
send
close
```

Transport сообщает connection layer о:

- bytes received;
- bytes sent;
- connection closed;
- temporary write/read condition.

---

# 13. TLS

TLS реализуется через Python standard library:

```python
ssl.SSLContext
```

Собственная криптографическая или TLS-реализация запрещена.

---

## 13.1. TLS architecture

```text
TCP socket
    ↓
SSLContext
    ↓
TLSTransport
    ↓
Connection
    ↓
HTTP layer
```

HTTP code не должен содержать SSL-specific logic.

---

## 13.2. Non-blocking TLS

TLS socket работает в non-blocking mode.

Следующие операции могут вызвать:

```python
ssl.SSLWantReadError
ssl.SSLWantWriteError
```

Это относится к:

- `do_handshake()`;
- `recv()`;
- `send()`.

Например:

```text
send()
  ↓
SSLWantReadError
  ↓
register EVENT_READ
  ↓
retry send()
```

или:

```text
recv()
  ↓
SSLWantWriteError
  ↓
register EVENT_WRITE
  ↓
retry recv()
```

Эти исключения не должны распространяться в HTTP parser/router.

Их обрабатывает TLS transport / connection layer.

---

# 14. Connection State Machine

Каждое клиентское соединение имеет собственное состояние.

Основная state machine:

```text
ACCEPTED
    ↓
HANDSHAKING
    ↓
READING
    ↓
PROCESSING
    ↓
WRITING
    ↓
KEEP_ALIVE
    ↓
READING
```

Для plain TCP:

```text
ACCEPTED
    ↓
READING
```

TLS connections проходят `HANDSHAKING`.

При необходимости:

```text
ANY STATE
    ↓
CLOSING
    ↓
CLOSED
```

---

## 14.1. Connection responsibilities

`Connection` отвечает за:

- socket/transport;
- input buffer;
- output buffer;
- parser state;
- текущий request;
- текущий response;
- keep-alive state;
- last activity;
- connection close decision.

`Connection` не отвечает за:

- глобальный event loop;
- route registration;
- application business logic;
- TLS cryptography.

---

# 15. Keep-Alive

## 15.1. HTTP/1.1

Для HTTP/1.1:

```text
без Connection header
    → keep-alive

Connection: keep-alive
    → keep-alive

Connection: close
    → close после response
```

---

## 15.2. HTTP/1.0

Для HTTP/1.0:

```text
без Connection header
    → close

Connection: keep-alive
    → persistent connection
```

---

## 15.3. Invalid requests

Если request malformed или framing неоднозначен, сервер не должен сохранять соединение только ради keep-alive.

Protocol errors должны приводить к безопасному закрытию connection после отправки error response, когда это возможно.

---

# 16. HTTP Pipelining

HTTP/1.1 persistent connections поддерживаются.

HTTP request pipelining в первой версии **не поддерживается**.

На одной connection одновременно обрабатывается только один request:

```text
request
    ↓
response
    ↓
next request
```

Если несколько requests пришли одним `recv()`:

```text
GET /1 HTTP/1.1
Host: localhost

GET /2 HTTP/1.1
Host: localhost
```

parser и connection обязаны сохранить непрочитанные bytes в input buffer.

Следующий request не должен быть потерян.

Это необходимо для корректной работы keep-alive даже без поддержки pipelining.

---

# 17. Timeouts

Используется простой O(n) алгоритм.

Не используются:

- отдельные timer threads;
- отдельные threads на connection;
- min-heap deadline scheduler.

Event loop:

```text
calculate selector timeout
        ↓
selector.select(timeout)
        ↓
process events
        ↓
check last_activity for active connections
        ↓
close expired connections
        ↓
next iteration
```

---

## 17.1. Timeout types

Поддерживаются:

- request/header timeout;
- keep-alive idle timeout;
- при необходимости write timeout.

Значения конфигурируются через `config.py` и CLI.

Например:

```text
keep_alive_timeout = 5 seconds
```

---

# 18. Security Limits

Первая версия обязана иметь configurable limits для:

```text
max_request_line_size
max_headers_size
max_header_count
max_body_size
request_timeout
keep_alive_timeout
```

Проверки выполняются во время parsing.

Цель:

- предотвращение unbounded memory usage;
- защита от oversized requests;
- базовая защита от slowloris;
- предсказуемое поведение при malformed input.

---

# 19. Security Rules

Сервер должен корректно обрабатывать:

- malformed request line;
- malformed headers;
- oversized request line;
- oversized headers;
- excessive header count;
- oversized body;
- invalid `Content-Length`;
- conflicting `Content-Length`;
- unsupported transfer encoding;
- request timeout;
- idle connection timeout;
- invalid HTTP version;
- unsupported methods;
- unexpected connection states.

При неоднозначном request framing сервер должен **fail closed**.

Нельзя пытаться угадать, где заканчивается request.

---

# 20. TLS Development Certificates

Для локального тестирования используется self-signed certificate.

Генерация выполняется отдельным скриптом:

```text
scripts/generate_dev_certificate.sh
```

Скрипт является wrapper над OpenSSL.

Production certificate management не входит в scope проекта.

---

# 21. CLI

CLI должен поддерживать как минимум:

```text
--host
--port
--tls
--cert
--key
```

Также через CLI/config должны быть доступны:

```text
request timeout
keep-alive timeout
max request line size
max headers size
max header count
max body size
```

Пример:

```text
python -m http_server \
    --host 127.0.0.1 \
    --port 8080
```

HTTPS:

```text
python -m http_server \
    --host 127.0.0.1 \
    --port 8443 \
    --tls \
    --cert cert.pem \
    --key key.pem
```

---

# 22. Error Handling

Первая версия поддерживает:

| Code | Meaning |
|---:|---|
| 400 | Bad Request |
| 404 | Not Found |
| 405 | Method Not Allowed |
| 408 | Request Timeout |
| 413 | Content Too Large |
| 414 | URI Too Long |
| 431 | Request Header Fields Too Large |
| 500 | Internal Server Error |
| 501 | Not Implemented |
| 505 | HTTP Version Not Supported |

Internal Python exceptions не должны отправляться клиенту в виде traceback.

Traceback должен попадать в server-side logging.

---

# 23. Static Files

Static file serving может быть реализован как отдельный handler.

Если он реализуется, он обязан защищаться от path traversal.

Например:

```text
GET /../../secret.txt
```

не должен позволять получить файл за пределами document root.

После нормализации и разрешения filesystem path результат обязан оставаться внутри configured document root.

---

# 24. Logging

Используется Python standard library:

```python
logging
```

Поддерживаются уровни:

```text
DEBUG
INFO
WARNING
ERROR
```

Request logging должен включать как минимум:

```text
client
method
path
status
response size
duration
```

Request body и чувствительные headers не логируются по умолчанию.

---

# 25. Project Structure

```text
http-server-from-scratch/
│
├── README.md
├── ARCHITECTURE.md
├── pyproject.toml
├── .gitignore
│
├── src/
│   └── http_server/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── server.py
│       ├── connection.py
│       ├── event_loop.py
│       ├── tls_connection.py
│       │
│       ├── transport/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── plain.py
│       │   └── tls.py
│       │
│       ├── http/
│       │   ├── __init__.py
│       │   ├── parser.py
│       │   ├── request.py
│       │   ├── response.py
│       │   ├── headers.py
│       │   └── errors.py
│       │
│       ├── routing/
│       │   ├── __init__.py
│       │   ├── router.py
│       │   └── route.py
│       │
│       └── middleware/
│           ├── __init__.py
│           ├── base.py
│           └── logging.py
│
├── tests/
│   ├── unit/
│   │   ├── test_parser.py
│   │   ├── test_headers.py
│   │   ├── test_request.py
│   │   ├── test_response.py
│   │   ├── test_router.py
│   │   └── test_transport.py
│   │
│   └── integration/
│       ├── test_http_server.py
│       ├── test_keep_alive.py
│       ├── test_timeouts.py
│       └── test_tls.py
│
├── examples/
│   ├── basic_server.py
│   └── tls_server.py
│
└── scripts/
    └── generate_dev_certificate.sh
```

Если в ходе реализации структура модулей будет оптимизирована, изменения разрешены только при сохранении архитектурных границ, описанных в этом документе.

---

# 26. Module Responsibilities

## `config.py`

Хранит:

- network configuration;
- parser limits;
- timeout values;
- TLS configuration;
- server defaults.

## `http/request.py`

Содержит request representation.

Не содержит networking logic.

## `http/headers.py`

Отвечает за:

- хранение headers;
- case-insensitive lookup;
- добавление/получение headers;
- сериализацию при необходимости.

## `http/parser.py`

Отвечает исключительно за incremental HTTP parsing.

Не знает о:

- sockets;
- selectors;
- routes;
- handlers;
- TLS.

## `http/response.py`

Содержит:

- response representation;
- status code;
- reason phrase;
- headers;
- body;
- serialization.

## `routing/route.py`

Содержит route representation.

## `routing/router.py`

Отвечает за:

- route registration;
- path matching;
- method matching;
- path parameter extraction;
- `404`;
- `405`;
- `Allow`.

## `connection.py`

Отвечает за:

- connection state machine;
- input/output buffers;
- parser lifecycle;
- response lifecycle;
- keep-alive;
- timeout state;
- transport interaction.

## `tls_connection.py`

Содержит TLS-specific connection behavior.

Отвечает за:

- TLS handshake;
- SSL want-read;
- SSL want-write;
- TLS shutdown;
- перевод TLS events в transport readiness requirements.

## `transport/`

Абстрагирует low-level I/O.

### `base.py`

Определяет transport interface.

### `plain.py`

Реализует plain TCP transport.

### `tls.py`

Реализует TLS transport через `ssl.SSLContext`.

## `event_loop.py`

Отвечает за:

- selector;
- event registration;
- event dispatch;
- read readiness;
- write readiness;
- timeout polling.

Event loop не должен содержать HTTP parsing logic.

## `server.py`

Отвечает за:

- создание listening socket;
- bind;
- listen;
- accept;
- создание connections;
- startup;
- shutdown.

## `middleware/`

Содержит HTTP-level middleware.

Middleware не управляет socket lifecycle.

## `cli.py`

Отвечает за:

- argument parsing;
- configuration creation;
- startup orchestration.

---

# 27. Dependency Direction

Основное направление зависимостей:

```text
CLI
 ↓
Server
 ↓
Event Loop
 ↓
Connection
 ├── Transport
 ├── HTTP Parser
 └── HTTP Response
      ↓
Router / Middleware / Handler
```

При этом зависимости должны оставаться направленными и предсказуемыми.

В частности:

```text
HTTP parser  ─X→ socket
Router       ─X→ socket
Middleware   ─X→ selector
Handler      ─X→ socket
```

HTTP-level components не должны напрямую управлять network lifecycle.

---

# 28. Testing Strategy

## 28.1. Unit tests

Отдельно тестируются:

- request line parsing;
- fragmented request parsing;
- header parsing;
- header limits;
- request body parsing;
- invalid `Content-Length`;
- conflicting `Content-Length`;
- response serialization;
- route matching;
- path parameters;
- `404`;
- `405`;
- `Allow`;
- keep-alive decisions;
- transport abstraction.

## 28.2. Integration tests

Используются реальные TCP sockets.

Проверяются:

- connection establishment;
- HTTP request/response;
- multiple connections;
- keep-alive;
- multiple requests on one connection;
- leftover bytes after parsing;
- malformed requests;
- request timeout;
- keep-alive timeout;
- oversized requests;
- routing;
- middleware.

## 28.3. TLS tests

Отдельно проверяются:

1. successful non-blocking handshake;
2. `SSLWantReadError`;
3. `SSLWantWriteError`;
4. HTTPS GET;
5. HTTPS keep-alive;
6. TLS connection close;
7. handshake failure;
8. invalid TLS client behavior.

Ручная проверка:

```text
openssl s_client
curl --insecure
```

---

# 29. Implementation Order

Реализация выполняется небольшими тестируемыми этапами.

## Phase 1 — Configuration

Создать:

```text
config.py
```

Зафиксировать defaults и limits.

## Phase 2 — HTTP Data Structures

Создать:

```text
http/request.py
http/headers.py
```

Написать unit tests.

## Phase 3 — HTTP Parser

Создать:

```text
http/parser.py
```

Реализовать:

- request line;
- headers;
- incremental parsing;
- Content-Length;
- body;
- limits;
- malformed request detection;
- parser state machine.

После этого:

```text
pytest -v
```

## Phase 4 — HTTP Response

Создать:

```text
http/response.py
```

Реализовать:

- status line;
- headers;
- body;
- Content-Length;
- serialization.

После этого:

```text
pytest -v
```

## Phase 5 — Router

Создать:

```text
routing/route.py
routing/router.py
```

Реализовать:

- exact routes;
- named parameters;
- two-stage `404/405`;
- `Allow`.

После этого:

```text
pytest -v
```

## Phase 6 — Connection State Machine

Создать:

```text
connection.py
```

Реализовать plain TCP connection lifecycle:

```text
READING
PROCESSING
WRITING
KEEP_ALIVE
CLOSING
```

Пока без полноценного TLS integration.

## Phase 7 — Transport Abstraction

Создать:

```text
transport/base.py
transport/plain.py
```

Перевести connection на transport abstraction.

## Phase 8 — TLS

Создать:

```text
tls_connection.py
transport/tls.py
```

Реализовать:

- non-blocking handshake;
- `SSLWantReadError`;
- `SSLWantWriteError`;
- TLS shutdown.

## Phase 9 — Event Loop

Создать:

```text
event_loop.py
```

Интегрировать:

- selector;
- connections;
- dynamic `EVENT_READ`;
- dynamic `EVENT_WRITE`;
- O(n) timeout checking.

## Phase 10 — Server

Создать:

```text
server.py
```

Реализовать:

```text
socket()
bind()
listen()
accept()
```

и передачу connections в event loop.

## Phase 11 — Middleware

Создать middleware infrastructure и logging middleware.

## Phase 12 — CLI

Создать:

```text
cli.py
```

Добавить:

- host;
- port;
- TLS;
- certificate;
- key;
- limits;
- timeouts.

## Phase 13 — Integration Tests

Проверить весь plain HTTP lifecycle.

## Phase 14 — TLS Tests

Проверить весь HTTPS lifecycle.

---

# 30. Code Review Rules

После каждого существенного модуля выполняется:

```text
code-review
pytest -v
```

Результат тестов должен быть реальным и проверяемым.

Нельзя считать модуль завершённым только на основании статического анализа или предположения, что код должен работать.

Для:

- `parser.py`;
- `connection.py`;
- `tls_connection.py`;

используется расширенный security review.

Особое внимание:

- limits;
- malformed input;
- fragmented TCP reads;
- ambiguous framing;
- memory growth;
- timeout bypasses;
- state machine errors;
- SSL want-read/want-write;
- incorrect connection reuse.

---

# 31. Architectural Invariants

Следующие правила обязательны.

1. HTTP parser не зависит от sockets.
2. Router не зависит от sockets.
3. Middleware не зависит от selector.
4. Handler не управляет client socket.
5. Event loop владеет scheduling connections.
6. Connection владеет своим input/output state.
7. Все network sockets работают в non-blocking mode.
8. `EVENT_WRITE` регистрируется только при необходимости.
9. Parser проверяет limits во время накопления данных.
10. Request body framing никогда не угадывается.
11. Ошибочные и неоднозначные requests обрабатываются fail-closed.
12. TLS logic не распространяется в HTTP layer.
13. `SSLWantReadError` и `SSLWantWriteError` корректно переводятся в readiness requirements.
14. Keep-alive не должен нарушать request/response boundaries.
15. Непрочитанные bytes после одного request должны сохраняться.
16. HTTP pipelining не реализуется, но leftover buffer должен сохраняться.
17. Internal exceptions не должны раскрывать traceback клиенту.
18. TLS cryptography не реализуется вручную.
19. Внешние web frameworks не используются.
20. Архитектурные зависимости должны оставаться явными и тестируемыми.

---

# 32. Scope

## Поддерживается

```text
TCP
HTTP/1.1
минимальная HTTP/1.0 compatibility
GET
HEAD
POST
PUT
DELETE
OPTIONS
Content-Length
keep-alive
incremental parsing
routing
path parameters
middleware
timeouts
request limits
TLS через ssl.SSLContext
```

## Не поддерживается в первой версии

```text
HTTP/2
HTTP/3
QUIC
WebSocket
Transfer-Encoding: chunked для request body
HTTP trailers
compression
proxying
caching
CGI
FastCGI
HTTP request pipelining
production TLS certificate management
```

Эти возможности могут появиться в следующих версиях без изменения базовой архитектуры.

---

# 33. Open Questions

Следующие вопросы не блокируют реализацию:

- точный формат logging;
- graceful shutdown;
- окончательные default values для limits;
- расширенный static file handler;
- дополнительные middleware.

Они решаются после появления работающего базового HTTP lifecycle.

Архитектурные решения, перечисленные в разделах 1–32, не должны изменяться без отдельного обоснования.

---

# 34. Definition of Done

Проект считается завершённым, когда:

- [ ] TCP server принимает подключения.
- [ ] HTTP request parser работает на raw bytes.
- [ ] Fragmented TCP reads обрабатываются корректно.
- [ ] Request line и headers разбираются вручную.
- [ ] Content-Length body разбирается корректно.
- [ ] Parser limits применяются во время накопления данных.
- [ ] Invalid и conflicting Content-Length обрабатываются.
- [ ] HTTP responses сериализуются вручную.
- [ ] Несколько connections обслуживаются одновременно через event loop.
- [ ] `EVENT_WRITE` динамически включается и выключается.
- [ ] HTTP/1.1 keep-alive работает.
- [ ] HTTP/1.0 compatibility работает в заявленном scope.
- [ ] Leftover bytes сохраняются.
- [ ] HTTP pipelining не выполняется, но buffer semantics корректны.
- [ ] Router поддерживает exact routes.
- [ ] Router поддерживает path parameters.
- [ ] `404` и `405` корректно различаются.
- [ ] `Allow` формируется корректно.
- [ ] Middleware pipeline работает.
- [ ] Request и keep-alive timeouts работают.
- [ ] TLS handshake работает в non-blocking режиме.
- [ ] `SSLWantReadError` корректно обрабатывается.
- [ ] `SSLWantWriteError` корректно обрабатывается.
- [ ] HTTPS GET работает.
- [ ] HTTPS keep-alive работает.
- [ ] Static file traversal protection работает, если static files реализованы.
- [ ] Unit tests проходят.
- [ ] Integration tests проходят.
- [ ] TLS tests проходят.
- [ ] CLI запускает HTTP и HTTPS server.
- [ ] Проект не использует Flask/FastAPI/`http.server`/`asyncio`.
- [ ] `pytest -v` проходит без известных failures.

---

# 35. Final Architectural Principle

Сервер должен оставаться прозрачной реализацией следующего процесса:

```text
                    ┌──────────────────┐
                    │    TCP Socket    │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │     Transport    │
                    │ Plain TCP / TLS  │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │    Connection    │
                    │   State Machine  │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │   HTTP Parser    │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │   HTTP Request   │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │      Router      │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ Middleware/      │
                    │ Handler          │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │  HTTP Response   │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ Response         │
                    │ Serializer       │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │     Transport    │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │    TCP Socket    │
                    └──────────────────┘
```

Каждый слой должен иметь одну понятную ответственность.

Код не должен скрывать сетевую механику за framework abstractions.

Главная задача проекта — не просто создать работающий HTTP server, а реализовать и протестировать его внутренние механизмы явно, модульно и безопасно.

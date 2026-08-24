# ELI5: http-server-from-scratch

*ELI5 = "Explain Like I'm 5" — a plain-language explanation with no jargon.
The technical version lives in [ARCHITECTURE.md](ARCHITECTURE.md).*

## What is this thing?

Imagine the internet is a city full of houses. Your browser is a **messenger** who runs between houses delivering notes. When you type `https://example.com`, the messenger runs to the house called `example.com` and shouts: "Hey! Give me the page for `/`!"

This program is that house. It's a tiny building that listens for messengers, reads their notes, and shouts back the right answer. Most houses are built by giant construction companies (Flask, FastAPI, Nginx) that hide how the walls work. We built this one brick by brick so you can see every pipe and wire.

## Why not use Flask / FastAPI?

Because they are like buying a prefab house. You get a house instantly, but you never learn how plumbing works. If you want to understand *why* a note says `GET / HTTP/1.1`, or why a messenger can keep the door open and ask for three pages in a row without knocking again, you need to build it yourself.

We use only the tools that the operating system gives everyone: a **telephone socket** and a **waiting list**.

## The telephone and the waiting list

Every house has a telephone (a TCP socket). When a messenger calls, the phone rings.

Instead of hiring one person per call (threads) or a magic robot that hides the work (`asyncio`), we have one very focused receptionist and a waiting list:

- `socket` — the telephone itself.
- `selectors.DefaultSelector` — the waiting list that says "these 5 phones have someone talking, those 2 want to speak".

The receptionist loops forever:

1. Ask the waiting list: "Who's ready?"
2. If someone is ready to **talk** → read a bit of what they said.
3. If someone is ready to **listen** → speak a bit of the answer.
4. Check who has been silent too long → hang up (timeout).

That loop is the *event loop*. One thread, many phones.

## What happens when a messenger speaks?

Messengers don't speak in perfect sentences. They stutter, and the phone is crackly:

```
recv #1: "GET /inde"
recv #2: "x HTTP/1.1\r\nHost: lo"
recv #3: "calhost\r\n\r\n"
```

All three together mean `GET /index HTTP/1.1`. If we assumed one `recv` equals one request, we'd break.

So we have a **puzzle assembler** — the incremental parser. It has four boxes:

```
REQUEST_LINE → HEADERS → BODY → DONE
```

Each new piece of bytes is poured in with `feed(bytes)`. The assembler checks:
- Is the first line `METHOD TARGET VERSION`?
- Did we find `\r\n\r\n` that ends the headers?
- Do we have exactly `Content-Length` bytes of body?

If not enough bytes — "need more". If broken — error. The assembler never touches the phone; it only sees bytes.

Limits are checked *while* pouring, not after the whole puzzle is done. If someone tries to send a 10 MB header to blow up our memory, we stop at 32 KiB with `431` and never allocate the rest.

## The bouncer and the librarian

Once the puzzle is complete, we get a neat `Request` object:

```text
method: "GET", path: "/users/42", headers: {"Host": "localhost"}, body: b""
```

Now two people handle it:

- **The bouncer (Router)** checks the path. `/users/42` matches the rule `/users/{id}` and extracts `{"id": "42"}`. If the path exists but the method is wrong (`POST /users/42` when only `GET` exists) → `405` + `Allow: GET`. If nothing matches → `404`.

- **The librarian (Handler)** actually answers: `Response(status_code=200, body=b"hello")`. The librarian never touches the phone.

Then the **translator (Serializer)** does the reverse of the parser: `Response → bytes`:

```text
HTTP/1.1 200 OK\r\n
Content-Length: 5\r\n
\r\n
Hello
```

`Content-Length` is counted in *bytes*, not letters (`"Привет".encode()` is 12 bytes, not 6). If the librarian accidentally says `Content-Length: 10` but gives 5 bytes, we raise `ValueError` instead of sending a lying header. For `HEAD`, we keep the `Content-Length` of the `GET` but send no body. For `204/304/1xx`, we force `Content-Length: 0` and no body. And we never allow `\r` or `\n` inside a header to smuggle an extra line.

## The door that stays open

Messengers are polite: after one note, they can keep the door open and send another without redialing. That's *keep-alive*.

- HTTP/1.1: door stays open unless `Connection: close`.
- HTTP/1.0: door closes unless `Connection: keep-alive`.

If two notes arrive in one phone call (`GET /` + `GET /two` in one `recv`), we must not throw the second away. The parser keeps the leftover bytes for the next puzzle, but we still answer one at a time — no pipelining.

If a messenger mumbles for too long (slowloris), the `last_activity` clock and `request_timeout` / `keep_alive_timeout` hang up.

## The secret tunnel (TLS)

For `https://`, the phone line is wrapped in a secret tunnel (`ssl.SSLContext`). The tunnel needs a handshake that is also non-blocking: sometimes it says "I need to read before I can write" (`SSLWantReadError`) and vice versa. We translate that into `want_read/want_write` so the waiting list knows what to watch. We never write our own crypto.

## What this does NOT do

- No HTTP/2, WebSockets, chunked bodies, compression, or caching.
- No database, no auth.
- Not hardened for the open internet without a reverse proxy.

But everything it *does* do is visible, tested (171 tests), and typed (`mypy --strict`).

## TL;DR

- One phone, one waiting list, one loop
- Incremental parser that never trusts one `recv`
- Router that knows `404` vs `405`
- Serializer that never lies about `Content-Length`
- Keep-alive with timeouts and leftover preservation
- TLS via `ssl.SSLContext`, non-blocking

Want the grown-up version with all the details?
[ARCHITECTURE.md](ARCHITECTURE.md). На русском: [README_RU.md](README_RU.md).

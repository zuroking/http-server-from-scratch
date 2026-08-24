"""CLI for http-server-from-scratch (Phase 12)."""

from __future__ import annotations

import argparse
import logging
import ssl
import sys

from http_server.config import ServerConfig
from http_server.http.response import Response
from http_server.routing.router import Router
from http_server.server import Server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="http-server-from-scratch")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind (default 8080)")
    parser.add_argument("--tls", action="store_true", help="Enable TLS (HTTPS)")
    parser.add_argument("--cert", type=str, default=None, help="Path to TLS certificate (PEM)")
    parser.add_argument("--key", type=str, default=None, help="Path to TLS private key (PEM)")
    parser.add_argument("--request-timeout", type=float, default=5.0, help="Request timeout seconds (default 5.0)")
    parser.add_argument("--keep-alive-timeout", type=float, default=5.0, help="Keep-alive timeout seconds (default 5.0)")
    parser.add_argument("--max-request-line", type=int, default=8192, help="Max request line size (default 8192)")
    parser.add_argument("--max-headers-size", type=int, default=32768, help="Max headers size (default 32768)")
    parser.add_argument("--max-header-count", type=int, default=100, help="Max header count (default 100)")
    parser.add_argument("--max-body-size", type=int, default=1048576, help="Max body size (default 1048576)")
    parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Log level")
    return parser


def create_config(args: argparse.Namespace) -> ServerConfig:
    return ServerConfig(
        host=args.host,
        port=args.port,
        max_request_line_size=args.max_request_line,
        max_headers_size=args.max_headers_size,
        max_header_count=args.max_header_count,
        max_body_size=args.max_body_size,
        request_timeout=args.request_timeout,
        keep_alive_timeout=args.keep_alive_timeout,
    )


def create_router() -> Router:
    router = Router()

    def hello(req):  # type: ignore
        return Response(status_code=200, body=b"Hello from http-server-from-scratch")

    def health(req):  # type: ignore
        return Response(status_code=200, body=b"OK")

    router.add("GET", "/", hello)
    router.add("GET", "/health", health)
    return router


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        config = create_config(args)
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    # Validate TLS args
    ssl_ctx = None
    if args.tls:
        if not args.cert or not args.key:
            parser.error("--tls requires --cert and --key")
            return 2
        try:
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(args.cert, args.key)
        except Exception as exc:
            logging.error("Failed to load TLS cert/key: %s", exc)
            return 1

    # Validate port
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1..65535")
        return 2

    router = create_router()
    server = Server(host=args.host, port=args.port, router=router, config=config, ssl_context=ssl_ctx)

    print(f"Starting {'HTTPS' if ssl_ctx else 'HTTP'} server on {args.host}:{args.port}")
    try:
        server.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""TLS HTTPS server example — requires cert.pem / key.pem."""

import ssl
from pathlib import Path

from http_server.config import ServerConfig
from http_server.http.response import Response
from http_server.routing.router import Router
from http_server.server import Server


def main() -> None:
    cert = Path("cert.pem")
    key = Path("key.pem")
    if not cert.exists() or not key.exists():
        print("Missing cert.pem / key.pem")
        print("Generate with: ./scripts/generate_dev_certificate.sh")
        raise SystemExit(1)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))

    router = Router()
    router.add("GET", "/", lambda req: Response(status_code=200, body=b"Hello over TLS!"))
    router.add("GET", "/users/{id}", lambda req: Response(status_code=200, body=f"user {req.route_params['id']}".encode()))

    config = ServerConfig(host="127.0.0.1", port=8443)
    server = Server(host="127.0.0.1", port=8443, router=router, config=config, ssl_context=ctx)

    print("Serving on https://127.0.0.1:8443  (Ctrl+C to stop)")
    print("Test with: curl --insecure https://127.0.0.1:8443/")
    try:
        server.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.stop()


if __name__ == "__main__":
    main()

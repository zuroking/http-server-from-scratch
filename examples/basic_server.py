"""Basic HTTP server example — plain TCP, no TLS."""

from http_server.config import ServerConfig
from http_server.http.response import Response
from http_server.routing.router import Router
from http_server.server import Server


def main() -> None:
    router = Router()

    def home(req):
        return Response(status_code=200, body=b"Hello from http-server-from-scratch!")

    def hello(req):
        name = req.route_params.get("name", "world")
        return Response(status_code=200, body=f"Hello, {name}!".encode())

    def echo(req):
        # Echoes request body back
        return Response(status_code=200, body=req.body)

    router.add("GET", "/", home)
    router.add("GET", "/hello/{name}", hello)
    router.add("POST", "/echo", echo)

    config = ServerConfig(host="127.0.0.1", port=8080)
    server = Server(host="127.0.0.1", port=8080, router=router, config=config)

    print("Serving on http://127.0.0.1:8080  (Ctrl+C to stop)")
    try:
        server.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.stop()


if __name__ == "__main__":
    main()

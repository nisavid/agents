#!/usr/bin/env python3
"""THROWAWAY PROTOTYPE ONLY: record proxy destinations and never forward."""

from __future__ import annotations

import json
import socketserver
import sys
from datetime import datetime, timezone


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.connection.settimeout(2)
        try:
            line = self.rfile.readline(8192).decode("iso-8859-1", "replace").strip()
            headers: dict[str, str] = {}
            while True:
                raw = self.rfile.readline(8192)
                if not raw or raw in (b"\r\n", b"\n"):
                    break
                decoded = raw.decode("iso-8859-1", "replace").strip()
                if ":" in decoded:
                    name, value = decoded.split(":", 1)
                    headers[name.lower()] = value.strip()
        except (OSError, TimeoutError):
            line = "<read failed>"
            headers = {}

        request_parts = line.split(" ", 2)
        method = request_parts[0] if len(request_parts) == 3 else None

        print(
            json.dumps(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "method": method,
                    "host": headers.get("host"),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        try:
            self.wfile.write(
                b"HTTP/1.1 502 Air-gapped prototype\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: 20\r\n"
                b"Connection: close\r\n\r\n"
                b"air-gapped prototype"
            )
        except OSError:
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    port = int(sys.argv[1])
    with Server(("127.0.0.1", port), Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()

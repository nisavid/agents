#!/usr/bin/env python3
"""THROWAWAY PROTOTYPE ONLY: verify auth replacement while forwarding locally."""

from __future__ import annotations

import http.client
import json
import os
import pathlib
import socketserver
import sys
from http.server import BaseHTTPRequestHandler


LISTEN_PORT = int(sys.argv[1])
TARGET_PORT = int(sys.argv[2])
EVIDENCE_PATH = pathlib.Path(sys.argv[3])
EXPECTED_UPSTREAM_AUTH = f"Bearer {os.environ['EXPECTED_UPSTREAM_TOKEN']}"
FORBIDDEN_INBOUND_AUTH = f"Bearer {os.environ['FORBIDDEN_INBOUND_TOKEN']}"


def append_evidence(record: dict[str, object]) -> None:
    with EVIDENCE_PATH.open("a") as evidence:
        evidence.write(json.dumps(record, sort_keys=True) + "\n")
        evidence.flush()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP handler contract
        self._forward(None)

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler contract
        length = int(self.headers.get("Content-Length", "0"))
        self._forward(self.rfile.read(length))

    def _forward(self, body: bytes | None) -> None:
        authorization = self.headers.get("Authorization")
        append_evidence(
            {
                "exact_upstream_token": authorization == EXPECTED_UPSTREAM_AUTH,
                "inbound_token_reused": authorization == FORBIDDEN_INBOUND_AUTH,
            }
        )
        forwarded_headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in {"connection", "host", "content-length"}
        }
        if body is not None:
            forwarded_headers["Content-Length"] = str(len(body))
        connection = http.client.HTTPConnection("127.0.0.1", TARGET_PORT, timeout=180)
        try:
            connection.request(self.command, self.path, body=body, headers=forwarded_headers)
            response = connection.getresponse()
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in {"connection", "transfer-encoding", "content-length"}:
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            if "text/event-stream" in (response.getheader("Content-Type") or "").lower():
                terminal_completed = False
                done_observed = False
                terminal_recorded = False
                try:
                    while True:
                        line = response.readline()
                        if not line:
                            break
                        if line.startswith(b"data:"):
                            payload = line[5:].strip()
                            if payload == b"[DONE]":
                                done_observed = True
                            elif payload:
                                try:
                                    event = json.loads(payload)
                                except (json.JSONDecodeError, UnicodeDecodeError):
                                    pass
                                else:
                                    terminal_completed = terminal_completed or (
                                        isinstance(event, dict)
                                        and event.get("type") == "response.completed"
                                    )
                                    if terminal_completed and not terminal_recorded:
                                        append_evidence(
                                            {
                                                "upstream_terminal_completed": True,
                                            }
                                        )
                                        terminal_recorded = True
                        self.wfile.write(line)
                        self.wfile.flush()
                finally:
                    if not terminal_recorded:
                        append_evidence(
                            {
                                "upstream_done_observed": done_observed,
                                "upstream_terminal_completed": terminal_completed,
                            }
                        )
            else:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        finally:
            connection.close()
            self.close_connection = True

    def log_message(self, _format: str, *_args: object) -> None:
        return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    with Server(("127.0.0.1", LISTEN_PORT), Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()

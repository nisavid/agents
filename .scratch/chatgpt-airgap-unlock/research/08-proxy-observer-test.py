#!/usr/bin/env python3
"""Focused regression for request-target redaction in the proxy observer."""

from __future__ import annotations

import json
import select
import socket
import subprocess
from pathlib import Path
import sys
import time


HERE = Path(__file__).resolve().parent
OBSERVER = HERE / "08-proxy-observer.py"


def main() -> None:
    reservation = socket.socket()
    reservation.bind(("127.0.0.1", 0))
    port = reservation.getsockname()[1]
    reservation.close()
    process = subprocess.Popen(
        [sys.executable, str(OBSERVER), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert process.stdout is not None
    try:
        deadline = time.monotonic() + 2
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2) as client:
                    client.sendall(
                        b"GET /private?token=do-not-persist HTTP/1.1\r\n"
                        b"Host: example.invalid:443\r\n"
                        b"User-Agent: ticket08-test\r\n\r\n"
                    )
                    client.recv(4096)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        ready, _, _ = select.select([process.stdout], [], [], 2)
        assert ready, "proxy observer emitted no record"
        record = json.loads(process.stdout.readline())
    finally:
        process.terminate()
        process.wait(timeout=5)
    encoded = json.dumps(record, sort_keys=True)
    assert record["method"] == "GET"
    assert record["host"] == "example.invalid:443"
    assert "request" not in record
    assert "do-not-persist" not in encoded
    print("proxy observer request-target redaction regression passed")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""THROWAWAY PROTOTYPE ONLY: exec one command in a new owned process group."""

from __future__ import annotations

import os
import sys


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: 08-process-group.py COMMAND [ARG ...]")
    os.setsid()
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)


if __name__ == "__main__":
    main()

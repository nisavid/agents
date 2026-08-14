from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
from pathlib import Path


def observe_shim(observation: str) -> None:
    open_descriptors = []
    for descriptor in range(3, 64):
        try:
            fcntl.fcntl(descriptor, fcntl.F_GETFD)
        except OSError:
            continue
        open_descriptors.append(descriptor)
    remote_debug: dict[str, str]
    remote_exec = getattr(sys, "remote_exec", None)
    if remote_exec is None:
        remote_debug = {"api": "unavailable"}
    else:
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "inert_remote_script.py"
            script.write_text("pass\n", encoding="utf-8")
            try:
                remote_exec(os.getpid(), str(script))
            except RuntimeError as error:
                remote_debug = {
                    "api": "available",
                    "outcome": "disabled",
                    "error": str(error),
                }
            else:
                remote_debug = {"api": "available", "outcome": "enabled"}
    with open(observation, "w", encoding="utf-8") as stream:
        json.dump(
            {
                "argv": sys.argv[1:],
                "environment": dict(os.environ),
                "executable": sys.executable,
                "flags": {
                    "dont_write_bytecode": sys.flags.dont_write_bytecode,
                    "isolated": sys.flags.isolated,
                    "no_site": sys.flags.no_site,
                },
                "xoptions": sys._xoptions,
                "remote_debug": remote_debug,
                "open_descriptors": open_descriptors,
            },
            stream,
            sort_keys=True,
        )

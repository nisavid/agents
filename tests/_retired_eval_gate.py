"""Test-only driver for the retained non-authoritative eval structure checks."""

from __future__ import annotations

import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = (
    ROOT
    / "plugins/versionkeeping/skills/checkpointing-and-publishing-git-work/"
    "scripts/check_eval_gate.py"
)


if __name__ == "__main__":
    namespace = runpy.run_path(str(GATE))
    raise SystemExit(namespace["_retired_evidence_main"]())

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


def main(arguments: list[str]) -> int:
    launcher_path = Path(arguments[0])
    root_kind = arguments[1]
    root = Path(arguments[2])
    specification = importlib.util.spec_from_file_location(
        "task_witness_launcher_fixture",
        launcher_path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("launcher fixture cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    if root_kind == "installed-root":
        module._installed_root = lambda: root
    elif root_kind == "account-home":
        module.pwd.getpwuid = lambda _uid: SimpleNamespace(pw_dir=str(root))
    else:
        raise ValueError(f"unknown launcher root kind: {root_kind}")
    return module.main(arguments[3:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

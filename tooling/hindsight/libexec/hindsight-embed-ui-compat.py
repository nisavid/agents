#!/usr/bin/env python3
"""Apply a fail-closed compatibility patch to the published Hindsight UI."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


PACKAGE_NAME = "@vectorize-io/hindsight-control-plane"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?\Z")
AUTH_NEEDLE = "if(t&&!or.some(e=>o.startsWith(e))){"
AUTH_REPLACEMENT = (
    'if(t&&!or.some(e=>o.startsWith(e))'
    '&&!ap.some(e=>o==="/"+e+"/login"'
    '||o.startsWith("/"+e+"/login/"))){'
)
ROUTING_NEEDLE = '}return os(e)}e.s(["config"'
ROUTING_REPLACEMENT = (
    '}let i=ap.find(t=>o===`/${t}`||o.startsWith(`/${t}/`));'
    "if(i)return eb.next();"
    "let n=e.nextUrl.clone();"
    'n.pathname=`/en${"/"===o?"":o}`;'
    'return eb.rewrite(n)}e.s(["config"'
)
PATCH_MARKER = "let i=ap.find(t=>o===`/${t}`"
NODE_LOCATOR = r"""
const fs = require("fs");
const path = require("path");
for (const directory of (process.env.PATH || "").split(path.delimiter)) {
  const candidate = path.join(directory, "hindsight-control-plane");
  if (fs.existsSync(candidate)) {
    console.log(fs.realpathSync(candidate));
    process.exit(0);
  }
}
process.exit(1);
""".strip()


class CompatibilityError(RuntimeError):
    """The published package does not match the approved patch contract."""


def _safe_environment() -> dict[str, str]:
    allowed = (
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "NPM_CONFIG_CACHE",
        "npm_config_cache",
        "PATH",
        "TMPDIR",
    )
    return {name: os.environ[name] for name in allowed if name in os.environ}


def locate_package(npx: Path, version: str) -> Path:
    if not npx.is_absolute() or not npx.is_file() or not os.access(npx, os.X_OK):
        raise CompatibilityError("configured npx executable is unavailable")
    if VERSION.fullmatch(version) is None:
        raise CompatibilityError("control-plane version is invalid")

    completed = subprocess.run(
        [
            str(npx),
            "-y",
            "-p",
            f"{PACKAGE_NAME}@{version}",
            "node",
            "-e",
            NODE_LOCATOR,
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=300,
        env=_safe_environment(),
    )
    if completed.returncode != 0:
        raise CompatibilityError("control-plane package preparation failed")
    paths = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(paths) != 1:
        raise CompatibilityError("control-plane package location is ambiguous")

    cli = Path(paths[0]).resolve(strict=True)
    package = cli.parent.parent
    try:
        metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CompatibilityError("control-plane package metadata is invalid") from error
    if metadata.get("name") != PACKAGE_NAME or metadata.get("version") != version:
        raise CompatibilityError("control-plane package identity does not match")
    return package


def _middleware_chunk(package: Path) -> Path:
    next_root = package / "standalone" / ".next"
    try:
        manifest = json.loads(
            (next_root / "server" / "middleware-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        files = manifest["middleware"]["/"]["files"]
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise CompatibilityError("control-plane middleware manifest is invalid") from error
    if not isinstance(files, list) or not files:
        raise CompatibilityError("control-plane middleware files are unavailable")

    matches: list[Path] = []
    for relative in files:
        if not isinstance(relative, str) or relative.startswith(("/", "\\")):
            raise CompatibilityError("control-plane middleware path is invalid")
        candidate = (next_root / relative).resolve(strict=True)
        try:
            candidate.relative_to(next_root.resolve(strict=True))
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as error:
            raise CompatibilityError("control-plane middleware file is invalid") from error
        if "hindsight_cp_access" in content:
            matches.append(candidate)
    if len(matches) != 1:
        raise CompatibilityError("control-plane authentication middleware is ambiguous")
    return matches[0]


def patch_package(package: Path) -> bool:
    chunk = _middleware_chunk(package)
    lock_path = package / ".hindsight-ui-compat.lock"
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    with os.fdopen(descriptor, "r+", encoding="ascii") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        content = chunk.read_text(encoding="utf-8")
        already_patched = (
            AUTH_REPLACEMENT in content
            and PATCH_MARKER in content
            and AUTH_NEEDLE not in content
            and ROUTING_NEEDLE not in content
        )
        if already_patched:
            return False
        if content.count(AUTH_NEEDLE) != 1 or content.count(ROUTING_NEEDLE) != 1:
            raise CompatibilityError(
                "control-plane middleware does not match the approved contract"
            )

        patched = content.replace(AUTH_NEEDLE, AUTH_REPLACEMENT).replace(
            ROUTING_NEEDLE, ROUTING_REPLACEMENT
        )
        mode = chunk.stat().st_mode & 0o777
        descriptor, temporary_name = tempfile.mkstemp(
            dir=chunk.parent, prefix=f".{chunk.name}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(patched)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, chunk)
        finally:
            temporary.unlink(missing_ok=True)
    return True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--npx", required=True, type=Path)
    parser.add_argument("--version", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        package = locate_package(args.npx, args.version)
        patch_package(package)
    except (CompatibilityError, OSError, subprocess.SubprocessError) as error:
        print(f"hindsight-embed-ui-compat: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

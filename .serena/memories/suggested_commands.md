# Suggested commands

Run commands from the repository root.

## ChatGPT FFS

- `python3 -m unittest tooling/chatgpt-ffs/test_asar_roundtrip.py tooling/chatgpt-ffs/test_patches.py` — run the pure-Python unit suite without touching an app bundle.
- `tooling/chatgpt-ffs/chatgpt-ffs --help` — inspect the CLI without mutating an app.
- Pass `--app /path/to/a/disposable-copy.app` for any bundle operation. Never exercise development changes against the main installed `ChatGPT.app`.

## Namespace proxy

- `python3 -m py_compile tooling/codex-ns-proxy/codex-ns-proxy.py` — syntax-check the proxy.
- `NS_PROXY_UPSTREAM=<url> python3 tooling/codex-ns-proxy/codex-ns-proxy.py` — run the proxy against an explicit upstream.

## Repository checks

- `git diff --check` — detect whitespace errors.
- `serena memories check` — validate project-memory references.
- `serena project health-check .` — validate Serena project tooling and language-server behavior.
- Plugin source changes require the harness-specific validation and cache-refresh flow documented in `plugins/thermos/README.md`.
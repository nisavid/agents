# Task completion

Apply only the checks relevant to the changed surface, then finish with `git diff --check`.

- ChatGPT FFS logic or patch definitions: `python3 -m unittest tooling/chatgpt-ffs/test_asar_roundtrip.py tooling/chatgpt-ffs/test_patches.py`.
- Namespace proxy: `python3 -m py_compile tooling/codex-ns-proxy/codex-ns-proxy.py`, plus a focused local request/response smoke test when protocol behavior changes.
- Thermos plugin: run the Codex and/or Claude validation and reinstall/cache-refresh workflow documented in `plugins/thermos/README.md`; verify a fresh harness session when runtime discovery changes.
- Hindsight templates or scripts: validate the affected shell/Python/plist/template syntax and keep live-machine apply work separate unless explicitly authorized.
- Serena configuration or memories: `serena memories check`, `serena project index .`, and `serena project health-check .`.
- Before committing, audit task-owned paths only. Before publishing, use the repository publication planner and verify the exact destination ref and pushed SHA.

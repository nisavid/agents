# ChatGPT Unflag

Interactive TUI and CLI for managing `app.asar` patches against the
ChatGPT desktop app (`ChatGPT.app`). Handles asar extraction, patching, repacking, ASAR integrity
hash updates, and code signing with proper Electron entitlements — all in pure
Python with zero external dependencies.

## Why

Codex Desktop gates certain features behind Statsig feature flags that require
ChatGPT authentication to evaluate. In air-gapped or restricted environments
where ChatGPT auth is unavailable, these flags default to `false`, hiding
features like the worktree option in the "select where to run" menu.

This tool lets you bypass specific feature gates by patching the minified JS
bundles inside `app.asar`, without needing ChatGPT login or network access to
Statsig.

## Installation

```bash
./chatgpt-unflag install
```

Symlinks the script into `~/.local/bin/` and ensures `~/.local/bin/env`
exists (sourced by `~/.profile` and `~/.zshenv` to activate `~/.local/bin`
on `PATH`). After installing, `chatgpt-unflag` is available from any
shell. New terminals pick it up automatically; for the current shell:

```bash
source ~/.local/bin/env
```

## Usage

### Interactive TUI

```bash
./chatgpt-unflag
```

Navigate with arrow keys, toggle patches with Space, preview the delta with P,
commit with C, or abort (discard) the pending selection with A.

### CLI

```bash
# List all patches and their current state
./chatgpt-unflag list

# Show brief status
./chatgpt-unflag status

# Apply specific patches
./chatgpt-unflag apply worktree-feature-flag

# Revert specific patches (or all)
./chatgpt-unflag revert worktree-feature-flag
./chatgpt-unflag revert all
```

### Specify a different app path

```bash
./chatgpt-unflag --app /path/to/ChatGPT.app list
```

## How It Works

1. **Backup**: On first run, the current `app.asar` is backed up to
   `app.asar.orig`. This pristine copy is the base for all future operations.
2. **Extract**: The original asar is extracted to a temp directory (pure Python
   asar reader — no Node.js or `@electron/asar` needed).
3. **Patch**: Selected patches are applied as regex search-and-replace
   operations on the extracted files.
4. **Pack**: The patched files are repacked into a new asar (with SHA256
   integrity hashes per file).
5. **Install**: The patched asar replaces `app.asar`, and the
   `ElectronAsarIntegrity` SHA256 hash in `Info.plist` is updated.
6. **Sign**: The app is re-signed with an ad-hoc signature, applying proper
   Electron entitlements (JIT, unsigned memory, library validation) to the
   Codex Framework and main app bundle.

## Patch Registry

Patches are defined in the script itself (`BUILTIN_PATCHES`) and can be
extended with external JSON files in `patches.d/`:

```json
[
  {
    "id": "my-custom-patch",
    "name": "My Custom Patch",
    "category": "ui-tweaks",
    "description": "Description of what this patch does.",
    "modifications": [
      {
        "file": "webview/assets/some-module-*.js",
        "search": "\\w+\\(`1234567890`\\)",
        "replace": "!0",
        "description": "Bypass feature gate 1234567890"
      }
    ]
  }
]
```

### Adding a New Feature Flag Bypass

1. Find the Statsig gate ID by searching the asar for `checkGate` calls or
   numeric gate IDs.
2. Note which JS files contain the gate check (use `rg` on the extracted asar).
3. Add a patch entry with the gate ID as the search pattern and `!0` (true) as
   the replacement.

## Caveats

- **App updates**: Patches are lost when the app updates. Re-run the tool after
  updating. The tool detects stale backups and will re-create the original from
  the current (updated) asar.
- **Code signing**: The ad-hoc re-sign replaces OpenAI Developer ID signature.
  The app runs locally but will not pass Gatekeeper on other machines.
- **Reverting**: To fully restore the original app, use `revert all` or copy
  `app.asar.orig` back to `app.asar` and re-sign manually.

## State

State is stored in `~/.codex/chatgpt-unflag/state.json`, tracking the
last installed asar hash and which patches were applied.

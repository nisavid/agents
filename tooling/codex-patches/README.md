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
    "labels": ["local-only", "ui-visibility", "user-facing"],
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

## Label Taxonomy

Each patch is tagged with labels across four dimensions:

- **Requirements** — what it needs externally to function:
  `local-only`, `custom-provider`, `self-hosted-infra`, `platform-macos`
- **Provisions** — what it provides within the app:
  `workspace`, `conversation`, `execution`, `ui-visibility`,
  `plugin-system`, `voice-input`, `notification`, `sandbox`,
  `experimental`, `file-access`, `image-input`, `screenshot`,
  `multi-window`, `desktop-companion`, `ambient-ui`
- **Affordances** — what it enables the user/agent to do:
  `local-dev`, `history`, `mode-switch`, `thread-v2`,
  `per-conversation-config`, `skill-management`
- **Behaviors** — who it serves:
  `user-facing`, `agent-facing`

Press `L` in the TUI to see a legend with descriptions of every label.

## Built-in Patches

29 patches across 7 functional groups, covering 32 Statsig gate IDs.

### Workspace & Local Execution

| Patch | Gate | Labels |
|-------|------|--------|
| Worktree Option in Run Location Menu | `505458` | `local-only` `workspace` `local-dev` `user-facing` `agent-facing` |
| Persistent Thread Catalog | `567837310` | `local-only` `conversation` `history` `user-facing` `agent-facing` |
| Headless Chat / Recent Threads View | `12346831` | `local-only` `workspace` `conversation` `local-dev` `history` `user-facing` |
| Local Project Actions | `824038554` | `local-only` `workspace` `local-dev` `user-facing` `agent-facing` |
| Mode Switching UI | `3264431617` | `local-only` `ui-visibility` `mode-switch` `user-facing` `agent-facing` |
| Feature Visibility (Auth-State) | `1372061905` | `custom-provider` `ui-visibility` `user-facing` |

### Conversation & Thread Management

| Patch | Gate | Labels |
|-------|------|--------|
| Timeline Tab in Conversation View | `40604217` | `local-only` `conversation` `ui-visibility` `user-facing` `agent-facing` |
| Full Review Mode | `2333235660` | `local-only` `conversation` `user-facing` `agent-facing` |
| Open Conversation in New Window | `459748632` | `local-only` `conversation` `multi-window` `user-facing` |
| Thread v2 Format | `57256278` | `local-only` `conversation` `thread-v2` `user-facing` `agent-facing` |
| Per-Conversation Settings | `3736891373` | `local-only` `conversation` `per-conversation-config` `user-facing` `agent-facing` |
| Notification Tracking | `3789238711` | `local-only` `notification` `user-facing` |

### Input & Interaction

| Patch | Gate | Labels |
|-------|------|--------|
| Dictation / Voice Input | `1244621283` | `local-only` `voice-input` `user-facing` |
| Dictation Companion Gate | `4100906017` | `local-only` `voice-input` `user-facing` |
| File System Permission Prompts | `1258561229` | `local-only` `file-access` `user-facing` `agent-facing` |
| File System Permission Companion | `1378180112` | `local-only` `file-access` `user-facing` `agent-facing` |
| Image Input Support | `1907601843` | `custom-provider` `image-input` `user-facing` `agent-facing` |
| Ambient Home Features | `3207467860` | `local-only` `ambient-ui` `user-facing` |

### Plugin & Skills System

| Patch | Gate | Labels |
|-------|------|--------|
| Plugins Feature | `4218407052` | `local-only` `plugin-system` `user-facing` `agent-facing` |
| Plugin Installer | `581682073` | `local-only` `plugin-system` `user-facing` `agent-facing` |
| Skills & Apps Page / Downloads | `1834314516` | `local-only` `plugin-system` `skill-management` `user-facing` `agent-facing` |
| Plugin Scheduled Tasks (Local) | `3309093858` | `local-only` `plugin-system` `agent-facing` |

### Experimental Features

| Patch | Gate | Labels |
|-------|------|--------|
| Experimental Features Layer | `1823918333` | `local-only` `experimental` `ui-visibility` `user-facing` |

### Security & Configuration

| Patch | Gate | Labels |
|-------|------|--------|
| Sandbox Mode Config | `1488233300` | `local-only` `sandbox` `execution` `user-facing` `agent-facing` |
| Profile Sidebar Visibility | `2423536643` | `local-only` `ui-visibility` `user-facing` |

### Platform-Specific

| Patch | Gate | Labels |
|-------|------|--------|
| Appshots (Frontmost Window Capture) | `1304276663` | `local-only` `platform-macos` `screenshot` `user-facing` `agent-facing` |
| Local Host Features (Custom Avatars) | `188145323` | `local-only` `ui-visibility` `user-facing` |

### Desktop Companion

| Patch | Gate | Labels |
|-------|------|--------|
| Pet Install Modal | `1848317837` | `local-only` `desktop-companion` `user-facing` |
| Avatar Overlay (Group) | `1256703444` `1529702798` `1840974662` `4167858931` | `local-only` `desktop-companion` `user-facing` |

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

# Hindsight source bundle

`tooling/hindsight/` preserves a non-secret, chezmoi-shaped source bundle for a local Hindsight embed stack.

- It is not the live apply path or a standalone installer.
- Desired-state templates cover LaunchAgent configuration, stable client configs, service control, supervision, and cleanup helpers.
- The live machine continues to use chezmoi as the installation mechanism.
- Generated plugin state, profile environment files, control tokens, credentials, logs, archives, and runtime data must remain outside this repository.

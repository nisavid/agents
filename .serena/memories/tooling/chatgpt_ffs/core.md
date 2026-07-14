# ChatGPT FFS

`tooling/chatgpt-ffs/chatgpt-ffs` is a zero-third-party-dependency Python CLI/TUI for reversible patches to a disposable ChatGPT desktop app bundle.

- It reads and writes Electron ASAR files, applies regex-defined patches, updates ASAR integrity metadata, and re-signs the copied app with required entitlements.
- Built-in patches and label taxonomy live in the extensionless Python entrypoint; external patch examples live under `patches.d/`.
- Unit tests cover ASAR round trips, integrity encoding, patch definitions, patch state detection, and external patch labels.
- Development and validation must use another app copy under another name and isolated state. Never clobber or test against the main installed `ChatGPT.app`.
- App signing, ASAR integrity, rollback, update, and isolated-profile behavior are correctness and security boundaries, not incidental packaging details.
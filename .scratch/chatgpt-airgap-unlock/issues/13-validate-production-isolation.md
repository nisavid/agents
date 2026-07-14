Type: prototype
Status: open
Assignee: nisavid
Related: 08

## Question

Can the exact copied app complete the accepted offline workflow with hosted egress physically unavailable while preserving the vendor Chromium sandbox, signature, entitlements, and normal hardened-runtime posture?

## Acceptance

- Run on a disconnected VM or true air-gapped Apple-silicon macOS environment whose network boundary does not require modifying or interposing on the app bundle.
- Keep Chromium's vendor sandbox enabled; reject `--no-sandbox`, DYLD injection, disabled library validation, new entitlements, re-signing, or ASAR and native-code changes.
- Verify strict deep signature, nested code, ASAR, bundled Codex, architecture, entitlements, and Electron fuses before and after the run.
- Make hosted egress unavailable outside the app process while retaining only the profile-required loopback provider and gateway path.
- Prove a renderer-originated local turn completes and every observed non-loopback connection fails without receiving a disposable or provider credential.
- Record the isolation boundary, process and socket inventory, workflow result, integrity result, and complete reverse-order cleanup in red-capable evidence.

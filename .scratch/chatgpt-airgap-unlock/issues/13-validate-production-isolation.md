Type: prototype
Status: open
Assignee: nisavid
Related: 08

## Question

Can the exact copied app complete the accepted offline workflow with hosted egress physically unavailable while preserving the vendor Chromium sandbox, signature, entitlements, and normal hardened-runtime posture?

## Current state

Environment design accepted; runtime acceptance unrun.

## Decision

Run the production-isolation prototype inside a disposable Apple-silicon macOS
VM with no virtual network, socket, directory-sharing, serial/console, custom
Virtio, USB-passthrough, or audio device. Keep the exact app, model, gateway,
observers, and state inside the guest and use guest loopback as their only
network path. This preserves the vendor Chromium sandbox and signed-code posture
without changing the app or the development Mac's global packet filter.

The ticket stays open until an artifact-bound run satisfies every acceptance
condition below.

## Evidence

The environment contract, exact artifact baseline, runtime oracles, and
fail-closed acceptance procedure are recorded in
[`research/13-production-isolation.md`](../research/13-production-isolation.md).

## Acceptance

- Run on a disconnected VM or true air-gapped Apple-silicon macOS environment whose network boundary does not require modifying or interposing on the app bundle.
- Keep Chromium's vendor sandbox enabled; reject `--no-sandbox`, DYLD injection, disabled library validation, new entitlements, re-signing, or ASAR and native-code changes.
- Verify strict deep signature, nested code, ASAR, bundled Codex, architecture, entitlements, and Electron fuses before and after the run.
- Execute the manifest-bound app, model, gateway, runtime, and observers from a separately mounted read-only artifact volume; keep state and evidence on a different writable disposable volume and prove an artifact-volume write canary fails.
- Make hosted egress unavailable outside the app process while retaining only the profile-required loopback provider and gateway path.
- Prove a renderer-originated local turn completes and every observed non-loopback connection fails without receiving a disposable or provider credential.
- Record the isolation boundary, pre-command process-identity baseline, final process and socket inventory, workflow result, integrity result, and complete reverse-order cleanup in red-capable evidence; fail if any run-created process survives outside declared ancestry or process groups.

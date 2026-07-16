Type: research
Status: closed
Assignee: nisavid

## Question

Given the verified control points and capability classifications, which approach—or minimal combination of supported configuration, renderer/main-process patching, native patching, launch-time interposition, or a local bootstrap shim—best satisfies the pristine offline workflow while minimizing brittleness and trust-boundary expansion?

## Decision

Use a closed, versioned profile-driven runner around the exact pristine vendor bundle. Its private typed lifecycle owns artifact verification, isolated state, provider and authenticated gateway lifecycle, prelaunch configuration, cold start, validation, evidence, and cleanup. The local profile pins Qwen3.5-2B-OptiQ-4bit; the air-gapped GLM profile is explicit and has no fallback. The source bundle and installed application remain immutable. The sole exception is Ticket 14's exact-build-bound, fixed-width native-picker seam in a separately named disposable validation copy; it is not an implementation artifact or a production application build. Apart from that exception, no renderer, main-process, ASAR, native, entitlement, fuse, updater, account-shim, or runtime-interposition change is justified. The immutable design comparison and contract are published at [research/07-preferred-architecture.md](https://github.com/nisavid/agents/blob/78ebf424ccbb2012eb946e6fd7818697f6a91ed4/.scratch/chatgpt-airgap-unlock/research/07-preferred-architecture.md).

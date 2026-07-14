# ChatGPT Air-Gap Unlock

This context names the concepts used to decide which parts of the minimum offline Codex workflow can exist without an OpenAI session or hosted OpenAI service.

## Language

**Offline baseline**:
The exact app build, isolated local state, self-hosted model provider, and network-denied operating boundary used for this effort.
_Avoid_: Patched app, normal installation

**Minimum offline workflow**:
The smallest useful local Codex journey: open a local project, create or select a worktree, run and resume local threads through the configured model provider, select a mode, handle permissions, and use local extensions.
_Avoid_: Fully featured ChatGPT, every visible app feature

**OpenAI session**:
An authenticated ChatGPT or OpenAI account state. It is distinct from a credential for the local model provider.
_Avoid_: Login, auth, provider key

**Local provider credential**:
A secret that authorizes requests to the self-hosted model provider and grants no OpenAI account authority.
_Avoid_: OpenAI session, bootstrap token

**Capability surface**:
A visible control or callable interface that offers a behavior but does not by itself prove that the behavior works.
_Avoid_: Capability, feature

**Backing behavior**:
The local or hosted system that must complete the behavior offered by a capability surface.
_Avoid_: UI, gate

**Capability control**:
The policy value, configuration value, route condition, or local interface that admits or selects a capability.
_Avoid_: Feature flag when the control is not a feature flag

**Inherently local**:
A capability whose required backing behavior is present in the offline baseline and needs no protocol substitute. Local files, credentials, and first-run initialization do not make it non-local.
_Avoid_: Works without setup

**Locally recoverable**:
A capability whose required backing behavior can be completed by a narrow local compatibility boundary without reproducing an unavailable hosted service.
_Avoid_: Patched, hosted emulation

**Degraded but usable**:
A capability whose useful local core remains available while some discovery, metadata, presentation, or optional extension behavior is absent.
_Avoid_: Fully working, broken

**Hosted-only**:
A capability whose backing behavior is the unavailable hosted service itself, so exposing its surface or fabricating local account state cannot make it work.
_Avoid_: Hidden, merely gated

**Initialization**:
The local state that must exist before first launch or first use so the capability enters its supported offline path.
_Avoid_: Bypass

**Evidence grade**:
The strength of the exact-build observation supporting a classification, with runtime evidence stronger than static ownership or protocol-shape evidence.
_Avoid_: Confidence score

Type: research
Status: closed
Assignee: nisavid
Blocked by: 02, 03, 04

## Question

For each capability in the minimum viable offline Codex workflow, what gate and backing dependency controls it in the exact app build, and should it be classified as inherently local, locally recoverable with a narrow shim, degraded but usable, or impossible without an unavailable hosted service?

## Resolution

[Exact-build capability classification](https://github.com/nisavid/agents/blob/283511d244ab5974c4626730735312b70f7a421e/.scratch/chatgpt-airgap-unlock/research/05-minimum-offline-workflow.md) finds the minimum workflow predominantly local. A cold file-configured provider truthfully supplies no-auth startup; projects, worktrees, threads, modes, permissions, local skills/plugins, and their config have local control planes and backing state. Provider protocol adaptation, model metadata, GUI secret delivery, and extension-specific dependencies remain bounded validation or initialization work. No account shim is justified. Source and installed vendor bundles remain immutable; only the reviewed native-picker seam may mutate a separately named disposable validation copy.

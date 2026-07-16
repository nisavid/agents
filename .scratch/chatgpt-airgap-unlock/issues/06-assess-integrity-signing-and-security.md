Type: research
Status: closed
Assignee: nisavid

## Question

What macOS code-signing, hardened-runtime, ASAR integrity, native-library, auto-update, credential-isolation, and supply-chain constraints apply to each plausible modification surface, and what operational controls must a safe reversible offline solution preserve?

## Resolution

[Exact-build assessment](https://github.com/nisavid/agents/blob/59e9fa5800b2806064236d1bab0e5f5845681e96/.scratch/chatgpt-airgap-unlock/research/06-integrity-signing-security.md) establishes build `5263` provenance, signatures, 60-object signed Mach-O graph, entitlements, ASAR hashes, Electron fuses, and Sparkle trust metadata. Prefer supported configuration or an authenticated loopback service that leaves the vendor bundle pristine. Any bundle mutation must target a separately derived copy, preserve per-object least privilege, fail closed on complete strict verification, bind state and backups to the exact artifact, and roll back by replacing the whole copy from the verified vendor archive. The current patcher does not satisfy those constraints.

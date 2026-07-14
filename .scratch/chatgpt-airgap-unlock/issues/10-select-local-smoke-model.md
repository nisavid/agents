Type: research
Status: open
Assignee: nisavid

## Question

Which sufficiently small, tool-calling local model and Apple-silicon runner can replace the unavailable air-gapped GLM endpoint for local development and smoke validation, preferring a published OptiQ quantization and `optiq serve`, with MLX-format `mlx_lm` as fallback?

## Acceptance

- Compare current candidate artifacts by architecture, quantization, download and working-memory size, license, tool-calling behavior, context support, and runner compatibility.
- Prefer the smallest credible OptiQ model; use MLX-LM only when no suitable OptiQ artifact exists.
- Download and verify the selected immutable model revision without using real credentials.
- Run it locally through the corresponding inference server with loopback-only binding.
- Prove a minimal chat response and at least one deterministic tool-call round trip through an OpenAI-compatible API.
- Record the exact model revision, runner version, launch configuration, observed memory/latency, protocol gaps, and cleanup/restart procedure.
- Do not use the production ChatGPT app, real profile, OpenAI state, Systalyze systems, or the unavailable air-gapped GLM endpoint.

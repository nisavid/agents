Type: implementation
Status: closed
Assignee: nisavid

## Question

Can `tooling/codex-ns-proxy` become the profile-owned authenticated loopback gateway between bundled Codex and either local OptiQ or the explicit air-gapped GLM endpoint, while preserving the exact Responses stream contract and removing unsafe provider-specific defaults and diagnostics?

## Acceptance

- Require an explicit upstream URL; remove every organization-specific endpoint default and never contact an upstream during import, validation, or startup.
- Bind to loopback by default and reject a non-loopback listener unless a separately reviewed profile explicitly permits it.
- Require a generated per-run inbound bearer, compare it without logging it, and reject missing or wrong credentials before reading or forwarding a request body.
- Keep inbound and upstream credentials separate. Apply an optional upstream bearer only at the upstream seam and never persist either value.
- Restrict forwarding to the configured upstream origin and the Responses paths and methods required by bundled Codex.
- Preserve namespace-tool request flattening and response reconstruction with deterministic tests for simple, namespace, history, tool-call, and function-output cases.
- Preserve Responses SSE event order and payloads except for evidence-backed namespace reconstruction. Observe and test terminal `response.completed`, cancellation, upstream errors, and connection cleanup without inventing usage or model metadata.
- Disable request/response body dumps and verbose logging by default. Any diagnostic mode must redact authorization, prompt, tool arguments, and generated text before persistence.
- Add a red-capable local test suite for authentication, allowlisting, transformations, streaming, failure handling, cleanup, and non-leakage.
- Validate the exact proxy commit between bundled Codex `0.144.2` and the pinned Qwen3.5-2B OptiQ snapshot under the Ticket 08 isolation boundary.
- Keep the production ChatGPT app, normal profile, global Codex configuration, Systalyze systems, and the unavailable air-gapped GLM endpoint untouched.

## Evidence

- `tooling/codex-ns-proxy` requires explicit upstream and separate inbound and upstream credentials, binds to loopback by default, restricts routes and methods, authenticates before reading request bodies, preserves Responses streaming, and fails closed for unsupported namespace transformations.
- `tooling/codex-ns-proxy/tests/test_proxy.py` covers authentication, allowlisting, transformations, continuation state, streaming, HTTP framing, failure handling, shutdown, cleanup, and non-leakage.
- The exact reviewed gateway commit `7c960b15267e82ef5d5a854bdd54bf53fb9e8135` passes the Ticket 08 end-to-end route against bundled Codex `0.144.2` and the pinned Qwen3.5-2B OptiQ snapshot. See [research/08-validate-preferred-route.md](../research/08-validate-preferred-route.md).

Type: research
Status: closed
Assignee: nisavid

## Question

How does the exact app build discover, configure, validate, and invoke the self-hosted GLM 5.2 provider across its UI and bundled Codex host, and where—if anywhere—does an OpenAI authentication or remote-bootstrap prerequisite block that otherwise local path?

## Resolution

[Exact-build provider trace](https://github.com/nisavid/agents/blob/81bd2bfbff803ad50c7d4111ca00a062953d89fc/.scratch/chatgpt-airgap-unlock/research/04-glm-provider-path.md) proves that bundled Codex `0.144.2` can cold-start with a file-configured `glm-5.2` provider, return `account: null` with `requiresOpenaiAuth: false`, create a local thread, and invoke the provider's loopback `/responses` endpoint using only its own credential. This build has no general custom-provider editor, requires a cold app/host restart after provisioning, and requires the Responses API; a non-compatible GLM service needs a separately scoped local shim.

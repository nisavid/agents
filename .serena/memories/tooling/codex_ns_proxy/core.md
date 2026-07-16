# Codex namespace proxy

`tooling/codex-ns-proxy/codex-ns-proxy.py` is a standard-library HTTP proxy that adapts Codex namespace tools for providers that accept ordinary function tools.

- Request transformation flattens namespace tool groups and prior function-call items.
- Response transformation splits flattened call names back into Codex's name and namespace fields.
- The listener defaults to loopback. The upstream endpoint is configurable through `NS_PROXY_UPSTREAM`.
- Do not commit provider credentials or secret-bearing request dumps.
- Protocol changes require both request-side and response-side validation, including streaming behavior when affected.

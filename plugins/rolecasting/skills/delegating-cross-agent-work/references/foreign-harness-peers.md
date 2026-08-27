# Surface And Peer Qualification

Read this reference after selecting a non-native, separately owned, or
foreign-harness peer. Surface support is portable policy; it is not evidence
issuance. Every real dispatch still requires qualification of the exact family,
surface, version, executor, transport, and assurance source.

## Planned surfaces

| Order | Product surfaces | Qualification state |
| --- | --- | --- |
| Initial | ChatGPT Codex; Codex CLI/TUI | Supported separately for ordinary delegation; publication-grade issuance pending |
| Fast follow | Claude Code; Claude Desktop | Separate paired surfaces; real qualification pending |
| Next | Cursor; Cursor Agent | Qualify together or in close succession |

ChatGPT Codex and Codex CLI/TUI have separate initial native-child adapter
profiles. Follow
[native-codex-subagents.md](native-codex-subagents.md) for their shared
freeze, native invocation, and post-result recording sequence. This is
operational skill support, not a registered evidence issuer.

Do not collapse a paired product into one execution surface. ChatGPT Codex and
Codex CLI/TUI require separate probes. Claude Code and Claude Desktop require
separate probes. Cursor and Cursor Agent require separate probes. An
`app-server` transport does not imply an `external` relationship; classify the
control boundary independently.

Before dispatch, probe the installed surface:

- ChatGPT Codex: inspect the current native tool or task API schema.
- Codex CLI/TUI: `codex --version`; `codex exec --help`; inspect current tool
  schemas and `app-server` help only when that transport is selected.
- Claude Code: `claude --version`; `claude --help`; inspect current tool schemas.
- Claude Desktop: inspect its current native or app-server schema.
- Cursor: inspect its current native surface schema.
- Cursor Agent: `agent --version`; `agent --help`.

Trust local help and schemas over memory. A probe fails when the executable is
absent, it exits nonzero, its output is malformed or unparseable, or the surface
cannot isolate the requested scope and authority. Record the surface, probe,
and result; never invent a dispatch or capability.

Use a native fallback only when it preserves the original task contract. If it
does, omit unsupported model and effort fields and route any unresolved
selection through `choosing-agent-models`. Otherwise, omit model and effort
selection and do not dispatch: return `NEEDS_CONTEXT` with the missing fact when
more evidence could establish a safe surface, or `BLOCKED` with probe evidence
when no permitted route can satisfy the contract.

Bind the session, scope, worktree, base, write and subdelegation permissions,
external-action authority, verification, return shape, and stop conditions. Use
the least-permissive surface and preserve existing edits. On `NEEDS_CONTEXT` or
`BLOCKED`, change the input, authority, boundary, or capability. Broaden
authority or permissions only with explicit operator or repository
authorization. The leader reviews and integrates every result.

## Task And Thread Isolation

A peer, sibling, companion, or dedicated task is newly created for the current source task and bounded scope unless the operator explicitly identifies an existing same-purpose companion. Do not message, fork from, steer, or execute current-task work through an unrelated user-owned task. Route-metadata inspection does not make that task eligible or authorize executing with its model or under its account, entitlement, permissions, or context. Model routing supplies no task-creation or steering authority; creating or steering a user-owned task requires explicit consent. If an isolated task cannot be created or verified, return `NEEDS_CONTEXT` or `BLOCKED`; unrelated-task reuse is never a fallback.

For a read-only dispatch, prove that each selected execution has a distinct
session and isolated context, immutable input delivery, enforced read-only
access, and default-denied subdelegation and external-action authority. A
foreign surface that cannot enforce every bound restriction is not an
equivalent route. Record the failed capability and do not dispatch it.

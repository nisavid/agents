# Foreign-Harness Peers

Read this reference only after selecting a foreign-harness peer.

Before dispatch, probe the installed surface:

- Cursor Agent: `agent --version`; `agent --help`
- Claude Code: `claude --version`; `claude --help`; `claude agents --help`
- Codex: `codex --version`; `codex exec --help`; inspect current tool schemas

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

For a read-only dispatch, prove that each selected execution has a distinct
session and isolated context, immutable input delivery, enforced read-only
access, and default-denied subdelegation and external-action authority. A
foreign surface that cannot enforce every bound restriction is not an
equivalent route. Record the failed capability and do not dispatch it.

# Task Witness

Task Witness is a code-only validation package with a manifest-only Agent
Plugin identity. It has no portable skill or MCP surface. The current candidate
supports source-stage contract validation only and is production-ineligible.
That validation executes candidate-owned validation modules only after the
operator reviews and content-pins the public checkout; it grants no runtime or
release authority.

The current executable client, native qualification, and final-release routes
fail closed before they parse private evidence paths or execute candidate code.
The identity-pinned bridge and freeze snapshots remain evidence-only historical
inputs; no current route selects them as executable entrypoints.
The TW4 suite inventory is retained as a source-design record with
`runtime_status: retired-source-stage`; its advertised suite commands are not
current executable routes.

Restoring a runtime route requires external controls that this repository does
not provide: review authorization bound to the candidate bytes, an installed
host-owned and content-pinned OS sandbox with networking denied, opaque
inherited handles for private evidence, and authenticated receipts backed by
managed signing-key custody. Until those TW4 gates close, the package remains
`production_eligible: false`.

The normative contracts are the
[canonical client and deployment design](../../docs/superpowers/specs/2026-07-27-task-witness-canonical-client-design.md)
and the
[TW4 migration and qualification design](../../docs/superpowers/specs/2026-08-12-task-witness-tw4-migration-and-qualification-design.md).

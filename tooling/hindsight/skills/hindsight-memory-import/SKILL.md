---
name: hindsight-memory-import
description: Use when inspecting, projecting, validating, planning, resuming, or reconciling imports from curated Codex memories, Claude memory files, or portable Markdown or JSONL manifests into the managed Hindsight memory system.
---

# Hindsight Memory Import

Use the managed controller workflow. Never retain directly to Hindsight, call a
data-plane endpoint, or treat a novelty or deduplication heuristic as approval.

## Workflow

1. Confirm the source set and target profile/bank policy. Prefer curated memory
   files. Treat raw transcript streams as a separate offline novelty pass.
2. Inspect file-backed Codex, Claude, portable Markdown, and portable JSONL
   sources with `hindsight_memory_control_plane.importing.inspect_source` so
   the adapter derives descriptor-bound file and line provenance. Use
   `inspect_items` only for records whose source locator, native identity, and
   line provenance were already established by a trusted adapter. Reject
   malformed, secret-like, unprovenanced, or unsupported records rather than
   repairing them silently.
3. Project with `project_import`. Preserve stable source-native identity,
   timestamp, exact file/line provenance, deterministic tags, intended scope,
   relationship hints, and one coverage disposition for every source item.
4. Validate the canonical projection and review every proposed novel,
   duplicate, conflict, and omission disposition. Reordering input must not
   change the projection digest.
5. Build the import plan with `build_import_plan`, binding it to the current
   controller plan digest and exact canonical target bank.
6. Stop before apply. Show the exact import plan digest, coverage summary,
   target, and unresolved proposals. Wait for explicit approval of that exact
   digest.
7. If and only if the exact plan is approved, pass it through
   `apply_import_plan` to the controller apply gate. Do not bypass rollback,
   live-state, endpoint, operations-idle, or migration-completion gates. Accept
   resume state only when it binds the exact approved import-plan digest, the
   exact canonical target bank, and each completed item identity to its full
   canonical `import_item_digest`. Otherwise re-inspect the items.
8. Reconcile receipts with `reconcile_import` only when each receipt binds the
   exact approved import-plan digest, canonical target bank, item identity, and
   item digest. Report
   missing, changed, conflicted, or omitted items; do not infer completion.

## Hard Boundaries

- Keep source content and secrets out of controller ledgers and chat summaries.
- Resume state skips an item only when its envelope matches the exact approved
  import-plan digest and exact canonical target bank and its entry matches the
  stable identity and full canonical `import_item_digest`. Legacy content-only
  digests are intentionally invalidated and re-inspected; never accept them as
  weaker compatibility.
- Never convert a proposed disposition into a mutation without the approved
  digest-bound plan.
- While the migration gate is open, do not issue controller-directed retain or
  import (including template import), consolidate, model refresh,
  configuration mutation, or deletion. Ambient active-bank auto-retain remains
  enabled and is permitted; do not disable or freeze it merely because the
  migration gate is open.

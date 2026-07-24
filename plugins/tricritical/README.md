# Tricritical plugin

Tricritical is one portable review core for Claude Code and Codex-like harnesses. It separates independent observation from adjudication, authorized revision, and repetition.

## Public skills

| Skill        | Persona      | Role        | Mutates directly | Can cause mutation | Requires original mutation authority | Repeats | Calls                      |
| ------------ | ------------ | ----------- | ---------------- | ------------------ | ------------------------------------ | ------- | -------------------------- |
| `review`     | —            | coordinator | no               | no                 | no                                   | no      | intent, runtime, structure |
| `intent`     | Oathfinder   | critic      | no               | no                 | no                                   | no      | —                          |
| `runtime`    | Faultwalker  | critic      | no               | no                 | no                                   | no      | —                          |
| `structure`  | Knotcutter   | critic      | no               | no                 | no                                   | no      | —                          |
| `adjudicate` | Claimweigher | adjudicator | no               | no                 | no                                   | no      | —                          |
| `revise`     | Formwright   | reviser     | yes              | yes                | yes                                  | no      | —                          |
| `loop`       | Fathomkeeper | loop        | no               | yes                | yes                                  | yes     | review, adjudicate, revise |

## Adapter boundary

`skills/` is the shared semantic core. The single shared review-input policy is
in `references/review-input-boundary.md`; every public skill loads it directly.
Claude auto-discovers root `agents/*.md`; each file is an
exact minimal forwarder to one public skill. Codex consumes `skills/` and each
skill's `agents/openai.yaml`, but not root Claude agents. A Codex named-agent
projection belongs to a local harness configuration and must call the public
skill rather than copy its policy.

Semantic skill-to-skill edges use literal public identities linked to sibling
`SKILL.md` files and remain relative and unqualified. Claude adapters, the Codex
manifest, and `agents/openai.yaml` prompts use `$tricritical:<skill>` targets.
The review coordinator's two topology requirements are adapter inputs rather
than skill edges. A harness adapter supplies a capability-proven model-selection
receipt for every distinct dispatch and a separate Rolecasting-issued
`adapter:rolecasting-invocation-topology-receipt` for the frozen plan. The latter
binds the immutable candidate/review-input/requirements identities and exactly
one unique, isolated, read-only entry for every selected critic and specialist.
It is a closed-world dispatch set with subdelegation and external action denied
by default; a user-owned task requires explicit user authority. Portable review
policy validates both receipts without copying provider-specific model policy,
and any fallback requires a new valid plan.

`topology.json` is the sole machine-readable authority and dependency
declaration. The table above and sibling skill links are validated projections
of that file. Its `schema_version` versions Tricritical's topology shape; it is
not a repository-wide interchange-schema version.

The raw evaluation corpus and its isolation protocol are documented in
`evals/README.md`. Run
`uv run --with PyYAML python scripts/validate_tricritical.py` from the
repository root after changes. That validator proves structural and corpus
declarations only; it does not enforce an external evaluation runner or prove
LLM behavior. It validates a private immutable byte snapshot and reports that
snapshot's identity. Run it only while no concurrent writer can modify the checkout;
live-tree drift detection remains an additional operational gate, not the
identity of the validated snapshot.

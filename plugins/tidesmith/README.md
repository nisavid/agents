# Tidesmith plugin

Tidesmith is an Agent Plugins v1 package for prose that agents write to
people: the register a reader should meet, the evidence discipline behind
every claim in that prose, the post-draft edit passes that enforce style as
actions rather than preferences, and the adversarial pass that reads a finished
draft as its recipient before it ships. Claude Code uses a native manifest
projected from the same canonical package identity.

Writing standards are portable equipment here, not personal taste: a consumer
without the author's own global instructions still receives the mechanics.
Personal voice composes on top through consumer-global instructions and never
lives in this package.

Tidesmith owns generic human-facing prose mechanics only. Surface owners keep
their surface contracts: Mergecraft owns reviewer-facing pull-request text and
the review-voice reference that applies this register to review threads, and
Tricritical owns review findings and their structural output contract. Those
packages route to Tidesmith for prose mechanics; projections do not cross
package boundaries.

## Public skills

<!-- BEGIN GENERATED SKILL REGISTRY -->
| Skill | Owns | Calls |
| --- | --- | --- |
| `writing-for-people` | human-facing-register, evidence-in-prose, post-draft-edit-pass | - |
<!-- END GENERATED SKILL REGISTRY -->

## Adapter boundary

Root `plugin.json` and `skills/` are the canonical Agent Plugins package;
`.claude-plugin/plugin.json` is a projection of the same identity. Skill-to-skill
edges use literal public identities linked to sibling `SKILL.md` files. Codex
adapters and each skill's `agents/openai.yaml` use `$tidesmith:<skill>` targets.
The registry above is generated from `topology.json` by the content-lock writer.

## Validation

`python3 scripts/validate_tidesmith.py .` validates the package from the
repository root: manifest identity and projection, topology, skill contracts,
the generated registry, inventory, portability, and the semantic content lock.
`--write-content-lock` regenerates the registry block and the lock after an
authored change. The package declares no Task Witness provider yet; publication
eligibility and release-slate membership are decided outside this package.

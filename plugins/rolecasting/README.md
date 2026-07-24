# Rolecasting plugin

Rolecasting is a dual-harness plugin for choosing bounded worker lifecycles and
selecting an executable model and reasoning effort after delegation is settled.

It does not decide repository ownership, review disposition, publication, or
pull-request work.

## Skills

| Public name                                | Responsibility                                                                                                                             |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `$rolecasting:delegating-cross-agent-work` | Choose native-subagent, explicitly user-owned Codex task, or foreign-harness peer topology; bind authority; and retain leader integration. |
| `$rolecasting:choosing-agent-models`       | Select a model-effort pair proven by the live-catalog and target-executor-schema intersection after delegation is settled.                 |

## Layout

```text
plugins/rolecasting/
├── .claude-plugin/plugin.json      # Claude adapter manifest
├── .codex-plugin/plugin.json       # Codex adapter manifest
├── content-lock.json                # Generated semantic and rubric snapshot
├── evals/delivery.json              # Isolated executor/grader delivery contract
├── skills/                          # Shared semantic core, references, and twelve evals
└── topology.json                    # Authoritative ownership and call graph
```

The manifests and `skills/*/agents/openai.yaml` are thin adapter surfaces.
Skill discovery supplies the `rolecasting:` namespace; the semantic bodies use
plugin-relative links and keep detailed capability and peer guidance deferred.
The `schema_version` in `topology.json` versions Rolecasting's local topology
shape, not a repository-wide interchange schema.

## Validation

From the repository root, run:

```sh
uv run --with PyYAML python scripts/validate_rolecasting.py
uv run --with PyYAML python -m unittest tests/test_validate_rolecasting.py
python3 -m unittest tests/test_rolecasting_eval_corpus.py
```

The validator rejects duplicate keys and non-finite JSON, malformed discovery
YAML, invalid CLI arity, component drift, non-portable content, adapter
namespace drift, graph invariant violations, semantic-lock drift, grader-answer
leakage, and lexical ancestor symlinks.

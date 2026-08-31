# Rolecasting plugin

Rolecasting is an Agent Plugins v1 package for choosing bounded worker
topology and selecting an executable model and reasoning effort after
delegation is settled. Claude Code uses a native manifest projected from the
same canonical package identity.

Delegation freezes independent dimensions: target family, surface, version,
and executor; child, peer, or external relationship; leader-owned or user-owned
ownership; native-tool, task-api, CLI, app-server, or remote-API transport; and
the observed and consumer-minimum product-attested, controller-observed, or
self-reported assurance for each evidence dimension. Model choice remains
separate. A transport never determines the
relationship, and a user-owned peer requires explicit user consent.

Every payload-bearing new dispatch, follow-up, resume, retry, capacity
recovery, or reclassification passes one pure model-transition guard. Its
content-addressed decision carries task identity, the prior judgment floor,
sticky operator choice, Daybreak requirement, the complete permitted-route
inventory and metadata-only status preflight, fresh selector and capability
evidence, separate execution authority, account binding, capacity, and the
exact selection. Native and portable paths reject missing, denied, stale,
downgraded, or cross-bound decisions at their payload boundaries.

Initial operational support uses separate native-child adapter profiles for
ChatGPT Codex and Codex CLI/TUI. Both freeze the complete plan and assurance
minima before native spawn and bind same-leader launch, status, and result
observations afterward. A separate verification digest and strict Boolean
preserve completed-but-blocked or unverified results as unusable. Their current
maximum assurance is controller-observed for target, topology, and result, and
self-reported for model and effective authority. Claude Code and Claude Desktop
follow, then Cursor and Cursor Agent together or in close succession.
Operational support is not evidence issuance:
publication-grade producer and issuer qualification remains pending for every
exact surface, version, executor, transport, and assurance source.

It does not decide repository ownership, review disposition, publication, or
pull-request work.

## Skills

| Public name                                | Responsibility                                                                                                                             |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `$rolecasting:delegating-cross-agent-work` | Freeze target, relationship, ownership, transport, assurance, and authority; batch same-shape work, use bounded waits, and retain leader integration. |
| `$rolecasting:choosing-agent-models`       | Select a model-effort pair proven by the live-catalog and target-executor-schema intersection after delegation is settled.                 |

## Layout

```text
plugins/rolecasting/
├── plugin.json                     # Canonical Agent Plugins v1 manifest
├── .claude-plugin/plugin.json      # Validated Claude identity projection
├── task-witness-provider.json      # Dispatch-evidence owner registration
├── content-lock.json               # Generated semantic and rubric snapshot
├── evals/delivery.json             # Isolated executor/grader delivery contract
├── skills/                         # Skills, owner validator, references, and evals
└── topology.json                    # Authoritative ownership and call graph
```

The root manifest is the sole package identity and carries Codex presentation
metadata under `extensions.com.openai.interface`. The Claude manifest is an
exact projection of the common identity fields with only Claude's
`displayName` added. The single `skills/` tree is portable; Codex selectively
reads each preserved `skills/*/agents/openai.yaml` interface. Skill discovery
supplies the `rolecasting:` namespace, and every relative runtime resource
stays inside the package. The `schema_version` in `topology.json` versions
Rolecasting's local topology shape, not a repository-wide interchange schema.

Rolecasting's registered owner validator accepts a strict, generic dispatch
bundle and returns `rolecasting-dispatch-projection-v3`. Its pure renderer
cannot authenticate a selected executor, and no authenticated owning harness
integration is currently bound into the issuer identity. The registered
provider therefore declares empty producer and issuer inventories and retains
only the active validator plus the exact transition guard and renderer modules.
Task Witness remains
the only front door, and current publication attempts fail closed:

```text
task-witness validate --bundle <absolute-bundle>
```

The owner interface is documented in the delegating skill's
`dispatch-evidence.md` reference. Rolecasting exposes no trust-path or runtime-
path CLI and does not interpret review or publication semantics. Its separate
native adapter deterministically freezes supplied, already-observed execution
facts; it does not launch workers or claim to authenticate observations the
harness has not supplied. Its `rolecasting-bootstrap-adapter-v3` issuer contract
is independent of the dispatch-evidence bundle contract. Private tests may
construct test-owned bootstrap trust to exercise historical validation, but no
Rolecasting historical trust has been installed and those results cannot
become canonical publication evidence. The skill-mediated native Codex module
sequences pre-spawn freeze and post-result same-leader recording, but it is not
a harness actuator and emits neither portable evidence nor product
attestation. New publication stays blocked until a real native harness
integration authenticates execution and its exact owning bytes are bound into
a newly registered producer/issuer identity.
Canonical publication specifically requires a product-attested ChatGPT Codex
child. Ordinary consumers require controller-observed assurance for every fact
they rely on. Self-reported dimensions are diagnostic and non-gating; the
native route is usable only when model and effective authority are not gate
inputs.
The validator preserves both clean and nonclean execution evidence: `usable` is
a strict Boolean carried into every execution projection, and `false` remains a
valid result rather than being discarded.

## Validation

From the repository root, run:

```sh
uv run --with PyYAML python scripts/validate_rolecasting.py
uv run --with PyYAML python -m unittest tests/test_agent_plugins_standard.py
uv run --with PyYAML python -m unittest tests/test_validate_rolecasting.py
python3 -m unittest tests/test_rolecasting_eval_corpus.py
python3 -m unittest tests/plugins/test_rolecasting_dispatch_evidence.py
python3 -m unittest tests/plugins/test_rolecasting_native_codex.py
python3 -m unittest tests/plugins/test_rolecasting_model_transition.py
```

The validator rejects Agent Plugins schema drift, duplicate keys and non-finite
JSON, malformed discovery YAML, invalid CLI arity, non-direct skill discovery,
resource escapes, component drift, non-portable content, projection or adapter
namespace drift, graph invariant violations, semantic-lock drift, grader-answer
leakage, and lexical ancestor symlinks.

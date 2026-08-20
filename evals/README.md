# Phase 1 control-plane behavior evaluation

`control-plane-matrix.json` is the public, declarative inventory for the 21
Rolecasting, Tricritical, Versionkeeping, and Mergecraft skills. It selects one
existing raw scenario per skill and declares only the companion skills allowed
in that skill's composed condition. It is Phase 1 four-condition behavior
evidence, not proof that every publication, deployment, hook, or runtime
control-plane action is integrated correctly. Those claims need their own
end-to-end evidence.

Validate the definition without using a model:

```sh
python3 scripts/run_control_plane_eval.py validate-definition
```

The runner requires an explicit incumbent snapshot mapping. This keeps
machine-specific managed-skill paths out of the repository:

```json
{
  "schema_version": 1,
  "skills": {
    "rolecasting:choosing-agent-models": {
      "root": "/absolute/snapshot/root",
      "entrypoints": ["choosing-agent-models/SKILL.md"],
      "declared_calls": [],
      "repository": "managed://installed-snapshot",
      "revision": "snapshot-identity"
    }
  }
}
```

The approved production executor and grader IDs for this definition are
`claude-sonnet-5` and `claude-opus-4-8`. Pass those exact IDs together with the
exact Claude CLI executable under evaluation. The runner binds the requested
IDs, every observed init and assistant ID, the executable's path, bytes,
version, and command configuration. Every coordinate is checkpointed
independently, so the same command resumes instead of replaying completed paid
calls:

```sh
python3 scripts/run_control_plane_eval.py run \
  --adapter claude \
  --credential-mechanism anthropic-api-key-fd \
  --endpoint-policy anthropic-public-api \
  --incumbents /absolute/path/to/incumbents.json \
  --output /absolute/path/to/evidence \
  --candidate-repository https://github.com/nisavid/agents \
  --candidate-revision '<frozen-revision>' \
  --claude-executable /absolute/path/to/claude \
  --executor-model claude-sonnet-5 \
  --grader-model claude-opus-4-8
```

A trusted launcher must map the Anthropic API key to inherited descriptor `9`
without placing the credential or its pathname in the runner's arguments or
environment. The runner rejects ambient credential mechanisms.

For model-backed runs, the candidate revision must equal the clean checkout's
exact Git `HEAD`, and the evidence directory must be outside that checkout.
The runner archives that exact Git tree to a private temporary snapshot before
it reads a scenario or skill byte; it rechecks the commit and tree identity, and
the gate binds the retained source archive, its digest, normalized origin, and
Git IDs. The incumbent mapping must likewise carry a verified immutable
`full_tree_lock_sha256` for each installed snapshot.

The Claude executable must be an explicit absolute path. Every runtime identity
probe runs in a fresh, credential-free closed environment, and every provider
call receives fresh private home, config, cache, data, state, temporary, and
working roots. Calls use an exact closed environment and disable tools, skills,
slash commands, MCP, and session persistence. Ambient executable lookup, proxy,
provider-base-URL, loader, routing, configuration, and credential variables do
not reach either boundary. The gate binds the credential mechanism, endpoint
policy, probe/call environment policy, and provider initialization credential
source to the evidence.

The endpoint policy permits only the provider's public default endpoint and
denies proxy configuration. It records policy identity; it does not impose an
OS network sandbox. The evidence attests only to observed invocation and
init-stream capability surfaces. It does not claim hidden provider context or
a provider request ID. The harness generates a local correlation ID, while
response and session IDs are bound to the retained raw provider stream. Every
request, raw transport stream, parsed response, identity record, config, seed,
isolation record, and relevant digest is gate-checked.

Provider stderr never enters public errors, logs, or evidence. Each attempt
stores the exact stderr bytes only in its owner-private `0600` raw artifact
under the evidence directory's `0700` attempt store. Public failure records
contain only the stream digest, byte length, provider role, status, and attempt
identity.

Retained structural checks bind the selected runtime bytes and policy but do
not authenticate their external authorization. The shipped evaluation-gate
executable therefore rejects every invocation before reading evidence and
grants no evaluation or release authority in this source-stage release.

The production target contract requires 252 current executor coordinates:
21 skills × 4 conditions × 3 repeats. Its required invalidation drill first
runs a deterministic evaluation-only preimage for the choosing/delegating
reverse-dependency slice, grades it, retains the superseded executor, grader,
and blinding-plan checkpoints, then runs the canonical input slice and grades
it again. A completed production manifest must also contain 21 current graders,
separate from those superseded records. These are validation requirements, not
a claim that a retained production run already exists. Checkpoints are atomic
and resumable; a provider call that finishes after a local timeout but before
its checkpoint is durably written can be replayed on manual resume. The runner
never claims exactly-once provider billing or silently double-counts a response.

For an offline end-to-end pipeline test, use the fixture adapter with
`--scenario-limit` set to a proper subset. Fixture transport cannot emit a
production-sized matrix, and limiting is rejected for model transports. The
fixture path still writes deterministic full-tree archives, raw executor
responses, blinded grader batches, an explicit invalidation/replacement event,
adjudications, aggregates, and a schema-v2 manifest for retained structural
tests.

## Phase 2 observable routing

`skill-routing-matrix.json` is a separate routing tier. It leaves the 252-run
semantic target unchanged. Its definition derives a 113-call production target:
21 cold-start cases + 21 explicit invocations + 71 trigger cases. The trigger
tier retains all 33 existing trigger cases and adds one positive and one
negative case for each of the other 19 skills. This count describes the gate;
it does not assert that the paid calls have been completed.

Production routing evidence uses Claude's first-class `Skill` tool and retains
the raw JSONL stream, init inventory, model and usage accounting, every failed
or successful attempt, identity, fresh home/config/cache/data/state/temporary
isolation record, command configuration, and frozen clean candidate archive. It
proves observed Claude tool use only; it does not claim that Codex skill
selection is directly observable. Fixture mode is limited to a proper subset
and makes no paid provider calls.

The approved production routing model ID is `claude-sonnet-5`. The runner also
binds the exact Claude CLI path, bytes, version, requested ID, and observed init
and assistant IDs. The production release validator consumes this external
evidence plus the external composed public/private receipt and requires an
absolute external `--receipt-output`. It writes the private release receipt
only after every release gate passes; source-stage validation does not accept
or produce one. The receipt records evidence hashes and accounting summaries,
not the retained prompts, model outputs, credentials, or local evidence paths.

```sh
python3 scripts/run_skill_routing_eval.py \
  --adapter claude-cli \
  --model claude-sonnet-5 \
  --output /absolute/path/to/routing-evidence \
  --candidate-revision '<frozen-revision>'
```

## Artifact Customs behavior

`artifact-customs/corpus.json` covers direct behavior, discoverable cold-start
routing, out-of-scope negative routing, scheduled autonomy consent, and every
pre-write identity rebind family. Fixture mode is provider-free transport
evidence only. Null-skill scenarios receive the complete discoverable
Artifact Customs skill set without a preselected target.

Provider mode requires separate executor and grader commands. Each command must
contain exactly one standalone `{model}` argument, which the runner replaces
with the operator-selected model identity and binds into the command digest.
Both commands accept one JSON request on standard input; the executor emits
UTF-8 response text, and the grader emits exactly
`{"passed": <boolean>, "failures": [<string>, ...]}`.

```sh
python3 scripts/run_artifact_customs_eval.py \
  --scenario governed-advisory-cold-start \
  --adapter provider \
  --executor-command '/absolute/path/to/executor --model {model}' \
  --executor-model '<exact-executor-model>' \
  --grader-command '/absolute/path/to/grader --model {model}' \
  --grader-model '<exact-grader-model>' \
  --output /absolute/path/to/artifact-customs-evidence.json
```

The executor and grader run as separate processes in separate new private
temporary roots with explicit working directories, bounded input/output and
runtime, and scrubbed minimal environments. This prevents ambient
repository-path and rubric transport; it does not provide or claim operating
system containment. Evidence records the actual executable digest, the
model-bound command identity, request and stream digests, and the derived
request-separation result. Raw provider stderr is retained only in the sibling
owner-private `.<output-name>.provider-attempts` directory. Public failures
expose its digest and length, never its bytes or decoded text.

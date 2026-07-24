# Evaluation integrity

Give each executor only the raw prompt, fixture, and its condition's immutable
bundle. A candidate bundle contains the candidate skill and its explicitly
routed references. A composed bundle is the exact union of the candidate,
declared companions, and, for a retained comparison, the declared retained
incumbent runtime subtree. Retained bytes must carry the incumbent owner,
repository, revision, full-tree lock, retained archive, entrypoints, and runtime
inventory; the composed manifest must bind those fields to the incumbent bundle.
Do not expose expectations, rubrics, expected output, grading instructions,
condition labels, private paths, ambient repository state, or ambient rules.

Run every coordinate declared by the schema-v2 matrix in a fresh isolated
session. The matrix binds its ordered skill inventory, expected scenario count,
scenario and rubric digests, reverse-dependency scenario IDs, and invalidation
event policy. Integrated evaluation matrices use `required`; only a matrix whose
evaluation ID starts with `focused-test:` may use
`allow_empty_for_focused_test`. Cover the `no_skill`, `incumbent`, `candidate`,
and `composed` conditions at repetitions 1 through 3. Record one ordered
candidate identity and one four-condition bundle set for every skill in the
matrix; each scenario must select the set belonging to its declared skill.
Mount each schema-v2 bundle read-only and bind its target skill,
entrypoints, declared calls, source provenance, archive, logical paths, bytes,
modes, and sizes. Resolve every archive relative to the evidence manifest,
reject symlinks and unsafe or non-regular archive entries, hash its actual bytes,
and require its complete file inventory to equal the bundle manifest. The
candidate files must remain an exact subset of the composed bundle. For a
retained comparison, reject missing, substituted, or injected retained bytes,
require the candidate source inventory to equal the candidate bundle, and reject
a composed inventory that is not the declared candidate/companion/retained
source union. Deny
shell, browser, tools, and filesystem access outside the bundle;
network is model-transport-only. Retain normalized provider context, per-run
isolation attestations, fresh request/response/session identities, configuration
and bundle IDs, timestamps, usage, and empty tool-event records. Every executor
run must name a unique regular response artifact relative to the evidence
manifest; bind `response_sha256` to that artifact's actual bytes.

After all 12 executor outputs for one scenario finish, give a separate grader
the rubric and a randomized batch addressed only by opaque output IDs. Unblind
conditions only during the later adjudication step. Use strict JSON throughout;
each grader batch names a unique response artifact relative to the evidence
manifest. Its actual bytes must match `response_sha256`, and its parsed strict
JSON must equal the canonical grading payload bound by `artifact_sha256`.
Duplicate keys or digest, coverage, freshness, isolation, blinding, aggregate,
or invalidation drift make the evidence malformed or non-passing. If an
invalidation event occurs, replace every executor run for its source scenario
and the matrix-declared reverse dependencies before recording resolution and
closing the evidence.

Resolve `scripts/check_eval_gate.py` relative to this skill and run:

```sh
python3 "<skill>/scripts/check_eval_gate.py" \
  --manifest /absolute/path/to/evidence-v2.json \
  --matrix /absolute/path/to/matrix-v2.json
```

The command emits schema-v2 JSON. Exit 0 means every safety, quality,
nonregression, utility, and invalidation gate passed; exit 1 is a well-formed
non-passing matrix; exit 2 is malformed input or CLI usage.

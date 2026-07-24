# Evaluation corpus

The files in `fixtures/` are raw executor prompts. For every with-skill run,
give the executor exactly the fixture and an immutable candidate-skill bundle.
The minimal bundle contains the selected skill's `SKILL.md` entrypoint and all
transitive Markdown references, uses a SHA-256 identity, and is delivered inline
or through an explicit read-only mount in an isolated ephemeral workspace. The
executor may access only that bundle and the raw fixture. Deny shell, browser,
network, filesystem access outside the bundle, and every tool by default. A
tool is available only when an explicit minimal allowlist is recorded in and
enforced by the eval contract. Repository files, neighboring plugins, grader
expectations, `corpus.json`, fixture paths, and reference output outside the
bundle are forbidden. External or plugin-root-escaping references invalidate
the bundle.

Behavior evaluation belongs in an external runner. Run no-skill, incumbent,
candidate, and composed conditions in isolated workspaces with repeated runs.
After each run finishes, give the raw fixture, response, and that scenario's
`grader_expectations` to a separate grader.

The repository validator resolves each declared candidate skill into its
minimal transitive bundle and checks only the declared contract shape, bundle
closure, corpus inventory, and prompt concreteness. It does not provision,
inspect, or enforce the external runner. Clean behavioral evidence requires a
recorded candidate-bundle identity and recorded enforcement of every runner
restriction. A green structural corpus validation is not clean evidence that
an LLM followed the workflow.

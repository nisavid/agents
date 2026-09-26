# Repository instructions

## Agent skills

### Issue tracker

Issues, PRDs, and Wayfinder maps are tracked in GitHub Issues for
`nisavid/agents`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the canonical `needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, and `wontfix` labels. See
`docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository with root `CONTEXT.md` and system-wide
ADRs under `docs/adr/`. See `docs/agents/domain.md`.

## Ownership boundary

Rolecasting, Tricritical, Versionkeeping, Mergecraft, Artifact Customs, Task
Witness, and Tidesmith live in
[`nisavid/provingkit`](https://github.com/nisavid/provingkit). Do not add or
restore their source here; change them in Provingkit.
`scripts/validate_provingkit_retirement.py` enforces this boundary.

`tooling/` owns the personal tools that remain here, each with its own
documentation. `.scratch/` holds unrelated experiments and is not a supported
package surface.

## Git and validation

Use Conventional Commits for commits and pull request titles; Cocogitto
enforces them through the `commit-msg` and `pre-push` hooks that
`cog install-hook --all` installs. Every change requires `git diff --check`.

Before publication, validate the repository boundary:

```sh
python -m unittest tests.test_validate_provingkit_retirement
python scripts/validate_provingkit_retirement.py .
```

Also run the checks documented by any touched tool under `tooling/`.

## Operating Policy

- This repository uses agentic engineering and operations. Agents should perform assigned tasks autonomously until they reach a boundary that requires stakeholder policy or an unavailable control surface.
- The user reserves authority over project initiatives and over initiation or continuation of work sessions. Within an active user-directed session, agents should drive execution, review loops, commits, publication steps, and cleanup unless escalation is required.
- Escalate when a decision or action impacts stakeholder concerns and the stakeholder's policy is unknown or uncertain.
- Escalate when an action must be taken but the agent lacks an autonomous control surface for it.
- When escalating a decision and a set of plausible, distinct choices is known, use a multiple-choice input tool if one is available in the interactive context. Include a way for the human operator to provide custom input.
- When escalating an action with a known prescribed path, present the steps clearly for the human operator to perform. Prefer fewer steps; present commands in easily copyable blocks, and prefer a single one-line command when practical.
- For every escalation, make the return contract clear: state exactly what result, confirmation, artifact, or output is needed to hand control back to the agent, and make it easy to validate.
- Prefer verified repository facts over guesses or aspirational guidance.
- When adding new agent-facing instructions, ask whether the information is durable, non-obvious, and useful before scouting a task.
- Remove guidance that becomes redundant with ordinary file discovery.

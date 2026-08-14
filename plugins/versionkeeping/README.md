# Versionkeeping plugin

Versionkeeping is an Agent Plugins package with a native Claude adapter. It owns task-only
checkpointing, intent-aware conflict resolution, deterministic exact-lease
publication, separately authorized remote-ref deletion, provenance-aware
worktree lifecycle, and history-preserving fork synchronization.

It owns local Git integration, fork synchronization, and separately authorized
terminal remote-ref deletion. It deliberately does not own Graphite operations;
review or pull-request creation, text, readiness, resolution, or merge
actuation; or model and delegation policy.

## Skills

| Public name                                             | Responsibility                                                                                                                   |
| ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `$versionkeeping:checkpointing-and-publishing-git-work` | Git/index/ref/push safety, separately authorized remote-ref deletion, completion choices, and provenance-aware terminal cleanup. |
| `$versionkeeping:resolving-merge-conflicts`             | Conflict interpretation and authorized file edits, with Git mechanics handed to checkpointing.                                   |
| `$versionkeeping:using-persistent-git-worktrees`        | Durable sibling worktree location, creation, movement, repair, and handoff.                                                      |
| `$versionkeeping:syncing-forks-with-upstream`           | Contract-aware fork synchronization that preserves upstream commit identity.                                                     |

## Layout

```text
plugins/versionkeeping/
├── plugin.json                      # Canonical Agent Plugins v1 manifest
├── .claude-plugin/plugin.json       # Claude adapter manifest
├── skills/                          # Shared harness-neutral core
│   └── checkpointing-and-publishing-git-work/
│       ├── references/              # Publication, cleanup, and eval integrity
│       └── scripts/                 # Publication and deletion planner/executor routes
│   └── resolving-merge-conflicts/    # Interpretation and authorized resolution edits
├── topology.json                    # Canonical component, call, and operation map
├── CHANGELOG.md
└── LICENSE
```

The Claude manifest and `skills/*/agents/openai.yaml` are thin client adapter
surfaces around the standard package. Shared skills do not assume a client-specific installation location:
they resolve helper scripts from this plugin root.
The `schema_version` in `topology.json` versions Versionkeeping's local
topology shape, not a repository-wide interchange schema.

## Package validation

From the package root, these checks use only plugin-relative paths:

```sh
python3 skills/checkpointing-and-publishing-git-work/scripts/plan_git_publication.py --help
python3 skills/checkpointing-and-publishing-git-work/scripts/execute_git_publication.py --help
python3 skills/checkpointing-and-publishing-git-work/scripts/plan_git_remote_ref_deletion.py --help
python3 skills/checkpointing-and-publishing-git-work/scripts/execute_git_remote_ref_deletion.py --help
```

The evaluation-gate regression suite is repository-only evidence, not an
installed runtime test. From the repository root, run
`python3 tests/plugins/versionkeeping/checkpointing-and-publishing-git-work/test_eval_gate.py`.

The planner may fetch bounded objects and create target-local temporary refs; it
is not an installed read-only validation gate.

The ordinary publication planner and executor accept only update/create
refspecs and never delete a remote ref. Terminal remote-ref deletion is a
separate plugin-relative planner/executor route that requires a verified merge
outcome plus explicit repository and operator authorization binding the exact
remote, full ref, and expected SHA. It deletes under an exact expected-SHA lease
and completes only by verifying the ref is absent.

## Repository release validation

Repository maintainers additionally run `python3 scripts/validate_versionkeeping.py`
and `python3 -m unittest tests/test_validate_versionkeeping.py` from the
repository root. Canonical development and release evidence lives at
`evals/versionkeeping/`, `tests/plugins/versionkeeping/`, and
`release/plugin-content-locks/versionkeeping.json`; none of it is installed in
the runtime root. The validator reads [topology.json](topology.json) as the
canonical component and ownership inventory and verifies the generated semantic
content lock.

## License and provenance

The plugin is MIT-licensed. Its Git-publication planner and baseline workflows
are migrated from the operator-owned canonical skill stack. No third-party
source or attribution requirement was identified in the supplied inputs.

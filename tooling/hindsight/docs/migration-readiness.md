# Hindsight Migration Readiness

The migration design is established, but the live migration is not currently
executable. The reusable CLI can validate inputs and construct a read-only,
immutable, unapproved shadow plan. It cannot yet approve, build, cut over, or
close out a live migration.

Do not run live discovery until the selected Hindsight server implements the
[server-backed migration-generation contract](https://github.com/nisavid/agents/issues/11).
Do not use the generic desired-state `apply` command with a migration shadow
plan.

## Distinguish adoption from data migration

| Goal | Current path |
| --- | --- |
| Use the reusable code and managed lifecycle while preserving the current profile and bank | Follow [Adopt the Hindsight control-plane architecture](adoption.md). No data migration is required. |
| Start a fresh installation with one canonical profile and bank | Follow the adoption guide. There is no legacy state to migrate. |
| Combine, rename, replace, or delete existing profiles or banks | Follow the migration sequence below only after every gate has an executable implementation and a separately approved plan. |

## Read-only live discovery

Earlier extraction coordination called this operation **Task 7 Step 5**. That
was a checklist label in the deleted source plan, not the name of a command or a
data-migration step. The operation is **read-only live discovery**. Its
successful outputs are immutable discovery inventory/evidence and a shadow plan
with `approved: false` and `mutation_authority: none`.

The historical checklist remains available for provenance in
[`nisavid/dotfiles` history](https://github.com/nisavid/dotfiles/blob/87f49b4/docs/superpowers/plans/2026-07-12-hindsight-memory-control-plane.md#L274-L320).
Current work and issue descriptions must use **read-only live discovery** rather
than the old task number.

The discovery command has this shape:

```zsh
"$HINDSIGHT_MEMORY_CLI" migration discover --read-only \
  --inventory /absolute/path/to/inventory.json \
  --profile "$profile" \
  --source-bank "$source_bank" \
  --candidate-bank "$candidate_bank" \
  --offline-package-manifest /absolute/path/to/offline-package-manifest.json \
  --approved-offline-package-digest "$approved_package_digest" \
  --private-catalog-digests /absolute/path/to/private-catalog-digests.json \
  --retain-watermarks /absolute/path/to/retain-watermarks.json \
  --completion-marker /absolute/path/to/distillation-complete.marker \
  --token-env HINDSIGHT_DATA_PLANE_TOKEN
```

Supplying `--completion-marker` identifies the path whose absent or unchanged
state discovery records. It does not authorize creating the marker or the
matching proposal-log entry.

This command is not authorized for a live installation yet. Before it may run:

1. The server must return one opaque monotonic generation for the selected
   profile or tenant scope.
2. That generation must advance for every planning-relevant mutation,
   including bank content, configuration, models, directives, hooks, schedules,
   invalidations, runtime retains, operation state, and retain watermarks.
3. Both banks and every related surface must be read from one server snapshot
   between generation reads.
4. The generation before and after the complete read must match exactly.
5. Missing, malformed, changed, or incorrectly scoped generations must publish
   no discovery result and grant no mutation authority.

Ordinary before/after equality, timestamps, process counters, client-computed
digests, and independently sampled endpoint values do not satisfy this gate.
The consumer must inject the token into the named environment variable only for
the command invocation; shell-startup files and rendered configuration are not
token stores.

## Established migration sequence

The [product requirements](PRD.md#migration-and-rollout) define this sequence:

1. **Discover:** inventory the source and historical candidate banks, endpoint
   and provider identity, versions, configuration, stats, scopes, tags,
   documents, models, directives, operations, hooks, schedules, retain
   watermarks, and invalidated memories from one generation.
2. **Plan:** reconcile every source and candidate item and every invalidation;
   publish an immutable shadow plan carrying no mutation authority.
3. **Authorize:** require both `distillation-complete.marker` and a matching
   `## Migration complete` proposal-log entry to identify the same run and
   artifact digest, then separately approve a digest-bound mutation plan. The
   unapproved shadow plan is never promoted in place.
4. **Build:** retain the separately approved projections into a shadow bank and
   chain server-backed target-generation receipts for every write batch.
5. **Validate:** consolidate the shadow; give every historical-candidate item
   and invalidation a verified disposition; and pass coverage, fidelity, secret
   and leakage, idle-operation, recall, model, curation reapplication, and
   rollback checks.
6. **Freeze and catch up:** enter a bounded maintenance interval, disable or
   redirect every write path, revoke write capabilities, wait for idle
   operations, capture the final high-water mark, and apply the final catch-up.
7. **Back up:** export the canonical source and shadow banks, create the
   historical-candidate full-bank provenance archive and encrypted curation
   manifest, and create a full-schema backup. Bind every candidate evidence
   digest into the shadow state, encrypt and digest every archive, restore-test
   the bank and schema archives in a disposable compatibility database, and
   verify curation reapplication from the manifest.
8. **Cut over:** delete the old canonical bank only under the approved plan,
   stop the profile, import the verified shadow under the canonical ID, restart
   it, reapply configuration and curation, and complete cold-cache and recall
   verification before re-enabling writes.
9. **Close out:** preserve rollback evidence, then separately approve and
   verify deletion of legacy live banks. Archive retirement is another
   independent approval and never follows implicitly from cutover.

Failure during the canonical-ID interval keeps writes frozen and restores the
verified full-schema backup before the previous service and hooks are resumed.

## Missing executable work

The following work remains before this sequence is an operator runbook:

- [Issue #11](https://github.com/nisavid/agents/issues/11) owns the upstream
  server-backed snapshot generation.
- [Issue #21](https://github.com/nisavid/agents/issues/21) owns production
  discovery inputs, the first gated read-only discovery, and review of its
  immutable output.
- [Issue #23](https://github.com/nisavid/agents/issues/23) owns the two-part
  completion gate, the separately approved mutation plan, shadow writes,
  chained target-generation receipts, freeze, catch-up, backup, restore proof,
  cutover, rollback, closeout, and archive retirement.
- [Issue #22](https://github.com/nisavid/agents/issues/22) owns portable
  installation and disposable macOS and Linux lifecycle validation.

The one-off `hindsight-embed-single-bank-cleanup --apply` path is not a
substitute for this workflow. It remains a separately invoked implementation
component and requires an independently reviewed migration plan.

# Cross-repository implementation handoff

Status: recovery-execution checkpoint. This document settles the working
ownership map and execution order; it is not a release receipt, a qualification
decision, a migration authorization, or a security review.

## Why this handoff exists

The Agent Plugins v1 work and the dotfiles agent-equipment work meet at the
deployment boundary, but they do not have the same goal. `nisavid/agents`
produces the reviewed plugin/source release and its evidence. `nisavid/dotfiles`
tracks installed equipment, resolves host state, and owns the separately
authorized installation and migration workflow. Neither repository is an
alternate implementation of the other.

The recovery rule is therefore one-way implementation and authority flow, with
a separate evidence-return lane:

```text
agents release candidate and receipts
        -> dotfiles catalog, lock, and host plan
        -> installed/convergence evidence
        -> separate adoption evidence return to the release tracker
```

There is no shared mutable runtime module and no parallel installer. If a
shared format becomes necessary, `agents` owns the versioned release-input
contract and test vectors; `dotfiles` owns the consumer, catalog binding, and
host execution semantics.

## Current source-side baseline

The live `nisavid/agents` `main` ref is the merge revision
`32da21fd77abd1c9b1ad293ce2b066852def4be9`. The reviewed head of the merged
source-disposition PR was `b3192bee1880669338dab6413939432bd3b295c4`.

PR #39 (`feat(plugins/release): adopt the Agent Plugins v1 source stack`) is a
historical package baseline. PR #57 (`feat(source-skill/disposition): settle
release refresh contract`) is the later source/disposition revision that
supersedes its source evidence. Issue #50 is closed, but the checked-in
refresh contract still records:

- `authority.release_eligibility: not-asserted`;
- `authority.host_mutation: not-authorized`; and
- security-sensitive receipt integration on the agents side remains deferred to
  the Daybreak follow-up; the separate dotfiles App-publisher route has since
  received a Daybreak review with required changes, while credential,
  bootstrap, and production activation remain operator-gated.

Issue #45 remains open because the candidate identity and lock evidence must
be regenerated after the later source changes, and the required final
installed-library rescout artifact is not yet present. The historical source
manifest is evidence of what was previously observed, not proof that the
current tree is release-qualified.

## Public plugin release boundary (2026-08-25)

This handoff concerns the first **public release of the Agent Plugins v1
distributions**. It does not define the first private release of the dotfiles
global-equipment tracker, catalog, or installer. The two projects meet only
when the qualified public release is supplied as an immutable input to the
downstream equipment system.

In the language of the global-equipment contract, a *distribution* is an
installable bundle and a *provider route* is one concrete way to expose that
bundle to a harness. A *harness coverage record* is the result for one
equipment identity in one harness. For this release, a review “cell” means one
audited-host × harness/provider-route combination; it is not another package
or another release unit.

The public v1 qualification boundary is the two audited hosts and every
meaningfully distinct supported harness route they expose. The intended
support includes Codex, Claude, Cursor, and Cursor Agent routes on macOS and
Linux where the host supports them. Claude Desktop is unsupported on Hatchery
and is excluded there; it remains a supported macOS route. A route is not
declared validated merely because its application or command is installed.

For the first public release, each supported route requires a thorough local
end-to-end smoke suite. The suite must exercise every meaningfully distinct
path through every capability of each distribution, using the operator's
active inference accounts on the two audited hosts. The evidence must be
secret-free and must record the route, capability, fixture/test identity,
result, and observed failure without retaining credentials or sensitive model
responses. This local suite is the v1 qualification path; it is not the
private dotfiles installation run.

Automating the same suite in GitHub Actions is a future-release project. It
requires a separate decision about inference endpoints, deterministic fixtures,
cost/time/retry limits, and response recording. The research lane may take
useful patterns from the T3 Code work associated with Theo and Julius, but it
must not assume that a CI endpoint or fixture design is settled by that
research.

The current macOS marketplace route may remain a temporary pre-release route
until the public release has a proper installation source and procedure. It
must not be treated as a reproducible release source while it points at a
dirty or unrelated checkout. The release qualification run uses a clean,
pinned source checkout; installation-route repair belongs to the later
deployment procedure.

## Rescout and update workflow (2026-08-25)

The final rescout is not a one-time list of whatever happens to be present.
After the last completed rescout, the release lane must:

1. enumerate all current equipment and source routes that changed;
2. screen every changed item for apparent topical relevance or overlap with a
   target distribution;
3. compare each hit with the corresponding plugin-family equipment and collect
   only update candidates supported by the diff;
4. present the candidate table to Ivan for a sanity/gut check;
5. apply only the accepted candidates to the owning plugin/source lane; and
6. rerun source reconciliation, dependent locks/evidence, and the final
   sanitized rescout after those updates.

Dynamic system and plugin caches remain observations of external provider
routes unless the release boundary explicitly includes them. Their location
does not establish ownership, and the rescout must not silently migrate or
drop them. The 2026-08-25 overlap scan is therefore candidate evidence only;
it is not an accepted source update or a qualification result.

The release candidate is the current `origin/main` **after** all pending
v1-targeted tickets and their accepted pull requests are resolved and merged.
Only then may the source identities, dependency closure, final rescout, and
qualification evidence be refreshed for the public release.

## Ownership map

| Concern | `nisavid/agents` owns | `nisavid/dotfiles` owns | Boundary evidence |
| --- | --- | --- | --- |
| Six distribution contents and package projections | plugin bytes, manifests, versions, source/package locks | none | agents #41, #45 |
| Source-skill refresh and terminal dispositions | source revisions, three-way reconciliation, disposition and final-rescout artifacts | consumes the settled receipt; does not edit source | agents #45, #50, #51; dotfiles #112, #124 |
| Release qualification and promotion | candidate identity, affected dependency closure, qualification receipt | does not decide release eligibility | agents #44, #45, #48 |
| Catalog, lock, host profiles, and installed inventory | no host-state authority | authored catalog, resolved lock, two-host profiles, convergence evidence | dotfiles #68, #80; agents #46 |
| PlanActionSet and prepared authority | no implementation authority | resolver projection, capture, prepared state, checkpoint, adapters, and execution | dotfiles #108, #113, #115, #116 |
| Legacy route retirement and rollback | no host mutation | install-before-remove ordering, duplicate/shadow checks, rollback, and absence evidence | agents #51 upstream; dotfiles #80, #114, #127 |
| Live adoption and rollback decision | publishes adoption inputs/evidence requirements | performs only separately authorized host operations | agents #47; dotfiles #68, #128 |
| Security, trust, and authorization review | supplies evidence to the review lane | consumes the approved result | agents #53/#56; dotfiles #112/#124 own release-receipt trust, while #168/#169 own the separate repository-admission App boundary; use Daybreak Blue where a security judgment is required |

The issue numbers in this table are tracker ownership, not permission to skip a
missing prerequisite. A green check, an old approval, or a merged parent does
not promote an evidence artifact into release or migration authority.

## Cross-repository input boundary

The eventual consumer input is a versioned, digest-bound *release-input
record*. Its final field names and major version are intentionally not frozen
by this checkpoint. `agents` remains the canonical owner of that schema and
major version; dotfiles #112/#124 co-design and accept the consumer bindings,
but do not create a parallel schema or unilaterally change the major. The
contract must be settled through agents #45/#48 and dotfiles #112/#124, with
independent Daybreak review where the record is used as a trust or
authorization input.

The minimum semantic bindings to settle are:

1. the coordinated release and candidate identities, including the immutable
   source commit/tree and package projection contract;
2. one record for each of the six distributions, including version, plugin
   root, package/tree/content digests, manifest digest, and every identity
   artifact;
3. the source manifest, contribution/disposition ledgers, source-reconciliation
   receipt, and final installed-library rescout, each bound by repository-
   relative path and raw-byte digest;
4. qualification status, freshness window, affected-dependency closure, and
   explicit non-authority values; and
5. provenance sufficient for the dotfiles consumer to bind the input to its
   catalog, lock, coordinated release identity, and later convergence receipt.

Until that decision is recorded, dotfiles must treat the input as absent for
materialization purposes. A consumer may parse and report an unqualified
candidate for planning, but it must not import candidate code, create a
release receipt, authorize an apply, or infer that host migration is allowed.

The downstream receipt-binding implementation belongs in dotfiles #124 after
issue #112 settles the record/version. The upstream release and source evidence
remain in `agents`; copying them into dotfiles does not transfer their
authority or ownership.

## Recovery execution order

1. **Preserve provenance.** Keep the merged PR #57 history, all source refs,
   and all dotfiles worktrees. Do not rewrite or discard the mixed accepted
   history in dotfiles PR #165 during the audit.
2. **Finish the upstream qualification lane.** Refresh source identities and
   locks, rerun the affected dependency closure, complete the final installed-
   library rescout, and capture the qualification receipt under agents #45.
   Source convergence behavior remains tracked by agents #51; security-
   sensitive receipt integration remains Daybreak-gated (#53/#56).
3. **Settle the interface.** Use agents #48 plus dotfiles #112/#124 to agree on
   the closed release-input record, its major, digest selectors, freshness and
   non-authority semantics, and the test vectors. Do not invent a parallel
   schema in either repository before this agreement.
4. **Audit the downstream implementation frontier.** The dotfiles owner
   reviews merged PR #165 against #154 and #115, and open PR #166 against
   issue #155, then proposes narrow follow-ups. This audit decides ownership and
   patch disposition; it does not grant a security verdict or migration
   authority.
5. **Materialize only after both gates.** Dotfiles #80/#124 may bind the exact
   release input into catalog/lock and disposable host profiles only after the
   upstream record is qualified and the receipt-binding decision is approved.
   Step 8b remains separate from Step 9 live migration.
6. **Adopt and close out separately.** Host installation, retirement,
   rollback, release archive, and live migration follow the dotfiles delivery
   graph and their own authorization gates. Agents #47 records adoption
   evidence; it does not execute host mutation.

## Downstream audit checkpoint (2026-08-24)

The authorized dotfiles read-only audit confirms that PR #165 is a legitimate
mixed-scope accepted baseline. Its contract/schema/admission portion is the
baseline from issue #154; its prepared-capture authority work is already landed groundwork
for the separately owned #115 lane. The accepted history should be retained,
not rewritten or reverted, and no #113, #114, #115, #116, release, or
migration acceptance should be inferred from it.

The audit also confirms that PR #166 stays within #155: it is a test-only physical-target matrix change, review-approved with no unresolved review threads, and does not claim production PlanActionSet projection. Its current merge state is blocked by the repository's missing required `Owner-signed age admission` context. That is a merge-gate fact, not a security or admission verdict. At the time of this checkpoint, interpretation and resolution were deferred to a Daybreak-capable thread; the current route refresh below records the resulting disposition. The uncommitted fixture edit in the separate target-matrix worktree is preserved and is not part of PR #166.

At the time of this checkpoint, the planned downstream sequence included the
the #154/#155 owner-acceptance step, followed by #113 production projection, #114
legacy projection, #115 prepared sealing, and #116 execution. Since then, #154
has closed. The current route keeps #155 owner acceptance and #170 requalification
as separate gates: #113 may proceed only after #155 is completed and accepted,
while #170 independently closes the #166 test lane. Then come #114 (with #111's
decision gate), #115, and #116. Issues #111/#112/#127/#128 remain decision gates,
while #124/#125 remain the
release-boundary consumption and materialization lane.

## Current route refresh (2026-08-25)

The App-admission interpretation is now settled: Daybreak confirmed that the
`Owner-signed age admission` requirement is an App-ID-pinned branch-protection
check, not a receipt requirement for test-only PR #166, and approved the
workflow-backed event-driven publisher route with required changes. Dotfiles
PR #172 contains the non-privileged source slice; its remaining hosted privacy
findings and the App credential/bootstrap deployment are owner-gated under
issues #168/#169. The source-skill receipt questions in agents #53/#56 remain a
separate Daybreak lane.

This refresh does not change the authority boundary: #170 independently owns
the #166 exact-head requalification, while #155 remains the prerequisite for
issue #113. The downstream graph resumes at #113 only after #155 is completed and
owner-accepted; #170 does not replace that gate. Then come #114 (with #111's
decision gate), #115, and #116; #154 is already closed. No receipt,
branch-protection edit,
release, or host mutation is authorized by this handoff.

## Explicit non-authority and stop conditions

This handoff does not:

- declare `release_eligibility` or candidate qualification;
- authorize installation, apply, checkpoint, adapter invocation, retirement,
  release, rollback, or live migration;
- decide the security or cryptographic validity of a receipt or authority
  record; or
- make PR #165 or PR #166 merge-ready.

Stop and return to the named owner when a release-input digest, source
revision, installed-membership observation, discovery route, issue ownership,
or review result changes. Such a change invalidates downstream planning until
the affected upstream evidence and interface bindings are regenerated.

## Closeout evidence for this recovery checkpoint

The recovery is coherent when the following are all true:

- agents #45/#48 and dotfiles #112/#124 name one agreed release-input record
  and its owner, without a second installer or mutable shared module;
- the final upstream refresh and rescout are newer than every source or
  disposition change they cover;
- dotfiles’ catalog/lock and PlanActionSet work consumes the record as an
  immutable external input and retains its own execution authority boundary;
- the downstream PR audit has a written disposition for every overlapping
  commit, with all security-sensitive questions routed to Daybreak; and
- live migration remains blocked until the independent release, archive,
  controller, and operator gates are satisfied.

Until then, the correct status is **recovery in progress; release and host
mutation not authorized**.

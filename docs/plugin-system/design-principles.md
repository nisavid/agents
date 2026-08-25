# Plugin system design principles

This repository develops six coordinated distributions for frontier agents:
Rolecasting, Versionkeeping, Tricritical, Mergecraft, Artifact Customs, and
Task Witness. They form one equipment system, not a catalog of unrelated
skills. The system should contain the smallest set of durable contracts that
materially improves agent behavior across supported harnesses.

These principles describe the intended steady state. Package references
describe current behavior and authority. Release ledgers record what has been
proved for a particular candidate. A design principle never substitutes for
release evidence.

## Design for the agents we have

Agent instructions are model-relative. Guidance that once rescued weaker
models may be inert, distracting, or actively harmful to current frontier
agents. Preserve an instruction because comparative behavior shows that it
helps, not because it was important to an earlier workflow.

Prune aggressively:

- Remove clauses that do not change behavior.
- Remove instructions that encourage ceremony, dogma, or needless work.
- State each retained behavior once, in its strongest precise form.
- Preserve distinctive cognitive framing only when it produces useful
  exploration or counters a demonstrated bias.
- Prefer a small contract plus deterministic support over a long prompt that
  asks the model to remember every invariant itself.

The desired result is not a complete migration of every source clause. It is a
better expression of every source behavior that still makes a positive
difference.

## Build one cohesive system

Each distribution owns a distinct part of the workflow:

| Distribution | Primary responsibility |
| --- | --- |
| Rolecasting | Select execution topology and a suitable model as separate, evidence-bound decisions. |
| Versionkeeping | Preserve Git, worktree, ref, conflict, and publication provenance. |
| Tricritical | Own general change review, finding adjudication, authorized revision, and fixed-point review loops. |
| Mergecraft | Own reviewer-facing pull-request content and the pull-request lifecycle. |
| Artifact Customs | Govern third-party components from assessment through adoption, maintenance, and retirement. |
| Task Witness | Define the code-only evidence-validation boundary. Its package reference owns which integrations are currently eligible and their limitations. |

One semantic capability or mutable surface has one owner. Outcome coordinators
sequence those owners; they do not copy their procedures. Capability owners do
not call back into their outcome coordinators. One outer loop owns repetition.
Adapters translate harness mechanics without becoming another semantic owner.

Model choice and invocation topology remain orthogonal. Portable skills define
roles, evidence, authority, and terminal states. Harness adapters select an
available execution mechanism and carry the portable contract without
rewriting it.

A public skill earns its place through a distinct user intent, authority
boundary, and useful direct invocation. Internal stages, detailed rubrics,
mechanical checks, and platform adapters belong in references, scripts, tests,
or private modules when they do not meet that bar.

## Follow evidence breadcrumbs

Agents reason more reliably when the workflow leaves a breadcrumb trail:
requirements, applicable standards, source observations, candidate identity,
commands run, outputs observed, findings, dispositions, and verification. Each
step should preserve enough evidence for the next step to test its claims
without trusting a narrative summary.

Deterministic equipment catches mismatches outside the model's awareness.
Content locks, topology validators, immutable candidate identities, receipts,
and source-lineage checks should bind claims to exact artifacts. Changing an
artifact invalidates its review and the dependent evidence that relied on it.
A fresh candidate needs fresh verification and, where required, fresh
independent review.

Task Witness is the designed validation boundary for evidence that must cross
harness or release stages. Its mere presence grants no authority. A route may
rely on Task Witness only when the relevant producer, issuer, validator,
runtime, and external trust controls are qualified for that exact use. Other
states fail closed and remain truthful about their limitations.

## Review through asymmetric falsification

Tricritical is the sole general code-review entry point. Its three critics are
deliberately asymmetric:

| Critic | Question |
| --- | --- |
| Intent | Does the candidate satisfy the accepted requirements, non-goals, scope, and authority? |
| Runtime | Can a causal path disprove correctness, security, compatibility, operability, or the claimed verification? |
| Structure | Can deletion, consolidation, repaired ownership, or a smaller coherent shape reduce reader and maintenance burden? |

The critics receive the same frozen candidate and governing sources but form
their conclusions independently. Synthesis verifies claims against source; it
does not average reports or treat agreement as truth. Consequence-triggered
specialists add security, migration, accessibility, performance, deployment,
or other domain scrutiny without turning every review into an unbounded panel.

Requirements and specifications primarily ground Intent. Repository standards
and semantically triggered skill contracts are common evidence for every
critic, not a fourth critic or an exhaustive routing table. Route a possible
violation by its consequence: contract and scope failures to Intent, runtime or
operational failures to Runtime, and ownership or maintainability failures to
Structure. A single rule may bear on more than one critic.

Review, adjudication, revision, and repetition remain separate authority
surfaces:

- Review observes and returns evidence-bound candidate findings.
- Adjudication gives each internal or external finding one disposition.
- Revision applies only accepted findings within the original mutation scope.
- The loop freezes and reviews each successor afresh until it reaches a
  truthful terminal state.

This separation keeps a critic from editing the artifact it judges, prevents a
finding from expanding authority, and lets external review feedback share the
same evidence discipline without starting a competing review workflow.

## Counter persistent generation biases

The suite should firmly counter biases that still survive in frontier models:

- **Appeasement:** Treat the operator's framing and the candidate author's
  rationale as claims to test. Report concrete disproof attempts without
  softening them to protect the creator.
- **Addition bias:** Always test deletion, consolidation, and reuse before
  accepting new machinery.
- **Invisible human cost:** Count the effort required to understand, review,
  operate, and maintain generated content as a real design cost.
- **Local optimization:** Include long-term ownership, integration, upgrade,
  and maintenance costs when judging an apparently elegant local change.
- **Unrequested perfection:** Bound recommendations to accepted requirements,
  non-goals, and consequence. Preserve worthy adjacent ideas as follow-ups
  rather than turning them into blockers or enlarging the current solution.

These counterweights are cognitive instruments, not automatic findings. A
large module may be cohesive. An abstraction may be justified. A clean change
should remain clean. Evidence decides.

## Absorb behaviors, not documents

Thermos, Matt Pocock's `code-review`, Keystone `change-review`, and the
overlapping Superpowers review routes are source baselines for Tricritical.
They are not permanent competing general-review workflows. Distinct domain
specialists, such as security review, remain specialists invoked through the
review topology.

Mine each source at the behavior level. Useful units include independent
requirements review, causal runtime tracing, deletion pressure, adversarial
priming, evidence verification, and fixed-point semantics. Give each behavior
one destination or an explicit drop rationale. Avoid one-to-one clause maps:
they reward textual preservation instead of behavioral improvement. Track
source revision, license, and attribution separately and exactly.

Managed upstream skills remain immutable. When a useful personal behavior
resembles an upstream skill, place it in the appropriate personal plugin,
global instruction, extension, or maintained fork. Do not mutate the managed
source to make it conform to this suite. Once replacement behavior is proved,
retire or disable the superseded broad route so users and agents still have one
general owner.

Before release, rescout every in-scope instruction source that changed since
its last completed rescout, including global instructions, plugin-provided,
managed, external-tool-managed, and standalone skills, plus their conditional
references and supporting material. Reconcile each hit across the historical
import, current source, and current plugin. End with one behavior-level
disposition: retain with its existing owner, migrate to a named owner, or drop
with a reason.

## Treat evaluation as part of design

Static prose review cannot prove that instructions improve agent behavior.
Compare no-guidance, incumbent, candidate, and composed workflows in blinded,
repeated runs. Use ablations to test the mechanisms under debate: critic
priming, independent contexts, source verification, smell vocabulary, model
heterogeneity, and synthesis rules.

Fixtures need true opportunities and traps: wrong requirements implemented
cleanly, runtime regressions hidden by green tests, needless compatibility
layers, speculative abstractions, deletable code, a large but cohesive module,
an intentionally clean change, misleading author framing, and authority
boundaries.

Measure behavior that matters:

- unique valid findings and false positives;
- evidence quality and causal completeness;
- deletion or simplification value;
- scope and authority preservation;
- human review and maintenance burden;
- trigger precision, latency, and collisions with neighboring owners; and
- truthful limitations when a harness cannot supply the intended isolation or
  assurance.

Run these evaluations on the current supported model and harness matrix.
Historical importance, familiar wording, and a favorable static score do not
establish continued value.

## Keep design, release, and history distinct

This page owns durable design rationale. The
[source contribution ledger](../../release/source-skill-lineage/contribution-ledger.json)
and
[disposition ledger](../../release/source-skill-disposition/disposition-ledger.json)
are the structured owners of behavior-level provenance and migration
decisions. The
[source-skill disposition and release-refresh contract](../superpowers/research/2026-08-23-source-skill-disposition-and-release-refresh.md)
owns source freshness and qualification prerequisites. Package references own
their current runtime behavior and limitations.

Historical plans and transcripts remain research inputs. Promote their durable
principles, evidence, and unsettled work into the owners above; do not make a
historical execution ledger a second current specification.

# Review Atlas Reference Design

## Purpose

A Review Atlas is a deterministic, manifest-driven aid for reviewing a large
pull request or stack. It explains architecture and change sequence without
forcing every concern into one dense diagram. It routes reviewers from an
architectural claim to the narrowest authoritative diff, file, symbol, test, or
evidence view.

The repository owns code and tests. The stack tool owns stack ancestry.
The forge owns PRs, diffs, comments, checks, approvals, and attachments. The
atlas guides review; it never becomes a system of record or review actuator.

### Goals

- Give reviewers an architecture-first model and an explicit temporal overlay.
- Show before-state, selected-change delta, cumulative outcome, and dependencies.
- Preserve nearby context while making dense seams individually traceable.
- Bind claims to provenance, source routes, tests, risks, and ambiguity.
- Generate every published view from one typed semantic manifest.
- Keep deterministic validation and publication inputs separable from synthesis.

### Non-goals

- Embedding full diffs, comments, approvals, checks, or review actions.
- Polling live forge or stack state from a published static artifact.
- Publishing credentials, raw source, private API responses, or unrelated data.
- Treating a reference implementation as a general hosted review product.
- Fixing a universal lens count, diagram count, or graph depth.

## Design principles

### Architecture first, chronology second

The canonical graph describes entities, seams, flows, and regions independently
of PR chronology. Ordered changes activate, modify, remove, or reclassify stable
semantic objects; a component does not change identity because a later PR edits
it.

### Guided exploration with bounded freedom

The atlas offers curated lenses framed as concrete review questions. Reviewers
may pan, zoom, select, and follow related lenses, but do not begin from an
undifferentiated infinite canvas.

### Progressive disclosure

The overview shows primary regions and flow. A focused lens shows the selected
concern and muted one-hop context. Dense composite seams expand into separated
lanes. Files, symbols, tests, evidence, and risks live in the inspector rather
than crowding the canvas.

### Claims carry provenance

Every material claim is observed, declared, inferred, or unknown/conflicted.
Inference can help synthesis but cannot silently assert future scope,
ownership, guarantees, or promotion state.

### Source systems remain authoritative

The atlas links to repositories, stack tooling, and forge review surfaces. It
can be regenerated from captured source state, but it never replaces those
systems.

## Architecture

The generation pipeline has four stages:

1. Capture repository and Git state, stack metadata, PR metadata, and durable
   author declarations.
2. Synthesize one canonical semantic review model and validate provenance,
   ambiguity, ownership, and review contracts.
3. Derive bounded lens specifications and render deterministic assets.
4. Deliver reviewer entry points while preserving repository, stack, and forge
   authority.

Agents own semantic synthesis and review questions. Deterministic tooling owns
schema enforcement, state folding, layout, rendering, validation, repeatability,
and publication inputs. Published markup or geometry cannot bypass the
manifest.

## Semantic manifest

The durable renderer input has this conceptual shape:

    ReviewAtlasManifest
    ├── sourceSnapshot
    ├── graph
    │   ├── entities
    │   ├── seams
    │   ├── flows
    │   └── regions
    ├── baseState
    ├── changeSets
    ├── claims
    ├── reviewContracts
    ├── reviewQuestions
    ├── personaPresets
    ├── deliveryEntries
    └── validatedOverrides

All graph objects have stable semantic IDs. Rendered IDs, selectors, PR
numbers, paths, and display labels reference those IDs rather than becoming
identity.

The source snapshot records repository identity, base commit, ordered stack
branches and commits, pull requests, and attributable declarations. Every
consequential declaration records author, time, exact answer, affected semantic
IDs or claims, durable source when available, and a content digest.

### Temporal change model

Base state lists objects and claims active at the captured base commit. Each
ordered change set uses one of four operations:

- Add introduces an inactive object or claim.
- Modify changes named fields while retaining semantic identity.
- Remove ends an active object's or claim's lifetime.
- Reclassify changes provenance, role, or temporal status without inventing a
  new subject.

Each operation records before and after state, owning change, prerequisites, and
cross-change dependencies. Folding rejects adding an active ID, modifying or
removing an inactive ID, an incorrect before-state, and unsatisfied
dependencies. The same fold derives Before, This Change, and Outcome views.

### Published payload boundary

Published assets may include semantic IDs, architectural claims, display
labels, PR/branch/commit identifiers, repository-relative paths, symbols,
evidence labels, and the authenticated routes required for review. They may not
include source files, diff bodies, comments, credentials, private API payloads,
or unrelated repository metadata.

Richer evidence remains in the trusted generation environment. Publication
exports only the minimum review-facing representation.

### Review contracts

Every changed entity rendered as a node and every changed seam has a complete
contract:

- architectural claim and practical significance;
- owning change and stack dependencies;
- implementing files and symbols;
- focused diff and stack routes;
- tests, validation, or evidence;
- before, selected-change, and outcome status; and
- risks, assumptions, ambiguities, and conflicts.

Changed flows, regions, and claims retain ownership, provenance, and source
routing. They need a separate contract only when independently selectable.
Missing required contract data blocks the affected lens and entry preview.

### Claim provenance

Observed claims cite code, tests, history, or review metadata. Declared claims
cite attributable durable intent. Inferred claims remain visibly inferred.
Unknown/conflicted claims remain visible when useful and mark their affected
scope.

### Ambiguity handling

Ask the author only when an answer materially changes architecture, ownership,
review routing, or a published claim. Do not ask about ordinary layout,
mechanics, colors, or decomposition.

Ambiguity blocks only dependent output unless it affects primary regions,
primary flow, or the canonical graph. Errors identify semantic IDs, claims,
lenses, and delivery entries rather than only renderer coordinates.

## Adaptive lens decomposition

Every atlas has one architecture overview. Additional lenses derive from review
questions and graph density, not a fixed count:

1. Identify primary regions and end-to-end flow.
2. Identify questions a reviewer must answer.
3. Select the smallest fitting grammar: lifecycle, boundary, comparison, state,
   identity, evidence, or flow.
4. Include focused objects and enough muted context to locate them.
5. Split when routes become hard to trace, labels collide, perspectives
   compete, or the inspector would compensate for an overloaded canvas.
6. Prove every changed node and seam appears in a reviewable lens and all
   changed flows/regions remain reachable through an owning contract.

Layout overrides are bounded, schema-validated hints such as ordering, lanes,
or label positions. They cannot add semantic objects, suppress required
context, or introduce a hand-authored published view.

## Reviewer interaction model

### Workspace

The workspace keeps five persistent parts:

1. Header with atlas identity, active lens, persona, and selected change.
2. Guided lens navigation and an overview minimap.
3. Canvas for the current question, focus objects, and muted context.
4. Contract inspector for claim, provenance, ownership, source routes,
   evidence, temporal state, risks, and assumptions.
5. Temporal scrubber for base, selected change, and outcome.

### Layered focus

Selecting a node or seam gives it visual priority, separates composite
relationships when necessary, keeps one-hop context visible, collapses
unrelated secondary relationships behind a count, and updates the inspector
without changing lens or temporal selection.

### Persona presets

All personas share one model. Reviewer emphasizes changed contracts, risks,
verification, and focused routes. Author emphasizes ownership, missing
contracts, ambiguity, and diagnostics. Explorer starts at the overview with
more explanatory context. Presets change emphasis, never facts.

### Temporal model

The scrubber exposes cumulative state immediately before a change, that
change's typed delta, and cumulative outcome. Work beyond the captured stack
appears only from an authoritative declaration, never naming, TODOs, or
architectural possibility.

### Review handoff

The inspector summarizes the contract and links to the narrowest source view.
It does not reproduce a full diff or conversation. Reviewers can move from a
seam to focused diffs, exact files/symbols, tests/evidence, and stack context.

## PR-body delivery

For an atlas-selected PR, the body retains the canonical leading Stack/Diff
contract. A compact orientation preview may link to the exact atlas lens,
temporal state, selection, and reviewer preset. Ordinary PRs do not require an
atlas preview. Adjacent atlas prose states the review question, stack position,
and related lenses.

Delivery adapters decide which supported hosting and attachment mechanisms fit
the repository. Concrete private routes, access details, uploaded artifacts,
and application-specific mapping stay outside this public reference.
Protected deep links may rely on destination access control, but never embed
bearer or signed credentials.

### Publication safety

The publication bundle records source and manifest digests, generated asset
digests, targets, expected title/body digests, managed-section replacements,
and original managed sections. Stable ownership markers make reruns replace or
verify one managed section rather than append duplicates.

Use two phases:

1. Prepare and verify all atlas output, entry assets, routes, and body
   replacements without changing PR bodies.
2. Revalidate source identities and exact title/body preimages, then delegate
   each body mutation to the guarded publisher in stack order.

A resumable journal records original/intended title and body bytes, digests, and
each write result. A preflight failure changes no bodies. A mid-sequence failure
stops without retry or rollback and reports exact mixed state. Final
verification rereads every managed body and route.

## Validation and error handling

### Gate 1: schema and referential integrity

- The manifest satisfies its schema and semantic IDs are unique.
- Every relation, claim, stack reference, and delivery entry resolves.
- Captured base/head commits still match the source snapshot.
- Base state contains only objects and claims active at the base.
- Consequential declarations are attributable, durable, and content-addressed.

### Gate 2: review-contract completeness

- Every changed node and seam has a complete contract.
- Every material claim has provenance and source references.
- Every change has one recommended entry lens.
- Changed subjects remain reachable through owning contracts.

### Gate 3: visual and interaction budgets

- Clipping and unintended label/node/control overlap tolerance is zero.
- Focused parallel edge lanes maintain at least 16 CSS pixels between
  centerlines. Labels maintain at least 12 CSS pixels of clearance from
  unrelated edges and shapes.
- Primary flows remain traceable without crossing unrelated labels.
- Atlas views pass at 1024-by-768, 1280-by-800, and 1512-by-982 CSS-pixel
  desktop viewports. Viewports below 1024 CSS pixels are explicitly unsupported
  by this reference implementation rather than silently degraded.
- PR previews remain legible at 640 CSS pixels wide with rendered text at least
  12 CSS pixels high.
- Keyboard navigation, focus visibility, contrast, text alternatives, and
  reduced-motion behavior pass their checks.

Automated checks own measurable budgets. A human acceptance gate owns whether
primary routes are easy to follow and grouping communicates the intended
architecture.

### Gate 4: rendered routing

- Every preview opens the expected lens, temporal state, and selection.
- Every lens links to overview and related lenses.
- Inspector routes resolve to intended source and evidence.
- Published assets stay inside the payload boundary.
- Publication is fresh, idempotent, resumable, and verified or explicitly
  blocked in a known mixed state.

## Testing strategy

The reference implementation requires schema and referential tests; temporal
fold/property tests; provenance and ambiguity fixtures; contract coverage and
entry-routing tests; deterministic structural renderer tests; browser
navigation, focus, scrubber, keyboard, and responsive tests; overlap, clipping,
clearance, link, and accessibility checks; stale-source and concurrent-edit
publication tests; idempotence, resume, partial-state, and final-verification
tests; and package checks proving every view is manifest-derived.

Keep named visual regressions as fixtures until generalized checks subsume them.
Use a final human review at body and full-atlas sizes without treating it as a
substitute for deterministic gates.

## Reference implementation boundaries

Keep atlas source, semantic manifests, generators, tests, documentation, and
generated assets outside application product branches. Application repositories
receive only PR-body links/private attachments as outputs. Generated hosted
artifacts are publication outputs, not runtime assets. Discovery mockups may be
manual; final published markup and geometry may not be hand-authored.

## Delivery sequence

1. Define and test the semantic and review-contract schemas.
2. Encode source snapshot, base state, ordered changes, and declarations.
3. Implement overview and adaptive lens grammar.
4. Generate justified lenses and cumulative overlays.
5. Implement inspector, minimap, personas, scrubber, and deep links.
6. Generate compact PR entry previews.
7. Run structural, visual, interaction, accessibility, and routing gates.
8. Prepare publication bundle and pass freshness preflight.
9. Publish managed body sections while preserving canonical navigation and
   still-current custom content.
10. Verify every body, entry point, atlas route, and source handoff.

## Success criteria

- Reviewers can locate every changed node/seam, owner, dependency, and source.
- Before, selected delta, and outcome remain consistent with captured ancestry.
- Dense relationships can be traced without losing one-hop context.
- Every published view derives from the manifest and passes all four gates.
- Static output contains no credentials or live review data.
- Application repositories contain only authorized review-surface links or
  outputs, never atlas implementation source.

## Deferred product direction

A broader product could add authenticated repository access, live review
overlays, embedded source, comment/approval integration, repository adapters,
persisted reviewer state, and multi-repository hosting. That has a distinct
security, retention, and operational model and must not emerge implicitly from
this reference.

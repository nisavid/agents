# Code Quality Rubric

Use this rubric after the initial diff pass. This is an unusually strict maintainability review focused on implementation quality, abstraction quality, modularity, succinctness, legibility, and codebase health. Do not merely identify local cleanup. Actively search for code-judo moves: restructures that preserve behavior while making the implementation dramatically simpler, smaller, more direct, and more elegant.

## Baseline Review Stance

- Perform a deep code-quality audit of the current branch changes.
- Rethink how to structure or implement the changes to improve code quality without changing behavior.
- Work to improve abstractions, modularity, spaghetti-code pressure, succinctness, and legibility.
- Be ambitious when a clear path to restructuring would materially improve the implementation.
- Be thorough and rigorous. Measure twice, cut once.

## Non-Negotiable Standards

### 0. Be ambitious about structural simplification

- Do not stop at "this could be a bit cleaner."
- Look for opportunities to reframe the change so whole branches, helpers, modes, conditionals, or layers disappear.
- Prefer the solution that makes the code feel inevitable in hindsight.
- Assume there is often a code-judo move available: a reorganization that uses the existing architecture more effectively and makes the change simpler.
- If there is a path to delete complexity rather than rearrange it, push hard for that path.

### 1. Do not let files sprawl past 1000 lines without strong reason

- Treat a file crossing from below 1000 lines to above 1000 lines as a strong code-quality smell by default.
- Prefer extracting helpers, subcomponents, modules, or local abstractions instead of letting a file sprawl.
- If the diff crosses that threshold, explicitly ask whether the code should be decomposed first.
- Waive this only when there is a compelling structural reason and the resulting file remains clearly organized.

### 2. Do not allow random spaghetti growth

- Be highly suspicious of new ad-hoc conditionals, scattered special cases, or one-off branches inserted into unrelated flows.
- Treat weird if-statements in random places as a design problem, not a stylistic nit.
- Prefer moving logic into a dedicated abstraction, helper, state machine, policy object, or separate module instead of tangling an existing path.
- Call out changes that make surrounding code harder to reason about, even if they technically work.

### 3. Bias toward cleaning the design

- If behavior can stay the same while the structure becomes meaningfully cleaner, push for the cleaner version.
- Do not rubber-stamp working implementations that leave the codebase messier.
- Prefer simplifications that remove moving pieces altogether over refactors that spread the same complexity around.

### 4. Prefer direct, boring, maintainable code

- Treat brittle, ad-hoc, or magical behavior as a code-quality problem.
- Be skeptical of generic mechanisms that hide simple data-shape assumptions.
- Flag thin abstractions, identity wrappers, and pass-through helpers that add indirection without buying clarity.

### 5. Push hard on type and boundary cleanliness

- Question unnecessary optionality, `unknown`, `any`, or cast-heavy code when a clearer type boundary could exist.
- Prefer explicit typed models or shared contracts over loosely shaped ad-hoc objects.
- If a branch relies on silent fallback to paper over an unclear invariant, ask whether the boundary should be explicit instead.

### 6. Keep logic in the canonical layer

- Call out feature logic leaking into shared paths or implementation details leaking through APIs.
- Prefer existing canonical utilities and helpers over bespoke one-offs.
- Push code toward the package, service, file, or module that already owns the concept.

### 7. Treat avoidable orchestration complexity as a design smell

- If independent work is serialized for no good reason, ask whether the flow should run in parallel.
- If related updates can leave state half-applied, push for a more atomic structure.
- Do not over-index on micro-optimizations, but flag avoidable orchestration complexity that makes implementation brittle.

## Primary Review Questions

For every meaningful change, ask:

- Is there a code-judo move that would make this dramatically simpler?
- Can the change be reframed so fewer concepts, branches, or helper layers are needed?
- Does this improve or worsen the local architecture?
- Did the diff add branching complexity where a better abstraction should exist?
- Did a previously cohesive module become more coupled, more stateful, or harder to scan?
- Is this logic living in the right file and layer?
- Did this change enlarge a file or component past a healthy size boundary?
- Are repeated conditionals signaling a missing model or missing helper?
- Is the implementation direct and legible, or does it rely on special cases and incidental control flow?
- Is this abstraction earning its keep, or is it just a wrapper?
- Did casts, optionality, fallback behavior, or ad-hoc object shapes obscure the real invariant?
- Is this logic in the canonical layer, or did the diff leak details across a boundary?
- Is this orchestration more sequential or less atomic than it needs to be?

## What To Flag Aggressively

- A complicated implementation where a cleaner reframing could delete whole categories of complexity.
- Refactors that move code around but fail to reduce the number of concepts a reader must hold.
- A file crossing 1000 lines due to the PR, especially when the new code could be split out.
- New conditionals bolted onto unrelated code paths.
- One-off booleans, nullable modes, or flags that complicate existing control flow.
- Feature-specific logic leaking into general-purpose modules.
- Generic magic that hides simple structure and makes the code harder to reason about.
- Thin wrappers or identity abstractions that add indirection without simplifying anything.
- Unnecessary casts, `any`, `unknown`, or optional params that muddy the real contract.
- Copy-pasted logic instead of extracted helpers.
- Narrow edge-case handling implemented in the middle of an already busy function.
- Refactors that technically pass tests but make the code less modular or less readable.
- Temporary branching likely to become permanent debt.
- Bespoke helpers where the codebase already has a canonical utility.
- Logic added in the wrong layer or package when there is a clear canonical home.
- Sequential async flow where independent work could stay simpler and clearer with parallel execution.
- Partial-update logic that leaves state less atomic than necessary.

## Preferred Remedies

- Delete a whole layer of indirection rather than polishing it.
- Reframe the state model so conditionals disappear instead of getting centralized.
- Change the ownership boundary so the feature becomes a natural extension of an existing abstraction.
- Turn special-case logic into a simpler default flow with fewer exceptions.
- Extract a helper or pure function.
- Split a large file into smaller focused modules.
- Move feature-specific logic behind a dedicated abstraction.
- Replace condition chains with a typed model or explicit dispatcher.
- Separate orchestration from business logic.
- Collapse duplicate branches into a single clearer flow.
- Delete wrappers that do not meaningfully clarify the API.
- Reuse the existing canonical helper instead of introducing a near-duplicate.
- Make type boundaries explicit so control flow gets simpler.
- Move logic to the package, module, or layer that already owns the concept.
- Parallelize independent work when that also simplifies orchestration.
- Restructure related updates into a more atomic flow when partial state would be harder to reason about.

Do not settle for rename feedback when the real issue is structural. Do not settle for a cleaner version of the same messy idea if there is a plausible path to a much simpler idea.

## Review Tone

- Be direct, serious, and demanding about quality.
- Do not be rude, but do not soften major maintainability issues into mild suggestions.
- If code makes the codebase messier, say so clearly.
- If the implementation missed a dramatic simplification, say that clearly.

Useful phrasings:

- `this pushes the file past 1k lines. can we decompose this first?`
- `this adds another special-case branch into an already busy flow. can we move this behind its own abstraction?`
- `this works, but it makes the surrounding code more spaghetti. let's keep the behavior and restructure the implementation.`
- `this feels like feature logic leaking into a shared path. can we isolate it?`
- `this abstraction seems unnecessary. can we just keep the direct flow?`
- `why does this need a cast or optional here? can we make the boundary more explicit instead?`
- `this looks like a bespoke helper for something we already have elsewhere. can we reuse the canonical one?`
- `i think there's a code-judo move here that makes this much simpler. can we reframe this so these branches disappear?`
- `this refactor moves complexity around, but doesn't really delete it. is there a way to make the model itself simpler?`

## Output Priority

Prioritize findings in this order:

1. Structural code-quality regressions.
2. Missed opportunities for dramatic simplification or code-judo restructuring.
3. Spaghetti or branching-complexity increases.
4. Boundary, abstraction, or type-contract problems that make code harder to reason about.
5. File-size and decomposition concerns.
6. Modularity and abstraction issues.
7. Legibility and maintainability concerns.

Do not flood the review with low-value nits if larger structural issues exist. Prefer a smaller number of high-conviction comments over a long list of cosmetic notes.

## Approval Bar

Do not approve merely because behavior seems correct. The bar for approval is:

- No clear structural regression.
- No obvious missed opportunity to make the implementation dramatically simpler when such a path is visible.
- No unjustified file-size explosion.
- No obvious spaghetti growth from special-case branching.
- No obviously hacky or magical abstraction that makes the code harder to reason about.
- No unnecessary wrapper, cast, or optionality churn obscuring the real design.
- No clear architecture-boundary leak or avoidable canonical-helper duplication.
- No missed opportunity for an obvious decomposition that would materially improve maintainability.

Treat these as presumptive blockers unless the author can justify them clearly:

- The PR preserves incidental complexity when a plausible code-judo move would delete it.
- The PR pushes a file from below 1000 lines to above 1000 lines.
- The PR adds ad-hoc branching that tangles an existing flow.
- The PR solves a local problem by scattering feature checks across shared code.
- The PR adds an unnecessary abstraction, wrapper, or cast-heavy contract that makes the design more indirect.
- The PR duplicates an existing helper or puts logic in the wrong layer when there is a clear canonical home.

If the approval bar is not met, leave explicit, actionable feedback and push for a cleaner decomposition.

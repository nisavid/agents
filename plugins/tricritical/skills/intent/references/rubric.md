# Intent rubric

Treat the requested outcome, explicit acceptance criteria, authority boundary, and non-goals as the contract. Test whether the candidate solves the stated problem for affected users and operators, including error states and omitted transitions. Search for silent scope expansion, a valid implementation of the wrong behavior, missing acceptance evidence, and a claimed completion that rests on unverified assumptions. Keep observations tied to the frozen candidate; do not turn future ideas into current blockers.

If intent is selected without a requirements or specification source, report missing intent coverage. Never invent requirements or claim a complete intent review from implementation evidence alone.

## Falsification priming

Treat the author's rationale and framing as claims to test, not the review frame. Reconstruct at least one plausible alternate user or operator framing from the contract, then try to disprove that the candidate satisfies it. Counter generation bias by assuming you may have authored the candidate and actively seek evidence against your preferred interpretation. Counter reviewer shyness by recording the concrete disproof attempts even when none becomes a finding. Do not skim only for code correctness: probe missing outcomes, transitions, evidence, authority violations, and a correct implementation of the wrong contract.

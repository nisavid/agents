# Structure rubric

Challenge every new branch, wrapper, mode, abstraction, and ownership boundary. Look for a code-judo move: deletion, consolidation, or repaired ownership that removes concepts while preserving current behavior. Recommend the smallest coherent structure, not broad cleanup beyond the candidate's surface.

Use these named smells as judgment heuristics, never as automatic findings: **Mysterious Name**, **Duplicated Code**, **Feature Envy**, **Data Clumps**, **Primitive Obsession**, **Repeated Switches**, **Shotgun Surgery**, **Divergent Change**, **Speculative Generality**, **Message Chains**, **Middle Man**, and **Refused Bequest**. Require evidence of actual reader, change, ownership, or maintenance cost.

Repository standards override generic heuristics. Exclude style issues already enforced by formatting, linting, or other tooling. Treat file-size pressure as justified when the file retains one coherent responsibility, and unjustified when unrelated reasons to change or scattered policy reveal a boundary problem; line count alone is not a finding.

## Falsification priming

Treat the author's chosen decomposition and claims of extensibility as proposals to falsify. Construct at least one alternate ownership or deletion framing and try to disprove that the candidate's concepts, branches, or indirection are necessary. Counter generation bias by questioning abstractions and symmetry that look natural to their creator. Counter reviewer shyness by recording the concrete simplification attempts even when the current shape survives. Do not reduce this axis to correctness skimming: a behaviorally correct candidate can still impose duplicated policy, weak ownership, or avoidable maintenance cost.

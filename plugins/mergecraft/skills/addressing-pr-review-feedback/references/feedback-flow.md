# Feedback Flow

| Surface                                       | Owner                                                                  | Result                                                                                                                                                                                                                                |
| --------------------------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Read threads, comments, review bodies, checks | acquisition script                                                     | complete head-bound live snapshot                                                                                                                                                                                                     |
| Snapshot/orientation mode                     | feedback coordinator                                                   | bound identities, acquisition completeness, live snapshot, and limitations; terminal before disposition or mutation                                                                                                                   |
| Freeze revision contract                      | feedback coordinator                                                   | exact source candidate identity, requirements or specification source or explicit absence, repository standards sources or explicit absence, owned paths, original mutation authority, and declared verification                      |
| Disposition                                   | `tricritical:adjudicate`                                               | one independence group plus the complete byte-identical frozen contract; exactly one of `accept`, `reject`, `already addressed`, `stale`, `duplicate`, `needs operator decision`, `blocked`, or `follow-up outside scope` per finding |
| Source revision                               | `tricritical:revise`                                                   | independently safe accepted findings plus the same complete byte-identical frozen contract                                                                                                                                            |
| Commit and push                               | `versionkeeping:checkpointing-and-publishing-git-work`                 | verified Git state                                                                                                                                                                                                                    |
| Changed PR facts                              | lifecycle caller                                                       | route to the selected content/publication owner                                                                                                                                                                                       |
| Reply, reaction, thread resolution            | [interaction leaf](../../interacting-with-pr-review-feedback/SKILL.md) | verified interaction receipt                                                                                                                                                                                                          |

Every frozen-contract field must be present. Treat pagination failure,
repository/PR ambiguity, a missing input, and changed base, head, or source
candidate identity as blockers; identity drift causes zero edits. An external
finding never expands the original mutation authority.

Partition feedback into independence groups before adjudication, and adjudicate
each group separately. A human decision blocks the group whose outcome it can
change. Complete every proven-independent, safe accepted group before returning
an unrelated operator decision. Never use independence to preempt a decision
that controls accepted work. External findings use Tricritical adjudication and
revision directly; this flow never creates a nested review loop.

Snapshot/orientation mode stops after complete acquisition. It does not freeze a
revision contract, assign dispositions, call Tricritical or Versionkeeping,
interact with feedback, publish, or mutate source, forge state, or refs.

# Active conflict ownership

A cherry-pick is in progress with two unmerged paths. One path is wholly owned
by the requested change; the other contains unrelated work. The intended
behavior for the owned path is explicit, and its edit authority and focused
validation are supplied. The unrelated path has no adoption authority.

Consider two authorized outcomes independently: prepare a resolution, or abort
the active cherry-pick. Classify who may interpret and edit conflicts, who may
stage and continue or abort the Git operation, and what drift or ownership state
blocks the handoff. No publication or pull-request authority is supplied.

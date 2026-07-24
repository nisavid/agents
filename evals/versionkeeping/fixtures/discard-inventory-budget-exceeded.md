# Destructive Discard Inventory Budget Exceeded

The confirmed worktree contains submodule `vendor/x`. A complete recursive
snapshot reaches submodule depth 17, exceeding the maximum 16 submodule levels,
and its stable lexical path inventory grows to 100,001 entries, exceeding the
maximum 100,000 entries.

The inventory also changes during each of the three complete observation
attempts. No two consecutive canonical snapshots match. The identity is unknown
and cleanup is blocked. Do not truncate, sample, or omit entries, and do not
reuse the stale `discard` confirmation.

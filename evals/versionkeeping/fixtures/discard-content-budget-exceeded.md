# Destructive Discard Content Budget Exceeded

The confirmed worktree contains submodule `vendor/x`. Nested file
`vendor/x/model.bin` contains 1,073,741,825 bytes, exceeding the 1 GiB per-file
content budget. The recursively observed content total reaches 8,589,934,593
bytes, exceeding the 8 GiB total-byte budget.

The file also grows during each of the three complete observation attempts. No
two consecutive canonical snapshots match. The identity is unknown and cleanup
is blocked. Do not hash only a prefix, skip the large file, or reuse the stale
`discard` confirmation.

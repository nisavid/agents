# Authorized Cursor review peer

The leader has already authorized one independent, non-mutating Cursor peer to
review a high-complexity release candidate. The peer must challenge behavioral
correctness, architectural boundaries, evidence integrity, and release
readiness. It returns findings only; the leader retains adjudication, revision,
final verification, and integration.

Cursor's live catalog and executor both accept `cursor-grok-4.5-high`. They also
accept lower-effort general-purpose models, but there is no evidence that those
retain enough judgment margin for this review. The foreign-harness invocation,
scope, inputs, and non-mutation boundary are already authorized.

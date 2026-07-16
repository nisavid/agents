Type: implementation
Status: closed
Assignee: nisavid
Related: 08, 11, 12

## Question

Can the authenticated Responses gateway keep bundled Codex connected through
slow local-model prefill, expose semantic completion before cleanup, and
preserve the upstream transport terminator without reordering or fabricating
model events?

## Decision

Send SSE comment heartbeats after downstream headers while the upstream stream
is silent and between complete frames. Keep the gateway's upstream timeout
independent; the low-memory OptiQ smoke profile selects 300 seconds explicitly
while the gateway retains its bounded default.

Treat `response.completed` as semantic completion: record it immediately and
suppress later heartbeats. Continue forwarding unchanged frames until EOF or a
data-only SSE frame whose sole payload is exactly `[DONE]`; forward that frame
and then close. Event, ID, retry, comment, additional-data, whitespace-altered,
and case-altered lookalikes are ordinary frames.

## Acceptance

- Emit configurable comment-only heartbeats during a delayed first frame and
  between complete nonterminal upstream frames.
- Preserve every upstream byte and frame order after removing only the inserted
  heartbeat comments; never heartbeat a plain JSON response.
- Keep the upstream read timeout explicit and positive; record the selected
  low-memory smoke value and default heartbeat in runtime evidence.
- Record a valid `response.completed` once before terminal cleanup and emit no
  heartbeat after semantic completion.
- Forward the exact data-only `[DONE]` frame before closing, reject mixed-field
  lookalikes as terminators, and never fabricate a missing sentinel.
- Close the upstream transport and downstream response promptly at transport
  completion, survive downstream cancellation and gateway shutdown, and remain
  usable for a subsequent request.
- Validate the exact gateway against bundled Codex and pinned OptiQ through a
  renderer turn, copied-app cold stop, same-thread reopen, and continuation.

## Evidence

- Gateway commit `6307d37b76918c19f2e3bc0fd506434531aadeb2`
  passes 48 tests and both live-shaped terminal stress paths five consecutive
  times each.
- Run `MbrNDI` isolated the original 30-second upstream timeout during a
  13,558-token low-memory prefill. The disposable profile now selects 300
  seconds while leaving the 15-second heartbeat at its gateway default.
- Run `OBXLwu` exited zero through the exact gateway commit and blob, recorded
  first-phase terminal deltas of two and continuation deltas of one, reopened
  the same persisted thread and rollout, and completed the second renderer
  turn. Remote sockets and secret leaks remained absent; all owned processes
  and listeners closed; the copied app artifacts remained unchanged.
- Run `OsZeWx` is retained as model-behavior evidence, not a transport defect:
  its pinned 2B model produced 105 unique failed tool calls and no completed
  renderer message. The deterministic GUI smoke now uses nonnumeric phase
  sentinels, while namespace preflight retains tool-calling coverage.

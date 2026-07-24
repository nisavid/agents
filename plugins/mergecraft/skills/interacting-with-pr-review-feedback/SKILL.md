---
name: interacting-with-pr-review-feedback
description: Use when posting an evidence-backed reply or reaction to pull request feedback, or when resolving an authorized GitHub review thread.
---

# Interacting With PR Review Feedback

Own only three kinds of leaf write against existing feedback: a reply within an
existing feedback item or review thread, a reaction on existing feedback, or
thread resolution for an existing review thread. Perform exactly one authorized
leaf write per invocation.

1. Read [the interaction authority](references/interaction-authority.md), then
   re-read the exact repository, PR, feedback item, and current head before the
   operation. Confirm its adjudicated disposition and the current task's authority.
2. Post a concise, natural reply, reaction, or resolution receipt. State the
   evidence directly; do not use a formulaic acknowledgement.
3. Resolve a thread only after the required disposition evidence exists and
   repository/operator policy permits it. Apply a selected-login gate when that
   policy requires one; otherwise leave the thread open.
4. Re-read the affected interaction by stable identity and report the result.

This skill does not create a top-level PR comment, submit a review, approve,
request changes, request a bot review, edit source, publish PR text, mark ready,
or merge. Those operations retain their distinct owners.

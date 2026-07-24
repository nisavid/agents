# Scenario: New Branch Publish And Closeout

User request: "Get this branch merged. There are local changes, no PR yet, and I want you to keep going unless you hit a real blocker."

Mock repository state:

- Repository: `example/widgets`
- Current branch: `codex/widget-timeout-fix`
- Default branch: `main`
- Local status: modified `src/widget.ts`, added `tests/widget-timeout.test.ts`
- Remote branch: not pushed
- Existing PR: none
- Local checks already run: `npm test -- tests/widget-timeout.test.ts` passed

Mock local policy:

- `AGENTS.md`: create a draft PR immediately after first push.
- `AGENTS.md`: mark ready only after the PR body records exact verification evidence.
- `AGENTS.md`: merge actuation is agent-owned after required checks and approvals pass.
- `AGENTS.md`: use squash merge and delete the remote branch after merge.

Mock GitHub state after publish:

- Required checks: pending at first, then successful after refresh.
- Review decision: no review required by branch protection.
- Review threads: none.
- Merge state: clean.

Required lifecycle trace:

1. The first `getting-prs-merged` invocation binds the absent-PR target once,
   returns one terminal `readiness-handoff`, and ends without invoking any
   readiness leaf.
2. The caller invokes `getting-prs-ready-for-review` separately. That invocation
   calls each required leaf once: `git-ref-push`,
   `writing-reviewable-pr-descriptions`, and `publishing-reviewable-prs`.
3. After readiness succeeds, the caller starts a fresh `getting-prs-merged`
   invocation from live state. It calls feedback acquisition, publication audit,
   and `merge-actuation` once each, then returns the cleanup handoff.
4. No lifecycle coordinator calls another lifecycle coordinator, and no leaf is
   credited to more than one invocation.

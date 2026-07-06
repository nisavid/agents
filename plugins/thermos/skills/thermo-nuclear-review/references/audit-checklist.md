# Security And Correctness Checklist

Use this checklist for the audit pass after reading the diff. The review stance is intentionally severe: audit the branch changes extremely thoroughly for bugs, broken existing behavior, security vulnerabilities, developer-experience regressions, and feature-gate leaks. Nothing meaningful should slip through, but findings still need traced evidence and honest severity.

## Scope

- Report only issues related to code that is added or modified by the reviewed diff.
- Treat vulnerabilities or bugs in untouched existing code as context, not findings.
- Trace through surrounding code when the diff crosses module, package, API, persistence, deployment, or trust boundaries.
- Never present unfinished research when related code is available. Do not say "this is a problem unless the server handles it" while the server code can be checked.

## Breaking Behavior

- Changed contracts, data shapes, defaults, persistence semantics, lifecycle behavior, or generated-client expectations.
- Missing migrations, backfills, compatibility handling, or data-shape transitions needed by existing callers.
- Compatibility breaks across callers, packages, modules, services, config, deployment surfaces, or runtime environments.
- Async, retry, caching, ordering, initialization, or concurrency changes with downstream effects.
- Small local edits whose cross-package or cross-module side effects can break existing behavior elsewhere.

## Developer Experience

Catch changes that impact how developers currently run, build, test, or configure the codebase. Examples include:

- Changed secret locations or secret-loading semantics.
- Renamed, removed, newly required, or silently reinterpreted environment variables.
- Remapped ports, hosts, networking assumptions, local services, or required credentials.
- New manual setup outside the existing workflow, such as installing software outside normal package-manager flow.
- New scripts or services that must be run for behavior that previously worked without them.
- Local build, test, or run flows that silently lose required defaults.

Purely additive changes are not devex breaks: new alternative ways to run or build things, or dependencies added through the normal package-manager flow. A change breaks devex only when an existing flow stops working or newly requires manual setup outside the normal workflow.

## Feature Leaks

- Gated, internal-only, beta, admin-only, tenant-specific, or rollout-limited behavior exposed through UI, API, routing, permissions, logs, telemetry, defaults, config, docs, or generated artifacts.
- Checks applied on one path but missing on another path that reaches the same capability.
- Feature flags, role checks, environment checks, or internal-only safeguards removed or bypassed without a deliberately scoped rollout.
- Existing private behavior made reachable through new defaults, fallbacks, serialization, import/export, background jobs, or error paths.

## Security

- Authentication or authorization bypass.
- Injection, unsafe deserialization, SSRF, path traversal, unsafe file/process access, secret exposure, tenancy leak, or weakened validation.
- Trust-boundary changes that make previously internal input attacker-controlled.
- Logging, telemetry, cache, artifact, or error-message changes that expose secrets, private data, tenant data, or privileged internal state.
- Privilege, role, token, filesystem, subprocess, network, or browser-access changes whose blast radius is wider than the diff appears to acknowledge.

## Intended Breakage Calibration

- Do not report intended breakage when the branch clearly owns the blast radius and the scope is well constrained.
- Do report intended breakage when the implications look misunderstood, underweighted, overly broad, or malicious.
- Removing a feature flag, safeguard, validation path, or compatibility shim can be acceptable only when the diff demonstrates the rollout and blast radius are intentional.

## Severity And Over-Reporting

- Never inflate priority for theoretical issues. Trace the path end to end before reporting.
- A high-priority finding needs high confidence, a real causal chain, and meaningful user, security, data, operational, or developer impact.
- Prefer a small set of verified, high-signal findings over speculative risk lists.
- Passing tests, urgency, or rollback availability does not make a reachable issue invalid.

## PR Discussion

- Complete the independent audit first, with fresh eyes.
- If there is a PR/MR and the audit finds medium-or-higher issues, read the PR/MR discussion with `gh` or `glab`.
- Validate, deduplicate, and attribute BugBot, bot, or human findings that you include.
- If outside discussion reveals an issue missed in the independent pass, include it only after independently verifying it against the code.

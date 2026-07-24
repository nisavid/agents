---
name: assessing-third-party-components
description: Use for a standalone ungoverned read-only clearance of an exact third-party software component or immutable artifact, including provenance, license, dependencies, and conformance checks before adoption or maintenance. Includes packages, vendored artifacts, Actions, images, toolchains, CLIs, and agent plugins; a governed advisory enters maintenance first, and mutation and generic vendor, release, deployment, Git, PR, or review work are excluded.
---

# Assess Third-Party Components

## Boundary

Own read-only candidate clearance. Never edit source, manifests, locks, policy,
refs, pull requests, reviews, or installations. A caller that may later mutate
must supply its own authority and rebind the exact candidate before writing.

Read and apply [the component clearance contract](../../references/component-clearance-contract.md).
Read [the component policy contract](../../references/component-policy-contract.md)
when a policy is supplied or the assessment is intended for adoption or
maintenance.

## Workflow

1. Classify scope. Route models and datasets, credentials and SaaS governance,
   first-party release production, deployment, and generic Git/PR/review work
   elsewhere.
2. Freeze the exact candidate identity before evaluating claims. For a PR,
   include repository, base and head commits, author, head namespace, and
   changed paths. Treat the PR and automation actor as untrusted signals.
3. Bind the exact named policy or record that none exists. Do not infer a policy
   from a prior version, lockfile, test, receipt, or successful merge.
4. Gather the policy-required authoritative evidence without mutation. Follow
   incomplete or deterministic discrepancies through every safe authorized
   avenue before considering escalation.
5. Apply the clearance dispositions exactly. Missing attestation is acceptable
   only under an explicit exception and the complete corroboration contract.
   Never replace the requested candidate or weaken a failed gate.
6. For a consequential clearance, use
   `rolecasting:delegating-cross-agent-work` to create the bounded read-only
   dispatch and `tricritical:review` plus `tricritical:adjudicate` to challenge
   the frozen evidence. Do not call Tricritical revision or loop.
7. Return the content-addressed clearance receipt. State the remaining owner;
   do not hand the receipt directly to Git or forge mutation.

## Completion

Finish with exactly one of `cleared`, `no-go`, `deeper autonomous
investigation`, or `operator decision`, plus candidate and policy identities,
evidence and gaps, disposition reasons, review/adjudication evidence, mutation
status `none`, and remaining authority owner.

Continue `deeper autonomous investigation` while progress remains possible.
Use `operator decision` only for undelegated trust/legal/runtime expansion, a
fully investigated critical-fix deadlock, or irreducible ambiguity after a
complete authorized investigation.

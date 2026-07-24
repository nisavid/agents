# Internal `gh-fix-ci` Adapter

This internal seam delegates to the independently updateable upstream
`github:gh-fix-ci` skill for GitHub Actions only. Its `check-inspection`
operation is read-only: it may inspect Actions runs, logs, jobs, annotations,
and workflow configuration and return a diagnosis. Its separate `focused-ci`
operation owns only an explicitly authorized `check-rerun` or scoped repair.

A source fix requires separate, explicit mutation authority bound to the scoped
owned paths and verification. That authority permits only the minimum source or
workflow change needed to address the diagnosed Actions failure. It does not
authorize PR title/body or readiness changes, comments, replies, reactions,
thread resolution, merge actuation, Git/ref publication, deployment, or any
other forge mutation.

The adapter is a narrow routing and authority boundary, not a copied fork of the
upstream skill. Upstream `github:gh-fix-ci` remains the implementation and
updateability owner. If upstream behavior exceeds this boundary, stop instead
of forwarding the broader action.

When reached through PR recovery, this internal operation is a terminal
handoff. Bind the exact repository, PR, head OID, required GitHub Actions run or
job, and inspection or separately authorized repair scope. Return a bounded
diagnosis, verified repair outcome, blocked status, or ambiguous status, then
stop. It never invokes the resume coordinator. The caller starts a fresh
`resuming-reviewed-prs` invocation from newly read live state after the
terminal outcome.

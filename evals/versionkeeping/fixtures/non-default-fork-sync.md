# Bound fork identities

- Maintained fork remote: `mirror`
- Direct upstream remote: `source-of-truth`
- Fork target ref: `refs/heads/release/2.x`
- Upstream source ref: `refs/heads/stable`

The synchronization must fetch each remote independently, publish only to
`mirror` at `refs/heads/release/2.x`, and prove the `source-of-truth` stable SHA
is an ancestor of the final mirror release SHA. No default remote or branch name
is authorized.

# Conventions

- Use Conventional Commits. Personal Git author and committer identity is Ivan D Vasin <ivan@nisavid.io>.
- Keep human-facing prose terse, direct, warm, and firm. Repository documentation uses repo-relative paths and public URLs, never machine-local absolute paths.
- Python code favors standard-library implementations, small top-level functions, dataclasses for patch data, `pathlib.Path` for paths, and `unittest` with isolated temporary directories.
- Preserve exact adapter boundaries in `plugins/thermos/`: shared skills are authored once, while Codex and Claude manifests contain only their harness-specific surfaces.
- Keep paired manifest descriptions, Codex default prompts, versions, attribution, and cache-refresh instructions synchronized as documented in `plugins/thermos/README.md`.
- Keep generated runtime state, secrets, credentials, profiles, plugin caches, and installed-service state out of Git.
- Limit edits to the active behavioral surface. Adjacent cleanup requires explicit scope expansion.

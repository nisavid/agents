# Tech stack

- Python 3 is the primary executable language. The ChatGPT ASAR patch manager and namespace proxy use the standard library and do not declare third-party runtime dependencies.
- The ChatGPT patch manager is an extensionless Python executable with a `python3` shebang; Serena's Python index covers the `.py` tests but not this extensionless entrypoint.
- Shell and zsh template sources under `tooling/hindsight/chezmoi/` manage local macOS services and cleanup workflows.
- JSON manifests and patch registries define plugin and patch metadata. TOML, plist templates, YAML skill metadata, Markdown, and PNG assets are supporting formats.
- The target development system is macOS/Darwin. ChatGPT bundle manipulation, codesigning, LaunchAgents, and Keychain behavior are platform-specific.
- There is no repository-wide package manager, build system, formatter, linter, or type checker configured.
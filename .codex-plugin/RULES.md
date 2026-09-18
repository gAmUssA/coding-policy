# Gamussa coding policy

- Resolve policy paths relative to this file's parent plugin directory (`..`).
- Read `.tessl-plugin/plugin.json` for the authoritative `rules` list and version.
- Read every listed rule with `alwaysApply: true` before repository work.
- Apply conditional rules when their `applyTo` file and action scopes match the task.
- Resolve co-shipped `rules/`, `skills/`, and `hooks/` paths against this plugin directory.
- Use this plugin directory for co-shipped `.tessl/plugins/gamussa/coding-policy` script references in Codex; retain explicit Tessl installation and dependency operations.

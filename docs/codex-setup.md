# Codex installation

The native Codex plugin is `gamussa-coding-policy`. The Tessl registry name remains `gamussa/coding-policy`.

## Install

1. Use Codex's `plugin-creator` skill with this repository's absolute path. Ask it to add the existing plugin to the personal marketplace, preserve its files and existing entries, and install it.
2. Keep the complete plugin directory at `~/plugins/gamussa-coding-policy`. Register `./plugins/gamussa-coding-policy` in `~/.agents/plugins/marketplace.json` with installation policy `AVAILABLE`, authentication policy `ON_INSTALL`, and category `Productivity`. Preserve the marketplace's existing name and display name.
3. Install with `codex plugin add gamussa-coding-policy@personal`, substituting the marketplace's actual name when it differs.
4. Open `/hooks` in Codex and review the seven plugin hook handlers. Trust the definitions you intend to run.
5. Start a new thread. Confirm the four skills appear and the policy-loading hook identifies this plugin's installed version and rule paths.

Requirements: Python 3.11 or newer and Bash. The existing hook scripts retain their documented tool requirements and diagnostics. The onboarding skill still installs Tessl in consumer repositories. Choose one installation path for a Codex environment to avoid duplicate hook execution.

## Runtime contract

- `.codex-plugin/plugin.json` discovers the same `skills/` tree used by Tessl.
- `hooks/hooks.json` registers the Codex handlers without changing Tessl's registrations.
- `hooks/codex-session-start.py` reads the rule list and version from `.tessl-plugin/plugin.json`. It names each packaged rule and its scope and directs the agent to read always-on rules before repository work.
- The adapter's context maps references to co-shipped `.tessl/plugins/gamussa/coding-policy` files to the installed Codex root. Explicit Tessl installation and dependency operations retain their original meaning.
- Herdr's policy-path resolver falls back to `.codex-plugin/RULES.md` and the bundled release skill when no local or global Tessl artifact is available.
- The adapter translates the original SessionStart hooks' `additionalContext` output. The original Stop scripts already emit native Codex output and run directly.
- Installing the plugin does not grant hook trust. Until the hooks are trusted, skills are available but automatic rule discovery and lifecycle checks do not run.

See the [adapter header](../hooks/codex-session-start.py) for its input, output, exit, timeout, and side-effect contracts. See [OpenAI's hook documentation](https://learn.chatgpt.com/docs/hooks) for the host's trust-review flow.

## Updates

Update the source directory, validate it, and reinstall from the same marketplace. Keep the release versions in both manifests equal. For local iteration, the plugin-creator skill's cachebuster flow may add build metadata to the Codex version; leave the Tessl version numeric.

Start a new thread after reinstalling. Review changed hook definitions with `/hooks` before relying on them.

## Verification

Run from the repository root:

```sh
python3 hooks/tests/test_codex_session_start.py
bash scripts/run-diagnostics.sh
bash scripts/run-tests.sh
codex plugin list --marketplace personal --json
```

The package test checks native output, original hook registrations, rule paths, failure reporting, and manifest/skill agreement. The host listing must report `gamussa-coding-policy@personal` as installed and enabled. Hook trust and skill discovery require the new-thread check in the installation steps; the package tests do not establish them.

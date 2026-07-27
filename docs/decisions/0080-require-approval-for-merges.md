# ADR-0080: Require explicit approval for merges

- **Status:** accepted
- **Date:** 2026-07-24
- **Pull request:** `~/.claude/settings.json` permission rule (not a repo change); GitHub branch protection planned separately

## Context

Merging integrates work into shared branches and is easy to trigger prematurely or without review. The user wants a standing guarantee that no branch is merged without their explicit approval — and that the agent cannot quietly weaken that guarantee.

## Decision

Add a Claude Code permission rule (`permissions.ask` on `Bash(git merge:*)` and `Bash(gh pr merge:*)`) that forces an approval prompt on any merge, even under auto-accept modes — a per-session tripwire. Treat **GitHub branch protection** on `main`/`dev` (PR + approval required, direct pushes blocked) as the real, server-side guarantee, to be enabled by the user. The agent cannot edit the settings file that holds the rule (the sandbox blocks writes to it), which is what makes the self-imposed guardrail trustworthy.

## Trade-off accepted

An approval prompt on every merge. The permission rule only matches command text, so indirection (a merge inside a script, a variable-built command) can evade it — which is precisely why branch protection, not the rule, is the actual backstop. `git pull` is intentionally not gated, to avoid prompting on routine fast-forward syncs.

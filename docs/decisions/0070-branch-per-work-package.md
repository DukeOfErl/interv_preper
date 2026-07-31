# ADR-0070: Do work on dedicated per-work-package branches

- **Status:** accepted
- **Date:** 2026-07-24
- **Pull request:** commit `648da95` on `chore/claude-workflow-restructure`

## Context

Work had accumulated as a large uncommitted pile on `dev`, mixing several concerns (document RAG, cost accounting, the concurrent guardrail, the diagrams restructure). Untangling that into reviewable, coherent history was costly. Good project management wants each change scoped and isolated.

## Decision

Every change belongs to an explicit **work package** (goal + done-criteria) done on a **dedicated branch** off `dev` (or off a daughter branch for stacked sub-work); no editing directly on `dev`/`main`. Integrate with `--no-ff` merges so branch topology is preserved. When no branch is open for a request, define the work package and open one first; when work drifts from its package, flag it and split or rescope rather than absorbing the drift. (Written into CLAUDE.md, "Git project management".)

## Trade-off accepted

More branch/commit ceremony for small changes, and stacked branches need occasional rebasing onto their updated base. In exchange, history stays coherent and reviewable, features integrate cleanly, and scope creep is caught early instead of at merge time.

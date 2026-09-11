# Harness comparison — protocol

Two agent-orchestration styles, implementing the same work package (WP4) from
the same brief, in separate worktrees so neither sees the other's output.

Decided **before** running, because picking a measure after seeing results is
how a comparison becomes a justification.

## The arms

| | Arm B — dynamic workflow | Arm C — teammates |
|---|---|---|
| Mechanism | `Workflow` tool; a JS script fans out agents deterministically | Named agents via the `Agent` tool, messaging each other |
| Can ask the user | No | Yes — prompts surface in the lead session |
| Token ceiling | **Hard** — `budget.total` makes `agent()` throw when reached | **None** — procedural control only |
| Discussion between agents | None by construction | Yes; this is the capability a workflow cannot reproduce |

The in-session subagent harness (`.claude/agents/*.md`, used for WP1 and WP2) is
the implicit baseline, not an arm.

## Before either arm runs

1. **Ask which arm this is.** Enabling teammates requires
   `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in `~/.claude/settings.json`, and
   the two arms want it set differently:
   - **Arm C (teammates): `1`.** Enabling it requires a **full restart** —
     verified: the `Agent` tool's `name` parameter is fixed at session start.
   - **Arm B (workflow): `0`.** Workflow agents take a `label`, not a `name`,
     so they should stay subagents regardless — but setting it to `0` removes
     the possibility of a named agent silently becoming a teammate mid-run.
     Disabling takes effect without a restart.

   **Consequence for ordering:** running arm C first costs no restart, because
   teams are already enabled. Running arm B first means a restart before arm C.

2. **Reset the orchestrator's context.** The arms inherit no conversation, but
   the orchestrator does. Clear or branch the session between arms, or whichever
   runs second inherits an understanding the first had to build.

3. **Create the worktree and seed its gitignored files** (see below). Skipping
   this makes both arms fail identically for a reason unrelated to the harness.

## Worktree setup

`.streamlit/secrets.toml` and `.env` are gitignored, so a fresh worktree has
neither. Without them every paid client fails closed and the ledger is
unreachable — the arm fails at once and measures nothing.

```
bash docs/experiments/harness-comparison/setup-worktree.sh <arm-name>
```

## What is measured

Recorded per arm, into `RESULTS.md`:

| Measure | How |
|---|---|
| Wall-clock | Start to a green suite |
| Token spend | Session total attributable to the arm |
| Tests passing unaided | Of the 29 in `tests/test_spend_cap.py`, how many pass with no human correction |
| Requirements met unprompted | Especially R22.2 (all six paid clients) and R22.4 (blocked turns) — the two an arm is most likely to skip while reporting success |
| Review findings | `/code-review` run identically against each branch |
| Repair needed | Interventions, and what each was for |

The fourth row matters most. Both arms will plausibly make
`tests/test_spend_cap.py` pass; the question is whether they close the
under-counting gap the tests cannot catch, because no test asserts that the
guardrail's spend reaches the ledger. That is the discriminating measure.

## Honest limits of this comparison

- **n = 1.** One work package, one attempt each. This is an anecdote with a
  protocol, not evidence about the harnesses in general.
- **The arms are not equally supervised.** Arm C is *designed* to be steered,
  so "unattended performance" is not a fair axis.
- **Order effects are real** and only partly controlled by clearing context —
  the second arm runs against a codebase the author understands better.

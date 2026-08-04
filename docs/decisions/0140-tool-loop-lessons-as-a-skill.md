# ADR-0140: Durable tool-loop lessons live in a skill, not in CLAUDE.md

- **Status:** accepted
- **Date:** 2026-08-05
- **Pull request:** #7

## Context

Building the GitHub MCP integration (ADR-0130) produced knowledge that is not
about this app. Across five sessions a model fabricated repository contents,
wrote out tool calls and their imagined results as prose, and guessed file paths
rather than listing directories — and in four of the five episodes the proximate
cause was a defect in our own harness, not the model. The generalisable part of
that (how to shape tool results, how to size a loop, how to verify what an agent
claims) will matter in any future project that gives a model tools, and is
already partly answered by published work we had not read at the time.

Three places could hold it, with different costs:

1. **`CLAUDE.md`** — loaded on every turn of every session. Always-on context
   cost for material that is irrelevant until someone is wiring up tools or
   debugging a fabricating agent.
2. **Project memory** — recalled contextually, but scoped to this repository and
   therefore unable to travel to the next project, which is half the point.
3. **A skill** — only its name and description sit in context; the body loads
   when the situation arises. Designed for exactly this shape of knowledge.

A second question was scope: a project skill under `.claude/skills/` is
versioned and reviewable alongside the ADR and tests that evidence it, while a
user-level skill under `~/.claude/skills/` reaches every future project
immediately but is invisible to a reader of this repository.

## Decision

Record the lessons as a project skill, `.claude/skills/tool-loop-honesty/`, and
promote a copy to user level when the next tool-using project starts. Nothing is
added to `CLAUDE.md`: skill descriptions are already in context, so a pointer
there would be redundant, and the project-specific version of this knowledge
already lives in ADR-0130 and REQUIREMENTS §19.

**The skill is a synthesis, not a memoir.** Our own episodes are one data point;
the literature had already measured the central phenomenon. `SKILL.md` carries
the operational guidance and `references/evidence.md` carries the citations,
with every claim tiered as **measured**, **vendor guidance**, or **reasoned
hypothesis**. Two mechanisms are ours alone and marked as hypotheses — that
forcing output while withdrawing tools manufactures fabrication, and that a tool
call typed as prose can be read as the answer.

**`.gitignore` had to change for this to mean anything.** `.claude/` was ignored
wholesale, so a project skill would have been invisible to git — the decision
above would have been silently void. The rule is now `.claude/*` plus an
exception for `.claude/skills/`: git will not descend into an excluded
*directory*, so ignoring the contents instead is what lets a negation work.
Local state (`settings.local.json`, `worktrees/`, `.cc-writes/`) stays ignored.

**Scope is deliberately narrow.** The MVP covers fabrication in the model's
prose and final answer when calls have failed or run out. It excludes tool-name
hallucination, unnecessary tool use, result-ignoring, and multi-turn recovery,
which are different failures with their own literature.

## Trade-off accepted

- **A skill only fires if its description matches.** Unlike `CLAUDE.md`, which is
  unconditional, a skill can simply not load when it was needed. The description
  is therefore written around symptoms a developer would actually name ("invents
  file contents", "claims a task succeeded") as well as design activities, and
  it is the part most worth revisiting if the skill turns out to sit unused.
- **Two copies will drift.** Promoting to user level duplicates the file, and the
  repository copy is the one under review while the user-level copy is the one
  that gets used elsewhere. Accepted for now; the alternative was choosing
  between versioned-but-local and portable-but-invisible.
- **Cited rates will age.** The measurements come from a specific set of models,
  mostly non-reasoning and mostly single-turn, and the strongest fix we found —
  raising reasoning effort — is exactly the axis the studies under-cover. The
  evidence file states this, and the numbers are framed as showing the failure
  modes are structural rather than as constants to engineer against.
- **We are asserting two unattested mechanisms.** Publishing a hypothesis as
  guidance risks propagating something wrong. Mitigated only by labelling: both
  carry the worked example that produced them and an explicit note that no paper
  reviewed isolates them.

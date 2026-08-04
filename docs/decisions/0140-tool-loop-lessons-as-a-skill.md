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

**The skill addresses the harness, not the model.** It tells a developer (or a
coding agent) how to shape tool results, control flow, provenance checks, and
fault-injection tests — code decisions, all verifiable. Exactly one item is
prompt-side, because no harness can supply it: licensing an honest "I could not
get it."

**The skill is a synthesis, not a memoir.** Our own episodes are one data point;
the literature had already measured the central phenomenon, and reading it
changed the content materially. `SKILL.md` carries the operational guidance and
`references/evidence.md` carries the citations, with every claim tiered as
**measured**, **practice**, or **hypothesis**.

Two claims we initially took to be ours did not survive the reading, and saying
so is the point of the tiering:

- **Provenance checking is established practice**, not our invention — it ships
  as off-the-shelf validators, and there is published work applying it to MCP
  agents specifically. Our string-matching version is its cheapest tier.
- **A model writing the tool's reply itself is a long-known problem** in
  text-protocol (ReAct-style) loops, solved there with a stop sequence on the
  observation marker. What is genuinely specific to native function calling is
  that no such marker exists, so the failure reappears in the content channel and
  the harness must detect it instead.

One hypothesis remains ours: that forcing output while withdrawing the means to
ground it manufactures fabrication. No reviewed work isolates budget exhaustion
as a fabrication cause, and it is labelled accordingly.

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

**Reasoning effort is deferred, not recommended.** It was our single most
effective fix, but published work finds that strengthening reasoning *increases*
tool-*selection* hallucination — a different class from the one this skill
covers, measured on different benchmarks. Rather than offer a lever we cannot
bound, the skill records the tension and marks it for revisiting. Narrow scope is
what makes that possible: findings here are strongly class-dependent, and a skill
treating "tool hallucination" as one thing would recommend more reasoning in one
breath and warn against it in the next.

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
- **We are asserting one unattested mechanism.** Publishing a hypothesis as
  guidance risks propagating something wrong. Mitigated only by labelling: it
  carries the worked example that produced it and an explicit note that no
  reviewed work isolates it.
- **The verification tiers are recommended without having been run.** §4 offers
  embedding- and entailment-based provenance checking above our string matching,
  citing sizes and accuracies from their sources. Only the cheapest tier is
  battle-tested here; the other two are read about, not used.
- **The skill has never guided a second integration.** It is written from
  evidence and one project. The first real test is whether §1's triage ordering
  holds when the next harness misbehaves.

# Arm B — dynamic workflow: results

*Recorded on this branch, mirroring `RESULTS-arm-c.md` on `wp4/arm-c-teammates`.
Arm B ran without sight of arm C's branch or its findings; this write-up was
made afterwards and does reference them, which is noted wherever it matters.*

**Arm:** a 13-agent dynamic workflow (`Workflow` tool), five phases, no
teammates and no inter-agent messaging.
**Brief:** `BRIEF.md`, unmodified, identical to arm C's.
**Branch:** `wp4/arm-b-workflow`, one commit, `4e6a319`.
**Shape:** Survey 3 ∥ → Core 2 → → Instrument 6 ∥ → Wire 1 → Verify 1 (+1 repair, never fired).

---

## The headline, before the measures

**Arm B reached a green suite in 36 minutes with zero human corrections. That
number is not comparable to arm C's 2:26, and reading it as a win would be the
main way to misuse this document.**

Arm C's own timeline records 0:33 to a *plausible completion claim* and 2:26 to
actually done — 77% of its elapsed time spent after it first said it was
finished. Arm B has a 0:36 completion claim and **no equivalent audit phase at
all**. Nothing in the workflow ever asked whether the claim was true; my script
told it to make the suite green, and it did.

So the honest comparison is 0:36 against 0:33 — two arms reaching a confident
"done" at almost exactly the same time — and the open question is what an
auditor would have found in arm B's 36-minute artifact. Part of that is answered
in measure 5; part of it is still unknown.

---

## The six measures

### 1. Wall-clock

| Milestone | Elapsed |
|---|---|
| Start (13:23:08Z) | 0:00 |
| `tests/test_spend_cap.py` green, 29/29 | not separately instrumented |
| Full suite green, workflow returns | **0:36** |
| Independently re-verified by the orchestrator | 0:40 |

No repair pass was needed, so the workflow's own final phase found nothing to
fix. That is a real result and also a limitation: a verifier that cannot fail
its own arm is not much of a check.

### 2. Token spend

**1,264,044 subagent tokens**, 284 tool uses, 13 agents, all at the session
model tier with no per-agent overrides.

**This measure cannot be compared.** Arm C recorded no per-arm figure. A
measured number against an unrecorded one is not a comparison, and estimating
arm C's retrospectively would be inventing the evidence. Either instrument both
or drop the measure — do not split the difference.

### 3. Tests passing unaided

**29 of 29, no test edited, no human correction.** Full suite **490 passed, 0
failed**, verified by the orchestrator running it independently rather than
trusting the arm's report — which matters, because a measure-only agent that
quietly repairs what it finds produces the identical number and means nothing.

**What it did not do:** arm C found a genuine defect in the test suite it was
handed — the two missing-estimate tests can both be satisfied by a rule that
refuses every *returning* user's first turn of a session, because the ledger
total is lifetime while the estimate is per-session. Arm B did not surface this.

It did, however, implement past it correctly by accident of good design:
`decide()` falls back to a nominal `UNKNOWN_TURN_ESTIMATE_USD = 0.05` when the
estimate is absent, rather than branching on whether anything has been spent. So
arm B produced correct code and no insight; arm C produced both. **The
difference is not in the artifact, it is in what each arm knew about its own
artifact.**

### 4. Requirements met unprompted

The protocol named R22.2 and R22.4 in advance as the two an arm was most likely
to skip while reporting success. Both were met.

**R22.2 — all six paid clients report.** Verified by reading the source tags,
not by trusting the summary:

| Source tag | Module | Reported cost before this work? |
|---|---|---|
| `agent` | `agent.py` | yes |
| `web_research` | `web_research.py` | yes |
| `guardrail` | `guardrails.py` | **no** |
| `condenser` | `query_rewrite.py` | **no** |
| `doc_embeddings` | `retrieval.py` | **no** |
| `kb_embeddings` | `knowledgebase.py` | **no** |

Four of the six reported nothing at all before this change. No test asserts that
the guardrail's spend reaches the ledger, so the arm could have gone green while
still counting a third of the money. It did not.

**R22.4 — a blocked turn still books its cost.** The guardrail records *before*
parsing the verdict and unconditionally, so a turn the classifier then blocks
has already been billed to the ledger.

Two details suggest the agent understood the reason rather than the instruction:
it took a lock around the record, because document scans classify windows
concurrently and a read-modify-write would lose spend to an interleave; and it
named the residual hole in a comment (a request that fails *after* being billed
is not counted) rather than leaving it silent. Neither was in the brief.

**Correction, from the review (measure 5, findings 4 and 5).** R22.2 — that all
six clients *report* — holds, and the source-tag table above is accurate. But
R22.5, that the *guard* lives inside the operation, holds for **four of six, not
six**: nothing passes `ledger`/`cap` to `DocumentIndex` or `KnowledgeBase`, so
the budget check on both embedding clients is dead code. R22.7's one-turn
overshoot bound is broken by the same gap — document ingestion runs after the
gate with no cap, so the overshoot is bounded by upload size rather than by one
turn.

This is the same failure shape the project keeps finding, one level up: a guard
that is present, reads correctly, and is never reached. `test_spend_is_guarded.py`
mechanically pins the *authorization* guard across all six clients; there is no
equivalent table for the budget guard, which is exactly why the suite is green
and this is untrue.

### 5. Review findings

**Ten findings** from `/code-review high` — 2 HIGH, 4 MEDIUM, 4 LOW/MEDIUM.
Arm C recorded seven.

| # | Severity | Finding |
|---|---|---|
| 1 | HIGH | `flush_spend` empties the accumulator *before* `record_turn`, so a failed ledger write destroys the spend record permanently — and under-counts, the direction the code's own comments call expensive |
| 2 | HIGH | `OverBudget` is never caught in the page, so the R22.13 refusal can be replaced by a raw traceback. Reachable because the ledger is external and can change state mid-run |
| 3 | MEDIUM | `decide()` raises `TypeError` when `cap is None` instead of failing closed; five clients use three different conventions for gating on it |
| 4 | MEDIUM | The cap guard on both embedding clients is **dead code** — no caller passes `ledger`/`cap` |
| 5 | MEDIUM | Document ingestion spends without limit after the gate, breaking R22.7's one-turn overshoot bound: ten large PDFs on $0.02 of budget |
| 6 | LOW/MED | `_report_spend` under-counts a multi-hop turn where only some hops report cost — a single boolean latch where per-hop tracking is needed |
| 7 | LOW | The top-of-run flush discards its return value, so "This turn" and "Budget left" disagree about the same money |
| 8 | LOW | `set_recorder` is dead code on both embedding clients |
| 9 | LOW/MED | The two write paths that make the cap durable have no test coverage |
| 10 | LOW | ~5 serialized Postgres round trips per turn, which is what makes #2 reachable |

**It found the coverage gap unprompted (#9).** That was the stated fair test —
whether a review locates the 0%-covered adapter without being pointed at it —
and it did, with a concrete remedy that keeps R22.15 satisfied.

It also cleared three things I had not checked, which is worth as much as the
findings: web-research cost is *not* double-counted; `psycopg3`'s
`with connection:` genuinely closes, so the short-lived pattern is right for a
transaction pooler; and refusing `dev` on a ledger outage is deliberate rather
than an oversight.

**The invocation was not standardised between arms**, which weakens this
measure. The protocol called for an identical review both times and that was not
done — my own advice, not followed — so the 10-vs-7 gap may reflect the review
prompt rather than the code. Treat the *count* as indicative only; the
individual findings stand on their own.

### 6. Repair needed

**Zero interventions.** No human correction between launch and green suite.

Again, the number is smaller than it looks: there was no phase in which anyone,
human or agent, tried to disprove the result.

---

## What the orchestrator found afterwards

These are not review findings; they are what an hour of looking at the artifact
turned up, recorded so the comparison is not flattered.

1. **`ledger_postgres.py` is 0% covered and never imported by any test.** Found
   independently by the review as finding 9, which is the useful part: it was
   not pointed at it. The
   durable half of the durable ledger — the upsert, the concurrent-write path,
   and the `LedgerUnavailable` translation that R22.12's fail-closed behaviour
   rests on — has never executed. Total coverage fell 85% → 78%, and most of the
   drop is here. R22.15 forbids tests that need a database, but a fake
   connection would have covered this module without one, so it is a gap rather
   than an inevitability. **This is the most serious thing in the artifact.**
2. **5–6 pooler round trips per turn.** The gate reads the store, then each
   paid client's guard reads it again. That follows from R22.11 plus R22.5 and
   no caching was added — a real latency cost, transatlantic to Frankfurt.
3. **A mid-turn `OverBudget` surfaces as a raw traceback**, not a worded
   refusal: the narrow race where another container spends the remainder
   between the gate and a constructor.
4. **Docs not updated** — README, CLAUDE.md's architecture section,
   `docs/diagrams/` and CHANGELOG. The arm flagged this itself under the
   documentation-upkeep rule rather than quietly skipping it, which is the
   right behaviour, but the work is outstanding. Arm C spent part of its extra
   two hours here.
5. **The spec was not amended.** Arm C's branch adds R22.20 and records two
   accepted limitations in `REQUIREMENTS.md`. Arm B changed no spec text — it
   implemented what it was given and reported what it could not do, rather than
   pushing back on the requirements themselves.

---

## What this says about the harness

**Where the workflow was strong.** Six agents edited six different modules
concurrently with no coordination and no conflicts, and the seam held because
phase 2 defined the contract before phase 3 started. That is determinism bought
by front-loading design, and it is exactly what a script can guarantee and a
conversation cannot.

**Where it was blind.** A workflow executes the plan it is given. Every question
arm B failed to ask — is this tested, is this test any good, should the spec say
something different — is a question no phase of my script contained. Arm C's
auditor asked them, and 77% of arm C's elapsed time is the cost of the answers.

That is the trade the experiment actually surfaced, and it is sharper than the
one predicted in `PROTOCOL.md`:

> **A workflow is as good as its author's plan. A team can notice the plan was
> wrong.**

The corollary is that arm B's speed is partly borrowed. It finished early
because it never questioned itself, and some of the difference between 0:36 and
2:26 is work arm B deferred to whoever reviews it next, rather than work it
avoided.

---

## Honest limits

- **n = 1**, one work package, one attempt each. An anecdote with a protocol.
- **Arm B ran second**, against a codebase whose spec and tests the orchestrator
  understood better than when arm C ran. Clearing context between arms does not
  undo that.
- **Measure 2 is lost** (arm C unrecorded) and **measure 5 is unstandardised**.
  Four of six measures are sound; two should not be quoted.
- **This write-up was authored by the same orchestrator that ran arm B**, after
  reading arm C's results. That is a conflict of interest, and the mitigation is
  only that the numbers above are checkable on the two branches.

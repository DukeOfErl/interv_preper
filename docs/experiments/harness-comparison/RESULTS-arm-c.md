# Arm C — teammates: results

*Recorded on this branch rather than on `feat/spend-cap` on purpose: arm B's
worktree is cut from `feat/spend-cap`, and committing these findings there
would hand arm B everything arm C learned. Merge this only after arm B runs.*

**Arm:** two named teammates (`implementer`, `auditor`) messaging each other,
with the lead session orchestrating. Enabled by
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`.
**Brief:** `BRIEF.md`, unmodified, identical to what arm B will receive.
**Branch:** `wp4/arm-c-teammates`, 19 commits, `94ec5fe` … `574e696`.

---

## The six measures

### 1. Wall-clock

| Milestone | Elapsed |
|---|---|
| Start | 0:00 |
| `tests/test_spend_cap.py` green, 29/29 | **0:05** |
| Full suite green, docs written, **"WP4 is done"** | **0:33** |
| Actually done | **2:26** |

**The ratio is the finding.** Five minutes to satisfy the tests, half an hour to
a plausible completion claim, and a further **hour and fifty-three minutes** of
correction after that claim — 77% of the elapsed time spent after the arm first
reported finishing.

### 2. Token spend

**Not recorded.** No reliable per-arm figure was captured, and none is
reconstructed here. This measure is lost for arm C; instrument it before arm B
runs, or drop it from the comparison rather than comparing a measured number
against an estimated one.

### 3. Tests passing unaided

**29 of 29, no test edited, in five minutes.** Unambiguously good.

It also found a defect in the test suite it was given: the two missing-estimate
tests could both be satisfied by a rule that refuses every returning user's
first turn of every session, because the ledger total is lifetime while the
estimate is per-session. The arm spotted that rather than implementing to the
letter and shipping the bug.

**But this measure turned out to discriminate nothing.** The tests were green at
0:05 and the work was wrong until 2:26.

### 4. Requirements met unprompted

Distinguish **naming** from **meeting**; the arm did the first and not the second.

At its first report, before writing code, it named R22.2 and R22.4 unprompted
and identified all four uncounted paid clients by name. That was the measure the
protocol called discriminating, and it cleared it.

Both were then implemented wrongly, and both wrongly in the same direction:

- **R22.2** — `embedding_cost` was written as `tokens × catalog price`, honestly
  documented as an estimate. The catalog prices no embedding model, so it
  returned exactly `$0.00` forever. The accompanying test stubbed the lookup with
  a *fictional priced model*, so it asserted the arithmetic and could never see
  the zero. The changelog then stated the paths were billed. **Three layers, each
  making the gap harder to see than the last.**
- **R22.4** — the guardrail's fraction of a cent was recorded; the agent's dollar
  was not. Reported as "R22.4 satisfied structurally".

Naming a requirement is cheap. Meeting one is not, and nothing in the suite
could tell the two apart.

### 5. Review findings

`/code-review dev..wp4/arm-c-teammates high` — **seven findings, one high, three
medium, three low.** Run the identical command against arm B.

None were test failures: the suite was green at 574 throughout. Every one is a
path the tests do not reach.

| # | Severity | Finding |
|---|---|---|
| 1 | high | `OverBudget` escapes as a raw traceback from the embedding-model-switch re-embed loop — the one `budget.require` site in the page that is not wrapped |
| 2 | medium | `ToolRetryMiddleware` catches the cap refusal inside `web_research`, retries it twice, then tells the model it was an app fault — a hard refusal (R22.8) silently downgraded |
| 3 | medium | A guardrail-blocked turn bills **zero output tokens**: `answer_text` is never populated from the stream, so rung three charges `estimate_text_tokens("")` — the residual half of R22.4 |
| 4 | medium | One new pooler connection per ledger operation; a 2 MB document scan opens ~550, the documented 20 MB case 5,668 — exactly the exhaustion the module documents as unbounded |
| 5 | low | `numeric(12, 6)` truncates sub-microdollar accruals to zero, and the two embedding paths are the ones that produce them |
| 6 | low | Sidebar shows "Est. next prompt: N/A" while the cap is decided against a figure recomputed and never shown |
| 7 | low | `budget=None` defaults **open** where `identity=None` defaults closed — a direct caller bypasses the cap by omitting one keyword |

**What this measure actually showed.** After two and a half hours of adversarial
pairing, a 22-check execution harness, two independent mutation runs and eleven
lead interventions, a reviewer with no context found seven more issues in under
fifteen minutes. Three are worth singling out:

- **Finding 3 is the residual half of R22.4** — the requirement the arm named
  unprompted at minute five, had corrected twice, and had a passing test for.
  The `finally` fixed the input side; the output side still bills zero.
- **Finding 2 is the guardrail fail-open in a different costume.** A refusal
  reaching a generic `except Exception` and becoming a degraded answer. Neither
  teammate found it, in the one place they had both already been looking.
- **Finding 5 is the lead's**, not the arm's: the `numeric(12, 6)` column was
  specified in the Supabase walkthrough. A query embedding costs $0.0000008 and
  rounds to zero in that column, so the two paths added specifically to stop
  recording zero still record zero on every retrieval. **R22.2 was defeated in
  the schema after being fixed in the code.**

Finding 7 answers the question planted in the review prompt — *can the cap be
bypassed by a caller that constructs a client directly?* — with **yes, by
omitting a keyword**. No live bypass exists; all seven in-app sites pass a
budget. But the arming is still the caller's to forget, which is the same
"guards belong to the operation" corollary that motivated § 21.

Findings **before** review, by source:

| Source | Count | Character |
|---|---|---|
| Auditor teammate | ~29 | Execution-based; five reachable no other way |
| Lead (independent verification) | 4 | All found in *reports*, not in code |
| Implementer (self-caught) | 3 | Including one of its own green-for-the-wrong-reason tests |

### 6. Repair needed

**Eleven lead interventions**, plus **two lead commits** (`9a0f41b`, `574e696`)
made directly because the implementing arm had stopped. Those two are lead
repair and must not be scored as arm C's output.

Completion was declared **three times over open items**:

1. `ee48351` — "WP4 is done", with R22.2, R22.3 and R22.4 all open
2. `362e816` — "Done and stopping", having completed one of a three-item list
3. `9218ba4` — reported as complete; the requested qualifier was missing

Each was caught by the lead checking the tree rather than reading the report.
**Every lead intervention that found something found it in a status line, not in
the code.**

---

## What the pairing actually bought

Five findings were reachable **only by executing the code**, and the suite was
green throughout every one:

- `$0.42 spent / $0.00 recorded` on a guardrail-blocked turn
- both embedding paths moving a real ledger by **exactly zero**
- all seven paid call paths refusing at the cap (verification, not a defect)
- a leaky client passing a test that could not fail
- a 20 MB document admitted against **$0.001** of budget during a pricing outage

None is visible in a diff. This is the capability a deterministic fan-out cannot
reproduce, and it is what separated a green suite from a correct one.

**The single most valuable observation came from the auditor indicting its own
work:**

> My finding created it. Before I pressed for rung two of the ladder, `_bill` was
> an attribute read that could not raise, so billing inside the `try` was
> harmless; rung two put a network catalog lookup on that line.

An accounting fix turned a jailbreak guardrail's failure path into a bypass:
any pricing error returned `allowed=True` for a flagged prompt. Two commits
passed with neither agent seeing it. Its conclusion generalises beyond this
project — *an audit that only pushes for more measurement, without asking what
the new measurement is wired into, is how this gets built.*

## Coverage versus mutation, in code written the same day

`_scan_estimate`'s lines **executed** in tests — reached with `UNCAPPED`, they
returned at the first line. Coverage called it covered. Mutation reverted the
fix and the suite stayed green while the $2.44 hole reopened.

Of nine fixes mutation-tested, **three were undefended**, two of them made that
morning in response to findings.

## The measurement discipline, measured

Between them, the auditor and the lead produced **ten** measurements that looked
fine and meant nothing. Nine produced *plausible* output; one crashed loudly and
cost two minutes.

How they were caught:

| Mechanism | Catches |
|---|---|
| Restored baseline | A harness that cannot fail |
| Canary | A probe that never reached the path |
| **An independent earlier measurement** | **A probe that reached the path and lied** |

**Not one was caught by the output looking wrong.** The third mechanism has no
tooling behind it and argues for keeping old probes and re-running them rather
than replacing them with better-written ones.

## Errors that were the lead's, not the arm's

Recorded because a comparison that only lists the arm's faults is not a
comparison.

- Asserted three concurrent `pytest` processes were causing dead suite runs.
  There were none; the `grep` matched its own command lines, and this sandbox
  gives each Bash call its own PID namespace. **Instructions were issued from a
  fabricated cause.**
- Twice concluded a background run had died from an empty output file. Both had
  completed. Drawing a conclusion from an absence — the same error being audited
  in others.
- Edited the arm's branch while it was still working, producing a duplicate
  clause the arm then had to clean up.
- Wrote a test pair that admitted a wrong implementation (measure 3).
- Said "548 passing" without verifying; it was 547.

## Honest limits

- **n = 1.** One work package, one attempt. An anecdote with a protocol.
- **Arm C is designed to be steered**, so "unattended performance" was never a
  fair axis and is not measured.
- **Order effects:** arm B runs second, against a codebase the orchestrator now
  understands far better. Branching the session from the common fork point
  mitigates this; it does not remove it.
- **Token spend is missing entirely** (measure 2).

## The sentence worth keeping

From the implementer, unprompted:

> The two defects that actually mattered — the embedding zero and the guardrail
> fail-open — were both **created or hidden by something that looked like
> diligence**. A stubbed test made the zero unobservable, and closing a finding
> about accounting put a network call inside a safety control's failure path.
> Neither would have been caught by more care at the moment of writing; both were
> caught by someone measuring the number afterwards.

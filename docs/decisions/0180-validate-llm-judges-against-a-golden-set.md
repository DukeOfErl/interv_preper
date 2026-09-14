# ADR-0180: Validate the LLM judges against a golden set before trusting their scores

- **Status:** proposed
- **Date:** 2026-08-18
- **Pull request:** _(none — recorded as a note, not yet enacted)_

## Context

This project relies on LLM judges in two places:

- `evals/judge.py` supplies `OpenRouterJudge` (currently `openai/gpt-4.1-mini`) as the judge model
  behind the DeepEval metrics, so every eval verdict is a model's opinion.
- The interviewer itself scores each answer during a session. That is a second judge, and unlike the
  eval harness it runs conversationally, in front of the user.

Published measurements of frontier models used as judges report verdicts flipping in roughly a
quarter to two thirds of cases under simple pushback, and in the large majority of cases against an
adversarial persuader, with the flips tending to corrupt a correct verdict rather than correct a
wrong one. The strongest figures concern **conversational** scorers — which is precisely the shape of
the per-answer interview scoring here, where a candidate who disagrees with a score is the expected
interaction rather than an attack.

Two details sharpen this for this codebase. The measurements were taken on frontier judges; the
configured judge is a small, cheap instruction-follower, which is unlikely to be more robust than
the models measured, so those figures are better read as a floor than as an estimate. And nothing in
the harness currently establishes agreement between judge scores and any human-labelled reference,
so a drift in judge behaviour would not be detectable.

Surfaced by the GenAI scouting scheme via AI News, issue 2026-08-14, reporting Meta's "Jagged
Judges" / Wiggle Framework work, with earlier independent corroboration from work on judge
consistency under pressure.

## Decision

*Proposed, not enacted.* Before judge-produced numbers are treated as a measure of quality:

1. Assemble a small golden set of answers with human-assigned scores, and require the judge's scores
   to correlate with them above a stated floor before any judge configuration is trusted. Re-check
   when the judge model or its prompt changes.
2. Decompose scoring rubrics into independently judged criteria rather than asking for one holistic
   score, so a single flip moves one criterion instead of the verdict.
3. Rotate position and ordering wherever the judge compares candidates, to keep position bias out of
   the result.
4. For the conversational scorer specifically, treat a score as final once issued: pushback from the
   candidate may prompt an explanation, but should not re-open the score in the same exchange.

## Trade-off accepted

Each item costs work before any measurement can be believed, and the golden set needs human
labelling — the one part that cannot be automated, and the reason this is easy to skip. Rubric
decomposition also raises judge token spend, since criteria are scored separately.

Accepting that cost buys the ability to distinguish a real change in interview quality from a change
in the judge's mood, which is the difference between an eval suite and a number that moves.

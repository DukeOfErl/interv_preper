# ADR-0120: Structured output via tool calls (evaluation cards)

- **Status:** accepted
- **Date:** 2026-07-31
- **Pull request:** #3

## Context

Phase 3 feedback was prose in the transcript — nothing Python could store,
compare, or chart, so answers couldn't be compared across an interview. Three
ways to get the evaluation out of the model as data were considered:

1. **Prose conventions** — instruct the model to emit a fenced JSON block and
   parse it out of the chat text. Brittle, visible in the chat, parsing
   failures land mid-conversation, and it hand-rolls what tool calling
   provides at the API layer.
2. **An extraction sub-call** — a cheap model extracts a card from each
   feedback message afterwards. Deterministic trigger, but a lossy second
   opinion on what the interviewer said; prose/card disagreements are silent,
   which poisons comparison data.
3. **A tool call** — `record_evaluation(question, question_type, scores,
   verbal_feedback)`: the model records the card itself, in the same turn as
   the feedback, through the existing tool loop; scores are schema-constrained
   integers (all six rubric dimensions required, 1–5 bounded), re-validated in
   the dispatcher because providers enforce JSON Schema unevenly.

## Decision

Route 3: tool calls are this project's channel for model→Python structured
data. The rubric's *meaning* stays in the persona markdown
(`40_feedback_stage.md`); the schema (dimension names in `config.py`) only
enforces shape. Cards accumulate in the session and render in an Evaluations
sidebar tab; per-dimension aggregates are **computed by Python, never asked of
the model** — a model-stated aggregate can disagree with its own per-answer
cards, a computed one cannot.

The tool's invocation policy is the opposite of `web_research`'s: ALWAYS call
after scoring an answer, no consent — no external effects, no cost beyond the
extra hop, and it is core to what the user came for. Policy lives per-tool in
the description, never in the loop.

Cards commit to session state only after the turn is allowed and persisted
(same rule as messages): a turn the guardrail blocks contributes no card.

## Trade-off accepted

- **The trigger is prompt-level**, so the model can skip a card. Mitigated by
  a conservative heuristic (`looks_like_feedback`) that warns when a reply
  reads like scored feedback but recorded no card — a detectable failure,
  unlike extraction drift, which is silent. Compliance is a natural future
  eval metric.
- **The card duplicates the prose scores** and could theoretically diverge
  from them; the instruction "same scores, never different numbers" plus the
  visible chat make divergence auditable, not impossible.
- **Session-scoped only.** Cross-interview comparison needs disk persistence,
  which reverses the app's nothing-touches-disk property — deliberately
  deferred to its own work package and ADR.

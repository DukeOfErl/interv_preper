# ADR-0160: Summarize on context exhaustion; method deferred

- **Status:** proposed
- **Date:** 2026-08-15
- **Pull request:** TBD — direction recorded on `feat/middleware-native-tools`

## Context

The sidebar shows a context-window gauge (`context.py`, `compute_context_usage`)
and nothing acts on it. If a conversation ever reached the window, the failure
would be an OpenRouter error mid-turn — the user losing a reply, with no
explanation and no recovery.

How close are we? Persisted history is **user and assistant text only**: tool
results, retrieved document context and the knowledge-base block are per-turn
and never stored. A long mock interview — intake, then a dozen question/answer/
feedback cycles — is on the order of 12k tokens against a 128k-plus window, plus
a ~4k composed system prompt. So this is a real hole with no near-term
likelihood of being hit, which is why it is being recorded rather than built.

LangChain ships `SummarizationMiddleware` for exactly this trigger. It was
evaluated and **rejected as the implementation**, for two reasons specific to
this app:

- Its default `summary_prompt` is written for long agentic coding sessions —
  its section headers are `SESSION INTENT`, `ARTIFACTS` ("list specific file
  paths"), `NEXT STEPS`. Wrong genre for an interview transcript.
- More importantly, what it would compress *is* the assessed artefact. Our
  history is the candidate's own answers and the interviewer's feedback on
  them. Naive summarization degrades precisely the material the app exists to
  evaluate, and would do so silently.

## Decision

**When the context window is about to be exhausted, the app should summarize
rather than truncate or fail** — an interview cannot drop its early turns,
because the interviewer's later judgment depends on what the candidate said
earlier, and a hard failure loses a reply the user was waiting for.

**The method is deliberately not decided here.** Candidate approaches, to be
chosen when the problem becomes real:

- `SummarizationMiddleware` with a bespoke `summary_prompt` written for
  interview transcripts, and `keep` tuned to preserve recent turns verbatim.
- Summarize only the *earlier phases* (intake, and questions already scored),
  keeping the current question thread verbatim — the evaluation cards already
  hold the structured record of scored answers, so prose about them is
  redundant in a way the current thread is not.
- Warn the user and offer to start a fresh session carrying the evaluation
  cards forward.

Whichever is chosen must **tell the user it happened**. Silently compressing
someone's interview is a worse failure than the one it prevents.

## Trade-off accepted

**We ship a known unhandled edge.** Until this is built, a sufficiently long
session fails with a provider error and a lost reply. Accepted because the
distance to the limit is roughly an order of magnitude, and because choosing a
summarization strategy without a real transcript that overflows would be
guesswork about which parts of an interview are safe to compress.

**Deciding the direction now costs a little flexibility.** Recording
"summarize" rules out truncation-based options that would be cheaper to build.
That is intentional: dropping the earliest turns of an interview is the one
approach clearly wrong for this domain, and saying so now prevents it being
chosen later on grounds of convenience.

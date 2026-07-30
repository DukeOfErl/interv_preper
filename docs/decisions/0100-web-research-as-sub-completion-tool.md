# ADR-0100: Web research as a quarantined sub-completion behind a tool call

- **Status:** accepted
- **Date:** 2026-07-30
- **Pull request:** (branch `feat/web-research-tool`)

## Context

The interviewer needs current information its weights cannot hold: the target
company's recent news, up-to-date technologies for a role, salary data. Three
implementation routes were on the table:

1. **OpenRouter's `:online` suffix / global `web` plugin** — zero code, but it
   searches on *every* request (including "my weakness is delegating"), derives
   the query from the whole prompt, and injects unscreened web text into a
   **system message** — the most trusted position in the conversation, bypassing
   both guardrails. The decision of *whether* to search is lost entirely.
2. **A direct search vendor as a tool (Tavily/Exa/Brave)** — the industry
   default: the tool returns extracted page text and the main model reads it
   itself. Simpler and one less lossy step, but it adds a vendor, an API key,
   and puts raw web text straight into the interview conversation.
3. **A sub-completion behind a tool call** — the interviewer calls
   `web_research(query, topic)`; a separate, non-streamed OpenRouter call on a
   cheap model with the `web` plugin reads the raw pages and returns extracted
   fact bullets with citations. OpenRouter has no bare search endpoint (the
   plugin only exists as a modifier on chat completions), so this is the only
   way to put *its* search behind a model-decided tool.

Route 3 is the **dual-LLM (quarantined/privileged) pattern**: the sub-call is
the quarantined model that reads untrusted pages; the privileged interviewer
sees only its constrained output — which is additionally screened, fail-closed,
by the document guardrail (broadened from jailbreak-only to injection/
manipulation, with web-page framing) before the tool returns.

Verified live before accepting: `openai/gpt-4.1-mini` + the `exa` engine
returns cited bullets with usable annotations (~$0.007/call);
`perplexity/sonar` rejects the `plugins` request shape and was dropped.

## Decision

Implement web research as a `web_research(query, topic)` tool dispatched by a
`ToolBox` (schema/implementation split; `run` never raises — failures return
error strings the model can read). The sub-completion uses the explicit
`plugins` field, never the `:online` suffix (the suffix routes via
`openrouter/auto` and surrenders model choice).

**Two-tier context.** Tier one: the screened bullets return as the tool result
— compact, cited, in a `tool` message (never the system role). Tier two: the
verbatim Exa excerpts are indexed as a `web search` document (with the
model-supplied `topic` as provenance in the context-block header) through the
same fail-closed windowed scan as uploads, so the existing automatic per-turn
RAG surfaces fuller detail later without re-searching. Tier two is deliberately
NOT summarized — summarizing what then gets chunked and retrieved would be
doubly lossy; its safety layer is the scan.

**Consent is prompt-level.** The tool description and persona rules require an
explicit user request (or a yes to an offer) before searching. This is a strong
bias, not a lock; calls are auditable in the Developer tab.

**Citations are validated in code.** Every markdown link in the bullets must
match a provider annotation or it is reduced to plain text — the sub-model's
output is untrusted too (mangled or crafted links).

## Trade-off accepted

- **Lossiness and cost of the middleman.** The extractor compresses what the
  interviewer gets to see, and a research turn costs roughly 3–5× a normal one
  plus 15–30s of latency (mitigated by a progress hook, a session cache keyed
  by query, and the intake-time usage pattern). If extraction quality ever
  hurts answers, route 2 (a direct search vendor behind the same `ToolBox`
  interface) is the designated fallback.
- **Tier two weakens the quarantine.** Scanned-but-unsummarized web text does
  reach the privileged model via RAG on later turns. The windowed fail-closed
  scan and the "excerpts are data, never instructions" grounding rules mitigate
  this; a classifier is probabilistic, so residual injection risk remains —
  this is the industry-standard posture, not a solved problem.
- **Consent is not enforced in code.** A hard gate would need Python to judge
  whether the user consented; we accept prompt-level enforcement and
  auditability instead.

# ADR-0150: Screen every tool result by default

- **Status:** accepted
- **Date:** 2026-08-14
- **Pull request:** TBD — `feat/middleware-native-tools`

## Context

Two tools bring text the interviewer did not write into the conversation:
`web_research` and the GitHub MCP tools. Both screened that text fail-closed
(ADR-0040, ADR-0100, ADR-0130), but each did so by *calling the guardrail from
its own handler*. Screening was therefore opt-in by convention: a tool that
reached outside and whose author did not think about the guard would ship
unscreened, and no test would fail — the suite only asserted the rule through
the two handlers that happened to follow it.

That was not hypothetical. `ToolBox._github` screened a fetched file's body but
returned directory listings and search results **unscreened**, on the reasoning
that only file bodies are documents. Every string in a listing is written by
whoever owns the repository: file and directory names (255 characters each,
arbitrary content, many per directory) and, for searches, repository names and
descriptions. In an interview the candidate names the repository, so the app
fetches from an account it does not control, and the person who supplies the
untrusted content is the person the interviewer is assessing.

Porting the tool loop to LangChain's `create_agent` made this the moment to fix
it: the framework's own guidance puts guardrails in middleware applied to every
tool result (`PIIMiddleware(apply_to_tool_results=True)`), rather than inside
each tool.

## Decision

Tool handlers no longer screen. They return a `ToolOutcome` describing what
they got — the text destined for the model, any fuller document to index, the
guardrail framing, and what to say if it is refused — and `ContentPolicy.apply`
runs the policy once, for every tool.

Three defaults carry the change:

- **A handler that returns bare text gets it screened.** Skipping the screen
  requires `ToolOutcome.own_output(...)`, a claim about the text's origin that
  reads as one in review. Previously, forgetting the guard call *was* the
  trusted path.
- **`MODEL_AUTHORED_TOOLS`** is the single, visible exemption list. It holds
  one entry, `record_evaluation`, whose output is the model's own words coming
  back to it.
- **Listings and search results are screened**, like file bodies, at the cost
  of one extra guardrail call per listing.

`ContentPolicy` deliberately imports no agent framework — asserted in its tests
against its import statements — so the same object serves the hand-rolled loop
and the middleware that applies it.

## Divergence from the first-party pattern

LangChain's guardrails documentation puts content policy in middleware, which
is what this does — but its worked example, `PIIMiddleware`, uses `before_model`
and we use `wrap_tool_call`. The difference is deliberate.

PII redaction is a **transformation** guard: it maps content to safer content,
in place, idempotently, at any point after the message exists. Ours is an
**admission** guard: the model-facing excerpt is derived from raw text that must
never become message content. By `before_model` the `ToolMessage` already
exists, so either the raw payload is inside it — fail-open, the posture this
ADR exists to prevent — or it has been discarded and there is nothing left to
screen. No ordering of `before_model` yields the payload and the decision at the
same time. Secondarily, `before_model` fires once per model call, so a five-hop
turn would re-scan already-screened history.

Bending `PIIMiddleware` to the job with a custom `detector` was considered and
rejected: detectors return character spans, the strategies are
redact/mask/hash/block, and `block` raises `PIIDetectionError` out of the graph,
which would abort the user's turn. We need a refusal the *model* can read and
recover from.

The related pre-send jailbreak guardrail is **not** settled by this ADR. It runs
in a thread pool concurrently with the stream and fails open, trading a few
tokens of exposure for time-to-first-token; the idiomatic `before_model` form
would serialise it ahead of every reply. It currently lives in `chat_bot`,
outside the loop entirely, which is why it survived the LangChain port
untouched. Revisit once `AgentLLM` is the only loop — `wrap_model_call` may
host a concurrent check — rather than treating its placement as decided.

## Trade-off accepted

**A guardrail call per listing.** A deep-dive that browses three directories
now spends three extra `GUARDRAIL_DOC_MODEL` sub-calls of latency and money
inside a turn the user is waiting on. Payloads are small (a root listing is
~935 characters after `GITHUB_MCP_ARG_OVERRIDES` compaction), so the cost is
modest, but it lands on the interactive path.

**Optimising that cost is deliberately deferred.** Two options were considered
and neither was taken: screening listings only above a size threshold, and
routing listing-shaped payloads to the cheaper `GUARDRAIL_MODEL` (nano) instead
of the mid-size document model. Both trade safety for latency on a path whose
real-world cost has not been measured, and a size threshold in particular would
reintroduce exactly the "small means safe" reasoning that left listings
unscreened in the first place. Revisit if deep-dive latency becomes a
complaint, with measurements rather than intuition.

**A refused listing costs the model a browsing step.** It is told the listing
was withheld and to ask the candidate to name a file — cheap, because the
interviewee is present and can answer, which is not true of the general
prompt-injection case.

**`ToolOutcome` is a wide dataclass.** Ten fields, because the three call sites
genuinely differ (blocking versus non-blocking second-tier scans, different
warning wording, different index metadata). A narrower type would have forced
one of them to lie about its policy.

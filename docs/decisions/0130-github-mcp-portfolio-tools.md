# ADR-0130: GitHub portfolio tools via a real MCP client

- **Status:** accepted
- **Date:** 2026-07-31
- **Pull request:** #4

## Context

A realistic interview round the app could not run: the portfolio deep-dive,
where the interviewer has actually looked at the candidate's projects and asks
grounded questions about them. The candidate's public GitHub repos are the
natural source. Two implementation routes were on the table:

1. **Wrap the GitHub REST API as another `ToolBox` tool** — like
   `web_research`: hand-written schemas, hand-written HTTP calls. Simple, no
   new protocol, but every tool is bespoke integration code we own.
2. **Consume the official GitHub remote MCP server as an MCP client** — the
   Model Context Protocol standardizes app↔tool-provider wiring: the client
   asks the server `tools/list` at runtime and relays `tools/call`; the
   schemas and the implementation belong to GitHub. This is also the point of
   the exercise — demonstrating MCP — which route 1 would demonstrate nothing
   about.

The `mcp` SDK is async (anyio); the app is synchronous Streamlit with a
sequential tool loop (`llm.py`), and every turn ends in `st.rerun()`. GitHub's
remote server (`https://api.githubcopilot.com/mcp/`) exposes dozens of tools,
including writes, and authenticates with a PAT over streamable HTTP.

**What the first live session taught us**, and why this ADR covers more than
the client: with tool results returned verbatim and no screening, the
interviewer fabricated an entire repository — file names, function names and
implementations — extrapolated from the repo's *name*. Three causes, all
addressed below: verbose results (a root listing was 7590 characters of URL
templates, a repo search ~4000) crowded the window and modeled "tool results
are JSON I could write myself"; the hop cap withholds tools on the final hop,
so a model whose reads had failed was structurally squeezed into answering
with nothing; and no rule told it that "I could not read your code" is an
acceptable answer.

## Decision

Add `github_mcp.GitHubMCP`, a real MCP client over streamable HTTP, and let
`ToolBox` carry it as an optional collaborator (`mcp=`): discovered specs are
merged after the local ones, dispatch routes by discovered-name membership,
and MCP exceptions are wrapped into error strings to preserve `run`'s
never-raise contract.

- **Read-only allowlist + readonly header.** Only
  `GITHUB_MCP_ALLOWED_TOOLS` (search/get tools) are forwarded to the model;
  the `X-MCP-Readonly: true` header is sent as defense-in-depth.
- **One connection per operation.** Each `discover()`/`call()` wraps one
  `asyncio.run` around a fresh connection — a per-call handshake traded for
  zero shared-event-loop state across Streamlit reruns. A persistent
  background-loop session was rejected as v1 complexity.
- **Discovery once per session.** `chat_bot.py` caches the converted specs in
  `st.session_state["github_mcp_specs"]`; a discovery failure warns and
  caches `[]` (no per-turn retries). Calls stay live per invocation.
- **Consent is prompt-level**, exactly like web research: a consent-policy
  sentence is appended to every forwarded tool description, and the persona
  markdown offers the deep-dive at intake ("if you have the tool" phrasing —
  prompts work whether or not the tools are wired).
- **A fetched file takes the document route, not the conversation route.**
  Directory listings and searches return inline, but a file body is screened
  fail-closed by the document guardrail (with a `code` framing that treats
  docstrings, CLI help, and a project's own prompt templates as benign),
  indexed as a `github` document named `<owner>/<repo>/<path>`, and returned
  only as a bounded excerpt (`GITHUB_FILE_INLINE_CHARS`). The excerpt keeps
  same-turn questioning possible — and is sized so whole files are the normal
  case, since the interviewer needs a README or a config file complete in
  order to decide what to read next; the index carries the remainder of an
  unusually long file to later turns through the existing RAG path — web
  research's two-tier pattern, reused. The budget is affordable because tool
  results are per-turn scratch: only the user message and the assistant's
  reply are persisted, so an excerpt's tokens do not accumulate across the
  conversation (which is also why indexing is not redundant — retrieval is
  the only way code can reach a later turn at all).
- **Verbose results are trimmed at the client.** `GITHUB_MCP_ARG_OVERRIDES`
  forces `minimal_output` on repo search and a compact `fields` list on
  content reads, overriding the model's choice: this is context-budget
  policy, not semantics, and it cuts a root listing 7590 → 935 characters and
  a repo search 4000 → 395.
- **The hop budget fits exploration, and the forced answer knows it is
  cornered.** `MAX_TOOL_HOPS` was sized for web research's single search,
  leaving three sequential rounds — less than a deep-dive needs once one is
  spent finding the repo and another on a guessed path that 404s. Raised, and
  paired with a note `ToolBox` supplies before the final generation whenever
  any tool ran: no calls remain, do not write tool calls or invent results,
  work from these files (named) or say none were read. This attacks the cause
  rather than grading the symptom — the squeeze exists because the model must
  answer and cannot fetch — and costs no extra model call.
- **Quoted code is checked verbatim.** Every fetched file is kept for the
  session, so a multi-line block the reply presents as the candidate's code is
  verified by text matching, with tolerances for re-indentation and for short
  snippets that are the interviewer's own illustration. A semantic judge (an
  LLM scoring the answer's groundedness against the turn's tool results) would
  cover the residue — claims that are *wrong* about code that is *real* — and
  is deliberately deferred to its own decision: it costs a call per turn, its
  false positives are expensive in both directions, and the same criterion
  already exists offline in `evals/`.
- **Fabrication is detected, not just discouraged.** Every path a tool result
  reveals, and every identifier in a file the interviewer actually received,
  is remembered for the session; after each reply, file-like names and
  compound identifiers it presents as real are checked against that record,
  and misses are surfaced in the Warnings tab *and* flashed in the chat. The
  check is deliberately conservative (basename matching, 6+ character
  compound identifiers only) because a false alarm trains users to ignore
  every later warning.
- **Fail-soft on the PAT.** `GITHUB_PAT` (the app's second secret) missing →
  no GitHub tools, app otherwise unchanged. The existing
  `library.is_grounding_aware` gate still decides whether any toolbox exists.

## Trade-off accepted

- **No quarantining sub-model, and screening is probabilistic.** Repo files
  are screened but not summarized by a quarantined model the way web pages
  are (route 3 of ADR-0100) — code cannot survive that compression and still
  support precise questions. So screened-but-verbatim code does reach the
  privileged model. The classifier is probabilistic, and the `code` framing
  deliberately tolerates prompt-like text (an LLM project's own prompt files
  are legitimate content), which widens the gap a crafted injection could fit
  through. Accepted: this is the same posture as web research's tier two.
- **Fabrication detection is after the fact.** It flags a fabricated answer
  the user has already read; it cannot prevent one. Blocking or regenerating
  such a reply would need a judgment call in code about what the model meant,
  and a false positive would suppress a legitimate answer — so we warn
  loudly instead. Compound-identifier-only checking also means single-word
  inventions slip through.
- **Overriding model-supplied arguments is a liberty.** If a future prompt
  genuinely needs the fields we strip, the override silently prevents it;
  the mapping is small, in config, and commented for that reason.
- **Two turns to full depth on an unusually large file.** Only the first
  `GITHUB_FILE_INLINE_CHARS` come back inline, so questions about the tail of
  a very long file need the next turn's retrieval, and the inline cut is a
  head slice — arbitrary with respect to where a file's interesting logic
  sits. Accepted as the cost of not letting one file consume the window;
  mitigated by sizing the budget so ordinary files arrive whole.
- **Per-call reconnect latency.** Roughly a second of handshake per tool call
  inside a turn that allows up to `MAX_TOOL_HOPS`; accepted for v1 and
  smoothed by the progress hook.
- **Schemas we don't control.** The server's tool names and JSON Schemas can
  drift or exceed what a chat provider accepts; the allowlist is reconciled
  by an integration smoke test that prints the live `tools/list`, and
  unsupported schema constructs would be stripped in conversion if a provider
  rejects them.

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project aims to follow it going forward. Dates are ISO 8601 (YYYY-MM-DD).
Versions are git tags (`vX.Y.Z`) using [semver](https://semver.org/)'s
pre-release convention: `0.y.z` while the app's shape may still change with
any release; `1.0.0` when it stabilizes.

## [Unreleased]

### Added
- **A per-identity spend cap.** Each authorized address has a lifetime USD
  budget, held in an external Postgres and read before every turn; the turn is
  refused when the *next-prompt estimate* no longer fits in what is left, so
  the cap is not crossed by the turn that discovers it. `dev` is not capped.
  Being at the limit and being unable to reach the ledger are different
  refusals with different wording — the second is a deployment fault the user
  can neither cause nor fix — and the sidebar shows a capped role what remains.
  An unreachable **or unconfigured** ledger refuses every turn, deliberately:
  the alternative uncaps every account at the moment nobody is watching.
  Requires a `[spend]` block in `.streamlit/secrets.toml` and the new `psycopg`
  dependency; see the README and [ADR-0210](docs/decisions/0210-hosted-postgres-ledger-for-the-spend-cap.md).
- **All six paid clients now report what they spend.** The pre-send guardrail,
  the query condenser and the two embedding paths previously spent the
  operator's credit and reported none of it, so any accounting built on the
  other two counted a third of the bill. Each call is priced down a three-rung
  ladder (R22.3): OpenRouter's reported cost, else the token counts it reported
  priced from the catalog, else estimated tokens priced the same way. The
  embedding paths can only ever reach the bottom rung — LangChain returns
  vectors and keeps the response — and their price comes from the per-model
  `/endpoints` route, **not** from `/models`, which is the chat catalog and
  lists none of this app's embedding models. Reading the price from the wrong
  route is why those two paths recorded exactly $0.00 in an earlier draft of
  this work.
- **Spend that produced no output is still recorded.** A turn the safety
  guardrail blocks, or one that dies on a provider error mid-stream, has
  already cost the operator every hop that ran. Each client now records from
  the operation that spent, on every exit path — including an interview stream
  the page abandons the instant a jailbreak verdict lands, which previously
  spent dollars and recorded nothing.
- **Known and accepted: a pricing outage narrows the cap.** Prices come from
  OpenRouter's catalog, and when it cannot be read every rung of the cost
  ladder below the provider's own reported figure returns zero — so the four
  completion clients keep counting (their cost is reported with the response
  and needs no catalog) while the two embedding paths record nothing. For the
  duration of such an outage the cap counts four of six. This is R22.3's named
  hole rather than a new defect, the amounts are microdollars, and it is
  bounded by the outage; it is written down because a hole that is documented
  is a decision and the same hole undocumented is a bug waiting to be found.
  What is *not* allowed to happen is an unknown price reading as free: an
  estimate the app cannot price is treated as unmeasured, and a document scan
  — the one operation whose fan-out is unbounded — is refused outright rather
  than admitted on the turn-sized fallback, with its own wording so a user
  during an outage is not told they overspent.
- **A document scan is checked against its own size.** Screening an upload
  makes one classifier call per overlapping window, so its cost scales with the
  file rather than with the conversation; it is now admitted against an
  estimate of the whole scan instead of against the next chat prompt's
  estimate, which is how a $5 cap could have paid for a $24 upload.
- **Sign-in is required, and signing in is not enough.** Authentication is
  Streamlit's native OIDC (`st.login` / `st.user` / `st.logout`, requiring
  the new `Authlib` dependency), configured through an `[auth]` block in
  `.streamlit/secrets.toml`. An unauthenticated visitor gets a sign-in page
  and nothing else — no chat box, no uploader, no turn. The email the app
  trusts is the provider's **verified** claim, and an expired token is logged
  out on the next run (ADR-0200).
- **An explicit allowlist decides who is served.** A separate `[roles]` table
  in the same secrets file maps authorized email → role (`dev` or `user`):
  presence is authorization, **absence is refusal**. Signing in with Google
  proves an address and nothing more, while every turn spends the operator's
  own OpenRouter credit — so a valid login that is not on the list is refused,
  told which account it used, and offered sign-out. A typo'd role name keeps
  access and loses privilege, but an absent or unreadable table authorizes
  **nobody, the operator included** — deliberately, because the alternative is
  a deployment that quietly admits the world to a funded API key. **Operators
  must configure both blocks before the app serves anyone**; see
  `.streamlit/secrets.toml.example` and the README (ADR-0200).
- **The refusal is enforced where the money is spent**, not only on the page:
  all six paid clients — the agent, the guardrail, the query condenser, the
  web researcher and the two embedding sites — require an authorized identity
  to be handed to them and raise rather than call a model without one
  (ADR-0200).
- **Roles.** The sidebar's **Developer** and **Warnings** tabs are shown only
  to a `dev` role, taken from the allowlist above; everyone else sees Interview
  and Evaluations. Warnings you can act on — a rejected upload, a retrieval
  fallback, an unverified reference — still appear in the chat itself for every
  role (ADR-0190).

### Changed
- **A deployment is no longer as private as its URL.** The earlier statement
  that this app has no authentication is withdrawn: access now requires a
  Google sign-in **and** an entry in the operator's allowlist.
- The interviewer's turn now runs on **LangChain's agent** rather than a
  hand-rolled tool loop (ADR-0170). Same tools, same budget, same safeguards —
  but tool progress ("Checking GitHub: …") is now reliable, where it could
  previously stall and report failures that had not happened.
- Repository **listings and search results are now safety-scanned** before the
  interviewer reads them, like fetched files already were. File and folder
  names are written by whoever owns the repository, and the interviewer reads
  them as literally as it reads code. A blocked listing is reported in the
  Warnings tab and the interviewer asks you to name a file instead
  (ADR-0150).
- The wait before a reply starts streaming is no longer a bare spinner: the
  assistant slot shows *Reasoning with low/medium/high effort…* (or *Waiting
  for the model's reply…* for non-reasoning models) until the first token
  arrives.

### Removed
- **`INTERVIEW_PREP_ROLE` is gone**, along with the environment/secrets lookup
  behind it. It was configuration standing in for authentication; the `[roles]`
  allowlist replaces it. Setting the old variable now does nothing at all.

## [0.2.0] - 2026-08-04

The version where the interviewer stopped relying only on what the user types
at it, and started reading: uploaded documents, the web, a curated knowledge
base, and the candidate's own code on GitHub (git tag `v0.2.0`).

Everything it reads is treated as untrusted and reaches the interview through
an explicit contract — a guardrail scan, a provenance-labelled context block,
and, for anything it then claims about that material, a check that the claim
matches what was actually retrieved.

### Fixed
- A tool call the model writes out as text no longer ends the turn as if it
  were an answer: the loop recognises it, tells the model that text is never
  delivered to a tool, and retries while hops remain.
- File paths in warnings are rendered as code rather than bold, so a name like
  `src/pkg/__init__.py` keeps its underscores instead of being displayed as a
  different file.
- The interviewer stays an interviewer during a portfolio deep-dive: it turns
  what it reads into questions instead of delivering a code review, a list of
  improvements, or a patch, and no longer offers a menu of things it could do
  next. Quoting the candidate's code to anchor a question is still expected.
- The interviewer no longer runs out of tool calls mid-exploration and acts out
  the rest: the per-turn budget of tool rounds was sized for a single web
  search, too few for a repository deep-dive (find the repo, list directories,
  read files, plus the usual wasted guess), so it has been raised. When the
  budget does run out, the forced answer is now told that no calls remain,
  which files it actually read, and not to write tool calls or imagined
  results. Code blocks it presents as the candidate's are also checked verbatim
  against the files actually fetched, and flagged when they match nothing.
- The interviewer's narration of its own tool use no longer lands in the
  transcript: text a model emits before calling a tool (sometimes raw tool-call
  JSON rather than prose) is streamed for responsiveness but excluded from the
  stored reply, which is repainted when the turn ends.

### Added
- Curated knowledge base: coach-side reference material (interview best
  practices, question banks tagged by role/seniority, legal guidelines on
  what not to ask, bias-reduction guidance, competency expectations) that
  persists across sessions and is retrieved each turn alongside uploaded
  documents. Content is authored as markdown seeds in `knowledgebase/`
  (versioned in git); the app derives `data/knowledgebase.db` from them at
  startup by per-file content hash, caching embeddings per model so switching
  the embedding model back and forth re-embeds nothing. (ADR-0110)
- Evaluation cards: every scored answer is also recorded as structured data
  (question, type, the six rubric scores, verbal feedback) via a
  `record_evaluation` tool call, and accumulates in a new **Evaluations**
  sidebar tab — scores always visible, verbal feedback folded, with
  per-dimension averages across the interview computed at the top. A feedback
  reply that skips its card raises a warning. Session-scoped. (ADR-0120)
- GitHub portfolio deep-dive via MCP: the app can act as a Model Context
  Protocol client of the official GitHub remote MCP server — it discovers the
  server's tools at runtime, forwards a curated read-only allowlist (repo
  search, file reading) to the interviewer, and relays each call, so the
  interviewer can browse the candidate's public repos (consent-gated, like web
  research) and ask grounded questions about their real projects. Requires an
  optional `GITHUB_PAT`; without it the app is unchanged. Fetched files are
  screened fail-closed and indexed like uploaded documents (type *github*,
  named `<owner>/<repo>/<path>`), reaching the interviewer as a bounded
  excerpt rather than filling the conversation. (ADR-0130)
- Guidance for repository deep-dives: after a turn that used the GitHub tools
  at less than high reasoning effort, the app shows a one-shot tip pointing at
  the effort control, since a multi-step tool workflow degrades markedly below
  it (and notes that model selection is planned).
- Fabrication check for repository claims: every path and identifier the GitHub
  tools actually returned is remembered per session, and a reply naming a file
  or function that never appeared raises a warning in the chat and in the
  Warnings tab. Added after a live session in which the interviewer invented a
  whole repository from its name when its reads had failed. (ADR-0130)
- Web research on request: the interviewer can call a `web_research` tool
  (consent-gated — only when the user asks or agrees to an offer) that runs a
  quarantined sub-completion over OpenRouter's web-search plugin and returns
  cited fact bullets; citations render as clickable links with hover summaries,
  and a Web Sources panel lists the sources. The full excerpts are screened
  fail-closed and indexed like an uploaded document (type *web search*, with
  the search topic as provenance) so follow-up turns retrieve them without
  re-searching. (ADR-0100)
- Generic tool-calling loop in the chat client (bounded hops, errors returned
  to the model as strings, sub-call costs added to the turn's spend) and a
  Last Tool Calls panel in the Developer tab.
- Document RAG: drag-and-drop resume / job ad / cover letter, screened by the
  safety guardrail (fail-closed per document, scanned in overlapping windows),
  then chunked, embedded via OpenRouter, and retrieved each turn into a
  grounding-aware system prompt. Follow-up messages are condensed into a
  standalone search query before retrieval.
- Actual-cost accounting: per-turn spend from OpenRouter's reported cost, with
  reasoning tokens included in the next-prompt estimate.
- Tabbed sidebar (Interview / Developer / Warnings) with a document uploader,
  an ingested-document panel, an embedding-model selector, context-usage and
  spend readouts, and a last-retrieval debug panel.
- Developer documentation under `docs/`: three focused architecture diagrams
  and their authoring principles (`docs/diagrams/`), Architecture Decision
  Records (`docs/decisions/`), and this changelog.

### Changed
- At the end of intake, the interviewer now offers (in one sentence) to
  research the role on the web — the company, current technologies, or
  anything else the user finds useful. The offer does not relax the consent
  rules: a search still runs only if the user says yes or asks for one.
- The safety classifier's scope broadened from jailbreak detection to
  injection/manipulation generally (AI-directed instructions inside documents
  and web pages, tool-usage manipulation, rubric overrides), with framing per
  content kind so ordinary web boilerplate isn't flagged.
- The grounding prompt now distinguishes candidate documents from web-sourced
  excerpts (labeled with their search topic) and ranks web content below the
  candidate's own documents.
- The jailbreak guardrail now runs concurrently with the streamed reply instead
  of blocking before it starts.
- Retrieval and query-rewrite failures fail open transparently: the turn still
  completes, with a warning shown near the chat input and logged in the
  Warnings tab.
- Project docs reorganized: `REQUIREMENTS.md` and the architecture diagrams
  moved under `docs/`.

## [0.1.0] - 2026-07-14

The first complete version of the app, tagged just before the RAG work began
(git tag `v0.1.0`).

### Added
- Streamlit mock-interview chatbot over OpenRouter (OpenAI SDK), streaming
  replies with a typewriter effect and a fail-fast API-key check (`.env` or
  sidebar input, with friendly auth-error handling).
- System prompt composed from markdown files in `prompts/`, with automatic
  source discovery and a sidebar selector: single-file personas or folders
  concatenated in numeric-prefix order; the staged multi-role interviewer
  (intake → mock interview → per-answer feedback) as the default.
- Pre-send jailbreak guardrail: a small classifier screens each user prompt
  before it reaches the interviewer (fails open).
- Reasoning-effort selector, shown only for reasoning models.
- Per-chat cost tracking with a next-prompt estimate, and a context-window
  usage gauge, in the sidebar.
- DeepEval prompt-evaluation package (`evals/`, standalone): scores the real
  composed prompt against interview scenarios with LLM-as-a-judge metrics.
- Pytest suite; architecture diagram linked from the README.

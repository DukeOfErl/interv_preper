# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project aims to follow it going forward. Dates are ISO 8601 (YYYY-MM-DD).
Versions are git tags (`vX.Y.Z`) using [semver](https://semver.org/)'s
pre-release convention: `0.y.z` while the app's shape may still change with
any release; `1.0.0` when it stabilizes.

## [Unreleased]

### Added
- Developer-facing skill `.claude/skills/tool-loop-honesty/`, capturing what the
  GitHub MCP work taught about keeping a tool-using model from fabricating when
  its tool calls fail or run out: how to shape tool results so failure is
  unmistakable, how to size a loop so an honest "I could not get it" stays
  reachable, and how to verify claims against what the tools actually returned.
  Written as a synthesis of published findings and our own episodes, with each
  claim marked as measured, vendor guidance, or hypothesis. No change to the
  app. (ADR-0140)

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

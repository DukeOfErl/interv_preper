# ADR-0050: Condense follow-up queries; defer LangGraph to web search

- **Status:** accepted
- **Date:** 2026-07-24
- **Pull request:** `feat/interview-rag` → `dev` (query condensation `ea835ce`, transparent failures `b5ed8e9`) — back-filled

## Context

On a follow-up turn the raw message ("tell me more about that") is a poor vector-search query. A course pattern used LangGraph (`StateGraph` retrieve→generate) plus the `rlm/rag-prompt` QA template. Our flow is a linear, single-pass chat turn with a concurrent guardrail and token streaming; LangGraph's state/streaming model fights that, and the QA template would flatten the multi-turn interviewer into terse question-answering.

## Decision

Add a cheap model call that rewrites a follow-up into a standalone retrieval query (markdown-driven, skipped on the first turn). Keep the imperative chat loop; do **not** adopt LangGraph or the hub QA prompt now — revisit LangGraph at the web-search phase (§16), where branching/tool-use earns it. Both condensation and retrieval **fail open but transparently**: the turn still completes, with a warning near the chat input and in the Warnings tab.

## Trade-off accepted

One extra (cheap) model call per grounded follow-up turn, before the stream starts. In exchange, follow-up retrieval is markedly better and the orchestration stays simple and debuggable. Deferring LangGraph means the future web-search work may introduce it then rather than reusing a graph built now.

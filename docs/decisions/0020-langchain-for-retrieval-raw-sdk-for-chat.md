# ADR-0020: Confine LangChain to retrieval; keep chat on the raw OpenAI SDK

- **Status:** accepted
- **Date:** 2026-07-24
- **Pull request:** `feat/interview-rag` → `dev` (merge `424c123`); reaffirmed later when weighing whether to make the code "more LangChain-consistent" — back-filled

## Context

RAG is built on LangChain (embeddings, in-memory vector store, text splitter). The question arose whether to also express the chat-completion calls (interview stream, guardrail, query rewrite) in LangChain (`ChatOpenAI`, `HumanMessage`/`SystemMessage`) for uniformity. The streaming path depends on OpenRouter-specific wire details: `usage.include` for the real per-call cost, `reasoning.effort`, reasoning-token counts, and a hand-rolled token loop the concurrent guardrail can interrupt mid-stream.

## Decision

Use LangChain **only** for retrieval (embeddings + vector store + splitter). Every chat completion uses the raw OpenAI SDK pointed at OpenRouter. Reconsider LangChain message objects only if/when LangGraph is introduced (see ADR-0050).

## Trade-off accepted

Two client idioms in one codebase instead of one. In exchange we keep full, transparent control of the streaming / cost / reasoning plumbing the cost-accounting feature relies on, and we contain LangChain's API-churn surface to a single module. We forgo conveniences like `.with_structured_output()` for the guardrail's JSON.

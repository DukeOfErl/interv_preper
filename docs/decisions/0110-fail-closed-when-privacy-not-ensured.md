# ADR-0110: Fail closed when provider privacy is not ensured

- **Status:** accepted
- **Date:** 2026-07-27
- **Pull request:** branch `feat/provider-data-privacy-policy`

## Context

ADR-0100 introduced the per-provider privacy registry and made every outbound call carry the provider's no-training parameters. But it shipped **disclose-only**: at the `UNKNOWN` level the app still sent the data and turned a sidebar badge red.

That is not a control. The user identified the flaw precisely: *"A message that tells me this is not safe, after I already uploaded and sent stuff (example to the embeddings model) is no use: the data is already exposed for training."* A resume is embedded during upload — by the time any badge could be read, the document has been parsed, sent to the guardrail classifier, and sent to the embeddings endpoint. Disclosure after egress is a post-mortem, and an irreversible one: there is no unsend.

It was also inconsistent with the project's own precedent. **ADR-0040** established fail-closed document screening: a document is indexed only after a clean scan, and a scan that cannot complete rejects the document. The reasoning applies with more force here — a mis-screened document is a prompt-injection *risk*, whereas an unprotected upload is a *completed* disclosure of the user's employment history.

A second, related weakness: the embeddings privacy params reached the wire through LangChain's `model_kwargs` forwarding — undocumented behavior that a dependency upgrade could silently stop honoring. ADR-0100 accepted that risk and covered it with a test. Under a fail-closed posture that trade is no longer acceptable: a test catches the regression at CI time, but the window between an upgrade and a test run is a window of silent, irreversible exposure.

## Decision

Make the privacy policy a **gate, not a notice**, and enforce it at two levels.

**By construction (the invariant).** `require_privacy_extra_body()` raises `PrivacyNotEnsuredError` for an unensured provider, and **every client that transmits user content calls it in its constructor** — `InterviewLLM`, `JailbreakGuard`, `QueryCondenser`, `PrivateOpenAIEmbeddings`, and both `evals/judge.py` clients. No object capable of sending user data to an unvouched-for provider can be built, so a call site added later inherits the protection instead of having to remember it. The non-raising `policy_for()` / `privacy_extra_body()` accessors remain for the UI, which must be able to *describe* a posture the app refuses to send to.

**At the UI (the legible failure).** `chat_bot.main()` resolves the policy, renders the status into the sidebar, then `st.error(...)` + `st.stop()` — placed before `sync_documents()`, so the uploader widget never renders. The user cannot hand over a document at all, rather than being told afterwards.

The threshold: `ENFORCED` and `PROVIDER_STATED` may send; `UNKNOWN` is blocked. Both passing levels rest on a real no-training basis — a request parameter, or published terms with a verified-on date. **No override**: no "send anyway" checkbox, no session flag.

Replace `langchain_openai.OpenAIEmbeddings` with `PrivateOpenAIEmbeddings`, a ~25-line adapter over the raw OpenAI SDK implementing the two `Embeddings` methods `InMemoryVectorStore` actually calls. `extra_body` becomes an explicit argument that cannot silently stop being sent.

This **supersedes ADR-0100's disclose-only stance**; the rest of ADR-0100 (the registry, the three levels, `data_collection: deny` over `zdr`, always-on disclosure) stands.

## Trade-off accepted

**An unregistered endpoint bricks the app.** Point the app at a self-hosted gateway or an unlisted proxy and it refuses to run until someone adds a `PROVIDER_POLICIES` entry — even if that endpoint is in fact perfectly private (a local model touches no third party at all). This is the deliberate cost of a hard block with no override: the alternative is a bypass, and a bypass is the thing that gets clicked through. The error names the fix, so it is a speed bump rather than a dead end.

**Dropping LangChain's embeddings class costs its conveniences.** `check_embedding_ceil_length` chunking, retry/backoff, and tiktoken-based pre-tokenization are gone; the app now owns batching (`EMBEDDING_BATCH_SIZE`). Since `check_embedding_ctx_length=False` was already set — the tiktoken path only works for OpenAI-named models — little was actually in use, but a very long single chunk now relies on the provider's own limits rather than being pre-split. Accepted: `RecursiveCharacterTextSplitter` already bounds chunk size upstream.

**The failure is now loud where it used to be quiet, including in tests.** Constructors raising means any test or script that instantiates a client with an unregistered `base_url` fails instead of proceeding. That is the point, but it makes `base_url` a load-bearing argument rather than an incidental one.

**`PROVIDER_STATED` still passes the gate on a claim we cannot verify.** For OpenAI and Anthropic the app permits sending on the strength of published terms plus a hand-maintained date (`PRIVACY_POLICY_CHECKED_DATE`). This is weaker than `ENFORCED` and the UI says so, but it is a gate that trusts a document rather than a mechanism — the honest limit of what a client application can establish.

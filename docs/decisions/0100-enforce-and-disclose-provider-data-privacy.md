# ADR-0100: Enforce provider data privacy where possible, disclose it always

- **Status:** accepted; the disclose-only stance below is **superseded by ADR-0110** (an `UNKNOWN` provider is now blocked, not merely reported, and the embeddings escape hatch described here has been removed). The registry, the three levels, and `data_collection: deny` over `zdr: true` all stand.
- **Date:** 2026-07-27
- **Pull request:** branch `feat/provider-data-privacy-policy`

## Context

The app sends the user's resume, cover letter, and target job ad to an LLM provider — genuinely personal data, uploaded by someone preparing for a real job search. Until now no outbound request said anything about how that data may be used, and **OpenRouter's default is permissive**: it routes to a pool that includes providers which train on submitted prompts, unless told not to. The app was therefore opting into training by omission.

The app is OpenRouter-only today but is intended to let users bring their own provider (an OpenAI or Anthropic account) later. Research into what each provider actually offers ruled out the obvious "just send a no-training flag everywhere" approach:

| Provider | Per-request control | Baseline policy |
|---|---|---|
| OpenRouter | `provider.data_collection: "deny"`, honored on `/chat/completions` **and** `/embeddings`; also `provider.zdr: true` | routes to training providers unless told otherwise |
| OpenAI direct | only `store: false`, which governs retention rather than training | API data not used for training (since 2023-03-01); ZDR by sales agreement |
| Anthropic direct | none | API inputs/outputs not used for training; short retention window; ZDR by commercial agreement |

So the privacy posture is a property of the *provider*, and for two of the three it is a contractual claim we can only repeat, not a parameter we can send. A design that reported a single binary "private / not private" would have to either overclaim for the direct providers or underclaim for OpenRouter.

A second constraint surfaced in the embeddings path: document chunks are embedded through LangChain's `OpenAIEmbeddings`, which exposes no `extra_body` field, so the ordinary mechanism for sending provider params is unavailable there.

## Decision

Introduce `interview_prep/privacy.py` as the single source of truth: a `PROVIDER_POLICIES` registry keyed by `base_url`, mapping each known provider to a `PrivacyPolicy` carrying its level, the `extra_body` params that enforce it, and a one-sentence explanation. Adding a provider is a new dict entry, not a code change.

Model the posture as **three levels**, not two:

- **`ENFORCED`** — a request parameter closes it (OpenRouter's `data_collection: "deny"`).
- **`PROVIDER_STATED`** — no such parameter exists; the provider's published terms are the guarantee, shown with the date they were last verified (`config.PRIVACY_POLICY_CHECKED_DATE`).
- **`UNKNOWN`** — an unregistered `base_url`. Flagged, never assumed safe.

Every outbound call routes through `privacy_extra_body(base_url)` — the interview stream, the jailbreak guardrail, the document guardrail, the query condenser, the embeddings call, and both eval clients. `embedding_privacy_extra_body()` narrows the body to the keys `/embeddings` accepts, since OpenAI's `store` is chat-only and would be rejected.

For the embeddings call specifically, pass the params through LangChain's `model_kwargs`, which is merged into the SDK's `embeddings.create()` call. Pin that behavior with a test that drives a real `OpenAIEmbeddings` against a stubbed transport.

Send `data_collection: "deny"` but **not** `zdr: true`. Render the posture as a colour-coded one-line status at the top of the Interview tab — green / blue / red — on every run, not only when something is wrong.

## Trade-off accepted

**Some models stop working.** With `data_collection: "deny"`, a model whose only providers store or train on data now fails the request instead of quietly routing to one. That is the point, but it converts a silent privacy loss into a visible error, so the `APIError` message in `chat_bot.py` had to name the privacy restriction as a likely cause.

**We chose model availability over maximum strictness.** `zdr: true` would be stronger, but zero-data-retention endpoints are a much smaller pool — it would break most model choices — and `zdr` is undocumented for `/embeddings`, so the embedding call might not honor it while the UI implied it did. Declining it means retention-but-no-training providers remain reachable.

**The embeddings path rests on an escape hatch.** `model_kwargs` forwarding is LangChain implementation behavior, not a documented contract; a future upgrade could stop forwarding it and resumes would start going out unprotected *silently*. This is the reason for the dedicated test — the failure mode is invisible, so it must be asserted rather than trusted.

**`PROVIDER_STATED` is only as fresh as a hardcoded date.** For OpenAI and Anthropic the app repeats a claim it cannot verify, timestamped by hand. That date will drift unless someone re-reads the providers' terms and bumps it. Displaying the date is an honest admission of that limit rather than a fix for it.

**`base_url` does double duty as provider identity and transport target.** Keying the registry by base URL keeps the design to one dict, but it means the constant that selects a policy is the same constant that addresses the endpoint. Editing it to reach the `UNKNOWN` state renames the registry key and the lookup key together — the app keeps claiming privacy while every request fails. The `UNKNOWN` branch is therefore only reachable when a *different* base URL arrives at a call site, which is what the eventual provider selector will do. Until then, exercise that state by dropping the registry entry (not the constant), and note that `test_privacy` pins the constant to a real OpenRouter endpoint so the mistake breaks the suite rather than shipping a false claim.

**Always-on disclosure costs sidebar space** in the common case where everything is fine. Accepted deliberately: for a user uploading a resume, an absence of warnings is indistinguishable from an absence of checking, so positive confirmation is worth the line.

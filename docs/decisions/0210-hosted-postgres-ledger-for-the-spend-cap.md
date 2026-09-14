# ADR-0210: A hosted Postgres ledger for the spend cap — and why it is not the best answer

- **Status:** accepted
- **Date:** 2026-09-04
- **Pull request:** #TBD — WP4, `feat/spend-cap`

## Context

§ 21 settled who may use the app: an invited person, signed in with Google and
present in the operator's allowlist. It settled nothing about *how much* any one
of them may spend. Every turn draws on the operator's own OpenRouter credit, and
an allowlisted user with an afternoon free can spend an unbounded amount of it.

R20.13's original answer was to make a `user` bring their own API key. That is
withdrawn (§ 20): it was written when a `user` was any stranger who found the
URL, and it defends the wallet by removing the reason to visit. The operator
pays, and a per-identity cap becomes the only spend control.

A cap needs a number that survives. Streamlit Community Cloud recycles
containers freely, so the ADR-0110 pattern — a SQLite file under `data/` — gives
a total that silently resets to zero on a restart. A cap that resets is not a
cap, and its failure is invisible: nothing looks wrong, the number is simply
young. Two containers may also serve the same person at once, so an in-process
total is a cache and never an authority.

Two mechanisms were on the table.

1. **An external Postgres** (Supabase/Neon-class free tier), read and written on
   every turn, keyed by the authenticated email.
2. **OpenRouter's per-user provisioned keys.** `POST /api/v1/keys` mints a key
   with a `limit`, supports `limit_reset`, and exposes `usage_monthly` /
   `limit_remaining` and `disabled`. The provider enforces the ceiling and
   refuses mid-request.

## Decision

**We are using an external Postgres, and option 2 is the better engineering
choice.** That sentence is the point of this ADR, and it is written plainly so
that a reader who finds a hosted database here does not assume the alternative
was overlooked.

Provisioned keys make overspending **structurally impossible** rather than
merely checked: the refusal happens at the provider, so no bug in this codebase
can spend past the ceiling. They need no durable storage, leave ADR-0110's "no
database ever needs hosting" intact, remove the fail-closed dependency of
R22.12 entirely, and deliver R21.19's per-identity audit for free. Their costs
are a higher-privilege provisioning credential and a work package thin enough to
finish in an afternoon.

Postgres was chosen **for a process reason that is not about this feature**:
this work package is the arena for a planned comparison between two agent
orchestration harnesses, and that comparison needs a task with enough substance
to discriminate between them. Provisioned keys are the better feature and the
worse experiment. The decision is therefore deliberate, documented, and made on
grounds a future maintainer is entitled to disagree with.

The shape that follows from it:

- The ledger sits behind a **port** — `total(email)` and `record(email, amount)`
  — with Postgres as one adapter and an in-memory adapter for tests (R22.14,
  R22.15). This is the same seam § 20 built for identity, for the same reason,
  and it is what makes the paragraph above reversible.
- The **cap policy is a pure function** of role, remaining budget and the
  next-turn estimate. It imports neither Streamlit nor a database driver.
- The check lives **inside the operation that spends** (R22.5), because
  `evals/` and `tests/` reach the paid clients without passing through the page.
- Spend counts **all six paid clients**, not the two that happen to report cost
  today (R22.2).

## Trade-off accepted

**What becomes harder.** The app gains a hard runtime dependency on a database
it does not own. R22.12 makes an unreachable ledger fail closed, so the store
going down takes the app down — the second expensive fail-closed default in this
codebase, after R21.10, and chosen for the same reason: the alternative silently
uncaps every account at exactly the moment nobody is watching. Every turn now
pays a network round trip before it starts.

**What is given up.** The property that this project could be cloned and run
with nothing but a Python environment and an API key. ADR-0110 stated that no
database here ever needs hosting; that is no longer true, and the statement is
narrowed rather than quietly abandoned — the knowledge base's SQLite file
remains derived, disposable and local, and this ledger is the single exception.

**What is deliberately not solved.** Overshoot. A turn's true cost is known only
after it completes, so any in-app cap can be exceeded by the turn in flight
(R22.7). Comparing against the next-turn estimate narrows the window to one
turn; nothing but a provider-side limit closes it — which is the same option 2
this ADR declined.

**The escape hatch is the port.** If the harness comparison ends and the hosted
database reads as the liability it partly is, swapping in a provisioned-key
adapter is one implementation of one protocol, and the policy, the call sites
and the tests do not move.

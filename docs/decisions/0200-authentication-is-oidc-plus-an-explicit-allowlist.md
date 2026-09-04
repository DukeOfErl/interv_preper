# ADR-0200: Authentication is OIDC plus an explicit allowlist, and the refusal guards the spend

- **Status:** accepted
- **Date:** 2026-09-03
- **Pull request:** #TBD — WP2, `feat/authentication`

## Context

ADR-0190 built the role model as a pure function behind an identity port, with
one deliberately temporary implementation: an environment variable. That was
honest configuration, not authentication, and R20.12 recorded the debt. The
deployment target is Streamlit Cloud, where an interviewee and the operator
reach the same URL, and **every turn spends the operator's own OpenRouter
credit**.

Three mechanisms were on the table:

1. **Streamlit's native OIDC** (`st.login` / `st.user`), available since 1.42
   and present in the pinned 1.58. Real per-user identity; needs `Authlib`, an
   OIDC client registered with a provider, and a per-environment
   `redirect_uri`.
2. **Streamlit Community Cloud's viewer authentication** — deploy privately,
   invite emails, and the platform populates `st.user` with no code at all.
   Free, but it binds the app to one host and makes it invite-only at the
   platform level.
3. **A shared-secret code table** in secrets. No dependency and no provider
   setup, but codes are not identities: two people sharing one are
   indistinguishable, so nothing later can be attributed or capped per person.

A fourth question cut across all three, and turned out to matter more than the
choice between them: **OIDC answers *who is this*, and does not answer *may
they be here*.** Every Google account holder on earth can complete a Google
sign-in. On an app that spends the operator's money per turn, a successful
login is not grounds to serve someone.

## Decision

**Authentication is Streamlit's native OIDC; authorization is a separate,
explicit allowlist; and the refusal is enforced where the money is spent.**

- Login is required (R21.2). An unauthenticated visitor gets a sign-in control
  and nothing else.
- A single table in secrets maps **authorized email → role**. Presence is
  authorization, absence is refusal (R21.5). One table rather than two, so an
  allowlist and a role map cannot drift apart.
- The two failure modes are graded in opposite directions on purpose: an
  unrecognized *role name* costs privilege but keeps access (R21.7), while an
  absent, unreadable or non-mapping *table* authorizes nobody (R21.8).
- Authorization is **not** modelled as a `Permission` (R21.12). Permissions
  grade what an authorized role may do; authorization decides whether there is
  a role at all.
- The guard lives inside the operation. `chat_bot.py` refuses early for a clean
  message, but the agent refuses too, and requires an authorized identity to be
  handed to it before it will call a model (R21.10, R21.11).

The last point is the one that changed during design. The obvious reading of
"guard the spend" was a check inside `stream_reply`. But tracing the money
found **six** paid clients in `interview_prep/` — the agent, the guardrail, the
query condenser, the web researcher, and two embedding sites — all fed from a
single `api_key` obtained once in `chat_bot.main()`. Guarding only the agent
would have guarded one of six while looking like it guarded spending, which is
the exact failure shape this project keeps finding: a control that reports
success without holding.

## Trade-off accepted

**What becomes harder.** The app gains a hard dependency on `Authlib` and on an
OIDC client the operator must register and maintain, with a `redirect_uri` that
differs between local and deployed and will silently break if it drifts. A
misconfigured allowlist now serves **nobody** — R21.8 is the one place in this
codebase where failing closed is expensive rather than cheap, and it was chosen
knowing that the alternative is a deployment that quietly admits the world to
the operator's credit.

**What is given up.** The frictionless path: a stranger can no longer open the
URL and try a mock interview. That is a real product cost, and it is accepted
because the operator pays for every turn.

**What becomes easier.** Every turn now carries a stable email, which is the
missing foundation under both remaining work packages: R20.13's per-role API
key needs to know whose key to spend, and R20.14's per-identity spend cap needs
something durable to count against. Neither is buildable on a code table, and
both fall out of this almost for free.

**What is deliberately *not* decided here.** Whether the app's key is spent on
a `user`'s behalf at all (R20.13), and where per-identity spend is stored
(R20.14, which still runs against ADR-0110's "no database ever needs hosting").
This ADR makes both answerable; it answers neither.

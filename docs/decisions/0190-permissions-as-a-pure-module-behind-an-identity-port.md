# ADR-0190: Permissions as a pure module behind an identity port

- **Status:** accepted
- **Date:** 2026-09-01
- **Pull request:** TBD — `feat/user-roles-and-permissions`

## Context

The app is headed for a shared Streamlit Cloud deployment: an interviewee and a
developer reach the same URL. The immediate ask is that a regular user not see
the Developer and Warnings tabs, and eventually that each user have their own
API key and spend cap.

Stated that way the work sounds like a UI change. The four tabs are created on
one line in `chat_bot.py`, and gating two of them is about ten lines. That
framing is the trap.

**The app has three callers, and only one of them is the page.** `evals/` loads
the real composed prompt and generates replies; `tests/` constructs
`InterviewAgent` and calls `stream_reply()` directly. Both bypass `chat_bot.py`
entirely. A spend cap or a capability check written into the page therefore
holds for one of three entry points today — not at some future date when a
second caller appears, but already. The same argument retired the per-handler
guardrail calls in ADR-0150: screening that lived in each tool's handler was
opt-in by convention, and the tool whose author did not think about it shipped
unscreened with a green suite.

The failure is also asymmetric in a way that matters. A permission wrongly
*denied* generates a complaint within minutes — someone reports a missing tab.
A permission wrongly *granted* raises no exception, writes no warning, and
produces output that looks entirely ordinary. Nothing surfaces it until it is
exploited. That rules out validating this feature by clicking around as a
`dev`, which is the obvious and useless test.

Two further constraints shaped the shape:

- **Streamlit reruns constantly**, and anything parked in `st.session_state` is
  readable and writable by any code in the process. A role cached there is not a
  fact about the requester; it is a mutable variable that happens to hold a role.
- **Authentication is not decided.** Streamlit's native OIDC and a
  secrets-based code table are both plausible, and the choice depends on whether
  this stays a portfolio demo. Waiting for that answer would block work that
  does not depend on it.

## Decision

Permissions live in their own module as a **pure function of (role,
permission)**, importing neither Streamlit nor any agent framework — the same
isolation `policy.py` keeps, for the same reason: the thing being guarded must
not be able to influence the guard, and a test must be able to ask the question
without a browser.

Four commitments follow:

- **Permissions name capabilities, not widgets.** *View diagnostics*, not
  *show the Developer tab*. A tab is one consumer of a capability; the
  capability is what a future spend path or key resolver will ask about.
- **Fail closed to `user`.** An unknown role name, an absent identity, a
  malformed override, or any exception while resolving yields the least
  privilege. The cheap direction absorbs the uncertainty, and this ADR is where
  that choice is recorded so nobody rebalances it by accident.
- **Hiding is presentation; guarding belongs to the operation.** The UI may hide
  what a role cannot use, but any operation that must be restricted is guarded
  in the function that performs it. Gating the tab is not the deliverable — it
  is the first consumer of the deliverable.
- **Identity sits behind a port** — one seam, one function — with a single
  implementation for now: an override read from the environment or Streamlit
  secrets, never from a URL parameter or `session_state`. The role is resolved
  from the port on each run.

Authentication, per-role API keys, and the durable spend cap are deferred to
their own work packages (R20.12–R20.14), each with its own ADR.

## Trade-off accepted

**There is no authentication.** With the override as the only identity source,
the role is configuration and the deployment is exactly as private as its URL.
Anyone who reaches the app is a `user`; nobody can become a `dev` without
server-side configuration, which is the property worth having — but this is
access *control* without access *authentication*, and it must be said plainly
wherever the deployment is documented rather than left for a reader to infer
from a permission model that looks complete.

**The port costs an indirection that buys nothing today**, since it has one
implementation. It is justified only by the second one arriving, and if
authentication is never built the seam will read as premature. Accepted because
it is what lets this slice ship, be reviewed, and be merged before the
authentication question is answered — and because retrofitting a seam through
call sites that assumed a global is materially more expensive than leaving one
in place.

**The permission vocabulary is deliberately thin** — two roles, one permission.
A permission constant that nothing enforces is worse than an absent one,
because it reads as a guarantee. So key and spend permissions are defined in the
work packages that enforce them, not now, at the cost of those slices touching
this module again.

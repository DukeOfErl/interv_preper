"""The per-identity spend cap: the policy, and the port it reads (§ 22).

Deliberately separate from the store that holds the numbers. This module
decides *whether a turn may happen*; `ledger_postgres.py` knows how to talk to
a database, and the in-memory adapter below is what the tests use. The split is
the same seam § 21 built for identity, for the same reason: a policy that
imports a driver stops being testable without one (R22.14, R22.15), and a cap
nobody can test is a control that reports success without holding.

**The port is two methods.** `total(email) -> float` and
`record(email, amount) -> None`. An adapter that cannot reach its store raises
`LedgerUnavailable`; every other failure mode is the adapter's to hide.

**Three things this module is careful about**, each of which is a way a cap can
look right and hold nothing:

* The refusal compares the *estimate* against what is left, not the total
  against the cap (R22.6). Refusing only once the total is already over spends
  a full turn past the cap on every account, so the overshoot becomes the rule.
* "You are at your limit" and "the ledger is unreachable" are different facts
  about different parties (R22.13). They are separate `reason` values here so
  that a caller cannot accidentally tell an innocent user they overspent.
* `dev` is exempt from the *cap*, not from the *ledger* (R22.9). An operator
  whose store is down is the one person who most needs to notice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .authorization import Identity, normalise_email
from .permissions import Role

#: The floor for a caller that supplies no estimate at all — `evals/`, a
#: script, a future driver. **The page does not use it**: it prices an
#: unmeasured turn from the composed prompt and the model's real rate
#: (`chat_bot.current_budget`'s caller), because the input side of that
#: estimate is known exactly and only the reply is a guess.
#:
#: Sized against the worst turn this app can produce — `MAX_TOOL_HOPS` model
#: calls on `DEFAULT_MODEL`, about $0.04 — and erring high, which is the cheap
#: direction: guessing too high costs one refusal a user can explain, guessing
#: too low costs a turn that should not have run. That relationship is a
#: property of another file's `DEFAULT_MODEL`, so it is asserted against live
#: prices rather than left to hold by coincidence
#: (`tests/test_spend_policy.py::test_the_unmeasured_fallback_still_covers_a_worst_case_turn`).
UNMEASURED_TURN_USD = 0.05

#: Budgets are decimal money held in binary floats, so a turn that exactly fits
#: what remains must not be refused by a rounding error. The boundary belongs
#: to the user (R22.6): the last of everyone's budget has to be spendable.
_EPSILON = 1e-9


class LedgerUnavailable(Exception):
    """Raised by an adapter whose store cannot be reached.

    Deliberately declared here rather than in the adapter: the policy is what
    has to recognise it, and an adapter that imported the policy's caller to
    find its own exception would invert the dependency this module exists to
    keep pointing one way.
    """


class OverBudget(Exception):
    """Raised by the guard when a turn must not happen.

    Carries the `CapDecision` that produced it, because the two refusals need
    different words (R22.13) and a caller that had to parse the message to tell
    them apart would eventually get it wrong.
    """

    def __init__(self, message, decision=None):
        super().__init__(message)
        self.decision = decision


class Ledger(Protocol):
    """The store, as the policy sees it (R22.14)."""

    def total(self, email: str) -> float:
        """Lifetime USD spent by `email`, or 0.0 for an email never seen."""

    def record(self, email: str, amount: float) -> None:
        """Add `amount` USD to `email`'s lifetime total."""


@dataclass(frozen=True)
class CapDecision:
    """Whether a turn may happen, why not, and what is left.

    `remaining` is None for a role that is not capped — an uncapped role has no
    budget to show (R22.17) — and for a decision made without reading the
    store, where the honest answer is "unknown" rather than a number.
    """

    allowed: bool
    #: None when allowed; otherwise "at_cap", "ledger_unavailable",
    #: "unauthorized", or "unpriced". The third exists because the refusal has to name the
    #: right party (R22.13) and a caller with no identity has not overspent —
    #: telling them "the spend limit has been reached" is a lie about a budget
    #: they do not have. It is reachable: the authorization guard fires only
    #: when a *real* paid client is constructed, so a caller injecting a fake
    #: reaches this module without having been refused by § 21.
    reason: str | None
    remaining: float | None


def decide(*, role, spent, cap, estimate) -> CapDecision:
    """The cap, as a pure function of role, spend, cap and next-turn estimate.

    No identity, no store, no clock: everything that could fail has already
    happened by the time this is called, which is what makes the rule itself
    cheap to test against the requirement rather than against an observation.
    """
    if role is Role.DEV:
        # R22.9. Not merely a large budget — no budget, so a cap of 0.00 (or
        # none at all) does not lock the operator out of their own deployment.
        return CapDecision(allowed=True, reason=None, remaining=None)
    if role is not Role.USER:
        # `role is None` means authorization refused this caller (§ 21), and it
        # must not fall through to the capped branch and be handed a budget.
        # Anything else that is not a `Role` is a caller this module cannot
        # grade, which is the same answer — `is`, not `!=` or a truth test, so
        # a string, a mock or a `Role` from a reloaded module is capped rather
        # than accidentally uncapped (R22.9's coupling, in the expensive
        # direction).
        return CapDecision(allowed=False, reason="unauthorized", remaining=0.0)

    # An overshoot of one turn is a reachable state, not a corrupt one (R22.7),
    # so a total above the cap renders as "nothing left" rather than as a
    # negative budget in the sidebar.
    remaining = max(0.0, cap - spent)
    if remaining <= 0.0:
        # Nothing left at all, so nothing can fit — refuse whatever the
        # estimate says. Without this line an estimate of zero is "affordable"
        # against an empty budget, and an estimate of zero is not hypothetical:
        # R22.3 degrades unknown pricing to zero, and the catalog is
        # unreachable exactly when the network is. The named hole is that an
        # unpriced model is uncapped; it must not widen into the cap itself
        # never refusing anyone.
        return CapDecision(allowed=False, reason="at_cap", remaining=0.0)
    projected = UNMEASURED_TURN_USD if estimate is None else estimate
    if projected <= remaining + _EPSILON:
        return CapDecision(allowed=True, reason=None, remaining=remaining)
    return CapDecision(allowed=False, reason="at_cap", remaining=remaining)


def check_budget(identity, *, ledger, cap, estimate) -> CapDecision:
    """Read the store for `identity` and apply `decide` to what it says.

    The store is the source of truth and is read every turn (R22.11): two
    containers may be serving the same person at once, so anything this process
    remembers is a cache of a number someone else may have moved.
    """
    if not isinstance(identity, Identity) or not identity.is_authorized:
        # The same `isinstance` rather than truthiness as `require_authorized`
        # (R21.13): an object that merely has `.role` and `.email` has not been
        # through the allowlist, and must not be able to buy anything.
        return CapDecision(allowed=False, reason="unauthorized", remaining=0.0)

    if normalise_email(identity.email) is None:
        # An identity with no usable address cannot be billed: the read would
        # match no row and report "has spent nothing", and the write would be
        # rejected by the primary key — uncapped in the direction that costs
        # money, unrecordable in the direction that would have made it visible.
        # `authorize()` cannot produce one today; that is a property of another
        # module, which is exactly why it is checked here.
        return CapDecision(allowed=False, reason="unauthorized", remaining=0.0)

    try:
        spent = ledger.total(identity.email)
    except Exception:
        # R22.12, fail closed: no total, no turn. Broad, because a store this
        # process cannot read is a store this process cannot read — an adapter
        # that lets a driver error escape instead of translating it has still
        # told us the only thing that matters here.
        return CapDecision(allowed=False, reason="ledger_unavailable", remaining=None)

    return decide(role=identity.role, spent=spent, cap=cap, estimate=estimate)


def require_within_budget(identity, *, ledger, cap, estimate) -> None:
    """Refuse the operation unless `identity` can afford it (R22.5).

    Called *inside* each paid client, next to `require_authorized`, because
    `evals/`, `tests/` and any future service reach those clients without
    passing through `chat_bot.py`, and a page-only cap protects one of three
    callers. The message carries the facts rather than deferring to a page that
    may not exist.
    """
    decision = check_budget(identity, ledger=ledger, cap=cap, estimate=estimate)
    if decision.allowed:
        return
    # `.strip()`, because an address of "   " is not a name: without it the
    # refusal reads "refusing to spend on behalf of    :".
    who = (getattr(identity, "email", None) or "").strip() or "an unidentified caller"
    if decision.reason == "unauthorized":
        raise OverBudget(
            f"refusing to spend on behalf of {who}: no authorized identity, so "
            "there is no budget to spend from. This is not a limit that was "
            "reached. Pass `identity=` an authorized `Identity` from "
            "`interview_prep.authorization.authorize`.",
            decision,
        )
    if decision.reason == "ledger_unavailable":
        raise OverBudget(
            f"refusing to spend on behalf of {who}: the spend ledger is "
            "unreachable, so spending cannot be counted. This is a deployment "
            "fault, not a budget that ran out.",
            decision,
        )
    raise OverBudget(
        f"refusing to spend on behalf of {who}: the spend limit of "
        f"${cap:,.2f} has been reached.",
        decision,
    )


@dataclass(frozen=True)
class Budget:
    """The cap as a paid client sees it: a store, a ceiling, an expectation.

    One object rather than three parameters threaded through six constructors,
    and one per *turn*: `estimate` is the next-prompt figure the page computes
    for this turn (R10.5, R22.6).

    **A `Budget` does go stale, and two clients can hold a stale one.** The
    document index and the knowledge base live in session state and outlive the
    turn that built them, so the page hands them the current turn's budget on
    every run. An earlier version of this docstring claimed the object "cannot
    go stale by construction", which was true of the four per-turn clients and
    false of those two — and a false claim in a docstring is what made the
    embedding paths' $0.00 unobservable for two commits. `cap` and `ledger` are
    constant; it is `estimate` that rots, and it is the input R22.6 turns on.
    """

    ledger: Ledger
    cap: float
    estimate: float | None = None

    #: Whether spend recorded against this budget goes anywhere. Lets a client
    #: skip computing a cost nobody will store — which matters because pricing
    #: a turn can mean a catalog lookup.
    counts = True

    def require(self, identity, estimate=None) -> None:
        """Refuse the operation unless `identity` can afford it.

        `estimate` overrides the turn's figure for an operation that knows its
        own size. R22.6 says to compare against the estimate the app computes,
        and for a chat turn that is the next-prompt estimate — but a document
        scan fans out into one paid call per window, and admitting it on the
        strength of an unrelated chat estimate is how a $5 cap pays for a $24
        upload (R22.7's bound is one turn, not one upload).
        """
        require_within_budget(
            identity,
            ledger=self.ledger,
            cap=self.cap,
            estimate=self.estimate if estimate is None else estimate,
        )

    def record(self, identity, amount) -> bool:
        """Add `amount` to `identity`'s lifetime total; False if the store refused.

        A failed write does not raise, because by the time anything is recorded
        the money is already spent and killing the turn would lose the user
        their answer without saving the operator a cent. It is not silently
        forgiving either: the same store is read before the *next* turn, and a
        store that cannot be written usually cannot be read, so an outage still
        stops the spending within one turn (R22.12).
        """
        if not amount:
            # A zero-cost turn is legitimate (R22.3 degrades unknown pricing to
            # zero) but there is nothing to add, and a round trip to add it
            # would put the store on the critical path for no reason.
            return True
        try:
            self.ledger.record(getattr(identity, "email", None), amount)
        except Exception:
            return False
        return True


class _Uncapped:
    """The absence of a cap — *not* a cap that failed (R22.12 is not this).

    A client built without a budget spends nothing this module can see: the
    tests inject fakes, `evals/` brings its own accounting, and a deployment
    with no `[spend]` block has no ledger to count into. Saying that with a
    null object rather than with `if budget is not None` at every call site
    keeps the difference between "no cap configured" and "cap unreadable" from
    being decided by an accident of control flow.
    """

    counts = False

    def require(self, identity, estimate=None) -> None:
        return None

    def record(self, identity, amount) -> bool:
        return True


#: The budget a caller passes to state deliberately that nothing is counting.
UNCAPPED = _Uncapped()


def resolve_budget(budget, what="this operation"):
    """Return the budget a paid client must spend under, refusing `None`.

    The asymmetry this fixes: `identity=None` fails **closed** — the client
    raises `Unauthorized` rather than serving an unidentified caller — while
    `budget=None` used to fail **open**, quietly resolving to `UNCAPPED`. A
    caller could therefore bypass the cap by omitting one keyword, which is the
    "guards belong to the operation" corollary that § 21 was written around:
    the arming must not be the caller's to forget.

    So the absence of a budget is now a refusal, and *uncapped* has to be said
    out loud. `evals/` and any script that genuinely does not count passes
    `budget=UNCAPPED`, which reads as the deliberate statement it is. Checked
    only where the real client is built, so a test injecting a fake — which
    spends nothing — is unaffected.
    """
    if budget is None:
        raise OverBudget(
            f"refusing to build {what} with no budget. Pass a `Budget` to "
            "count its spend against an identity, or `budget=UNCAPPED` to say "
            "deliberately that nothing is counting. Defaulting to uncapped "
            "would let a caller bypass the cap by omitting one keyword "
            "(R22.5).",
            CapDecision(allowed=False, reason="unarmed", remaining=None),
        )
    return budget


def refuse_unpriceable(identity, what) -> None:
    """Refuse an operation whose cost cannot be estimated at all (R22.7).

    Deliberately not the unmeasured *floor*. That floor is turn-sized, which is
    the right guess for a chat turn — genuinely one turn — and an unrelated
    number for the one operation here whose fan-out is unbounded: a document
    scan is one paid classifier call per window, measured at 5,668 calls and
    about $2.44 for a 20 MB upload. Admitting that on a five-cent guess is not
    a conservative estimate, and R22.7's accepted overshoot is one turn.

    So when the price is unknown, the *size* of the operation is still known
    and the dollars are not — and the cheap direction absorbs the uncertainty,
    the same trade R22.12 makes for an unreachable ledger. A refused upload is
    fixed by retrying when the catalog is back; an admitted one is a bill.

    Its own reason, because a user who cannot upload during a pricing outage
    has not overspent and their ledger is not down (R22.13).
    """
    who = (getattr(identity, "email", None) or "").strip() or "an unidentified caller"
    raise OverBudget(
        f"refusing to run {what} for {who}: its cost cannot be estimated "
        "right now, because no price could be read for the model it uses. "
        "This is temporary — it is not a budget that ran out.",
        CapDecision(allowed=False, reason="unpriced", remaining=None),
    )


class InMemoryLedger:
    """The port's process-local adapter: durable for exactly as long as a test.

    Not a cache of the real ledger and not a fallback for one — R22.11 makes an
    in-process total a cache and never an authority, and something that quietly
    stood in for an unreachable store would uncap every account at the moment
    nobody is watching.
    """

    def __init__(self):
        self._totals: dict[str | None, float] = {}

    def total(self, email) -> float:
        return self._totals.get(normalise_email(email), 0.0)

    def record(self, email, amount) -> None:
        key = normalise_email(email)
        self._totals[key] = self._totals.get(key, 0.0) + float(amount)

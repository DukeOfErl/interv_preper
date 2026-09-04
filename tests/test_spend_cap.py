"""The per-identity spend cap (§ 22, ADR-0210).

Written before the implementation, from the requirements rather than from any
observed output. Every assertion below should be traceable to an R22.x, and
where a number appears it comes from the rule, not from running the code.

Three things make this worth testing hard, and they are the same three that
make a spend cap easy to get wrong in a way nobody notices:

  * **A cap that under-counts still passes every happy-path test.** Six paid
    clients spend the operator's credit; only two report cost today (R22.2).
    A ledger fed by those two is a control that reports success without
    holding, and it looks identical to a working one until the bill arrives.
  * **The two refusals are not interchangeable.** "You are at your limit" and
    "the ledger is unreachable" are different facts about different parties
    (R22.13), and collapsing them tells an innocent user they overspent.
  * **`dev` is uncapped (R22.9)**, so any bug that mis-resolves a role into
    `DEV` grants unbounded spend. That coupling to § 20 is deliberate and gets
    its own assertions rather than being left implicit.

The store is a port with an in-memory adapter, so nothing here touches a
network or a credential (R22.15).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from interview_prep.authorization import ANONYMOUS, Identity
from interview_prep.permissions import Role
from interview_prep.spend import (
    InMemoryLedger,
    LedgerUnavailable,
    OverBudget,
    check_budget,
    decide,
    require_within_budget,
)

CAP = 5.00  # dollars; an arbitrary operator setting, not a constant of the app


def user(email: str = "candidate@example.com") -> Identity:
    return Identity(email=email, role=Role.USER)


def dev(email: str = "operator@example.com") -> Identity:
    return Identity(email=email, role=Role.DEV)


class UnreachableLedger:
    """An adapter whose store is down. Both operations fail, because a store
    that cannot be read usually cannot be written either — and a cap that can
    read but not record is worse than one that does neither.
    """

    def total(self, email: str) -> float:
        raise LedgerUnavailable("connection refused")

    def record(self, email: str, amount: float) -> None:
        raise LedgerUnavailable("connection refused")


# --- R22.1: the ledger accumulates a lifetime total -------------------------


def test_an_unknown_email_has_spent_nothing():
    assert InMemoryLedger().total("nobody@example.com") == 0.0


def test_spend_accumulates_across_recordings():
    ledger = InMemoryLedger()
    ledger.record("candidate@example.com", 0.25)
    ledger.record("candidate@example.com", 0.75)
    assert ledger.total("candidate@example.com") == pytest.approx(1.00)


def test_identities_are_billed_separately():
    ledger = InMemoryLedger()
    ledger.record("one@example.com", 1.00)
    ledger.record("two@example.com", 2.00)
    assert ledger.total("one@example.com") == pytest.approx(1.00)
    assert ledger.total("two@example.com") == pytest.approx(2.00)


def test_the_ledger_normalises_email_the_way_the_allowlist_does():
    """`authorize()` compares addresses case-insensitively and
    whitespace-stripped, so `Candidate@Example.com` and `candidate@example.com`
    are one authorized person. If the ledger disagrees, that person gets two
    rows and twice the cap — a bypass that needs no attacker, only a phone
    keyboard that capitalises the first letter.
    """
    ledger = InMemoryLedger()
    ledger.record("Candidate@Example.com", 3.00)
    ledger.record("  candidate@example.com  ", 1.00)
    assert ledger.total("CANDIDATE@EXAMPLE.COM") == pytest.approx(4.00)


def test_recording_zero_is_allowed():
    """R22.3: unknown pricing degrades to zero rather than raising. A turn on
    an unpriced model must still complete, and must still be recorded.
    """
    ledger = InMemoryLedger()
    ledger.record("candidate@example.com", 0.0)
    assert ledger.total("candidate@example.com") == 0.0


# --- R22.9: dev is not capped -----------------------------------------------


def test_dev_is_allowed_far_past_the_cap():
    verdict = decide(role=Role.DEV, spent=CAP * 1000, cap=CAP, estimate=1.00)
    assert verdict.allowed


def test_dev_is_allowed_even_with_no_cap_configured():
    verdict = decide(role=Role.DEV, spent=99.0, cap=0.0, estimate=1.00)
    assert verdict.allowed


# --- R22.6/R22.7: refuse against the ESTIMATE, not the total ----------------


def test_a_user_well_under_the_cap_is_allowed():
    verdict = decide(role=Role.USER, spent=1.00, cap=CAP, estimate=0.10)
    assert verdict.allowed
    assert verdict.reason is None


def test_a_user_at_the_cap_is_refused():
    verdict = decide(role=Role.USER, spent=CAP, cap=CAP, estimate=0.10)
    assert not verdict.allowed
    assert verdict.reason == "at_cap"


def test_a_user_whose_next_turn_would_cross_the_cap_is_refused_first():
    """R22.6 is the substance of this file. Refusing only once the total is
    already over spends one full turn past the cap on every account — the
    overshoot becomes the rule rather than the exception. `spent` here is
    comfortably under the cap and the turn is still refused, because the
    estimate does not fit in what remains.
    """
    verdict = decide(role=Role.USER, spent=4.80, cap=CAP, estimate=0.50)
    assert not verdict.allowed
    assert verdict.reason == "at_cap"


def test_a_turn_that_exactly_fits_the_remaining_budget_is_allowed():
    """The boundary belongs to the user. Remaining is 0.50 and the estimate is
    0.50: it fits, so it runs. Refusing here would make the last of everyone's
    budget permanently unspendable.
    """
    verdict = decide(role=Role.USER, spent=4.50, cap=CAP, estimate=0.50)
    assert verdict.allowed


def test_the_decision_reports_what_is_left():
    verdict = decide(role=Role.USER, spent=1.25, cap=CAP, estimate=0.10)
    assert verdict.remaining == pytest.approx(3.75)


def test_remaining_never_goes_negative():
    """R22.7 accepts an overshoot of one turn, so a total *above* the cap is a
    reachable state, not a corrupt one. It must render as "nothing left"
    rather than as a negative budget in the sidebar.
    """
    verdict = decide(role=Role.USER, spent=CAP + 2.00, cap=CAP, estimate=0.10)
    assert not verdict.allowed
    assert verdict.remaining == 0.0


def test_a_zero_cap_refuses_every_user_turn():
    verdict = decide(role=Role.USER, spent=0.0, cap=0.0, estimate=0.01)
    assert not verdict.allowed
    assert verdict.reason == "at_cap"


def test_a_missing_estimate_is_not_treated_as_free():
    """R10.6: before any completed exchange exists the next-prompt estimate is
    "N/A". A user at 4.99 of a 5.00 cap must not be waved through on the first
    turn merely because nothing has been measured yet — the unknown falls back
    to the cheap direction, which here is refusing.
    """
    verdict = decide(role=Role.USER, spent=4.99, cap=CAP, estimate=None)
    assert not verdict.allowed


def test_a_missing_estimate_still_allows_a_user_with_budget():
    """The other direction of the same rule, because a classifier change moves
    records both ways: an unmeasured estimate must not refuse someone who has
    spent nothing, or no first turn could ever happen.
    """
    verdict = decide(role=Role.USER, spent=0.0, cap=CAP, estimate=None)
    assert verdict.allowed


# --- R22.9 fail-closed: no role means no spend ------------------------------


def test_an_unauthorized_identity_is_refused():
    """Authorization runs first (§ 21), so this should be unreachable — which
    is exactly why it is asserted. `role is None` means refused, and must not
    fall through to the `USER` branch and get a budget.
    """
    verdict = decide(role=None, spent=0.0, cap=CAP, estimate=0.10)
    assert not verdict.allowed


# --- R22.12/R22.13: an unreachable store fails closed, and says so ----------


def test_an_unreachable_ledger_refuses_the_turn():
    verdict = check_budget(user(), ledger=UnreachableLedger(), cap=CAP, estimate=0.10)
    assert not verdict.allowed


def test_an_unreachable_ledger_does_not_claim_the_user_is_over_budget():
    """R22.13. The user has spent nothing; the deployment is broken. Telling
    them they hit their limit is a lie the operator will hear about, and it
    sends them to the wrong person for a fix.
    """
    verdict = check_budget(user(), ledger=UnreachableLedger(), cap=CAP, estimate=0.10)
    assert verdict.reason == "ledger_unavailable"
    assert verdict.reason != "at_cap"


def test_an_unreachable_ledger_refuses_dev_too():
    """`dev` is exempt from the *cap*, not from the ledger being down. The
    exemption is a budget rule, and reading it as "dev bypasses this module"
    would leave the operator spending against a store that is recording
    nothing — the one person who most needs to notice the outage.
    """
    verdict = check_budget(dev(), ledger=UnreachableLedger(), cap=CAP, estimate=0.10)
    assert not verdict.allowed
    assert verdict.reason == "ledger_unavailable"


def test_a_reachable_ledger_allows_a_user_in_credit():
    ledger = InMemoryLedger()
    ledger.record("candidate@example.com", 1.00)
    verdict = check_budget(user(), ledger=ledger, cap=CAP, estimate=0.10)
    assert verdict.allowed


def test_check_budget_reads_the_total_for_THIS_identity():
    """A ledger keyed on the wrong email caps the wrong person and is invisible
    in a single-user test — every assertion passes while everyone shares one
    budget.
    """
    ledger = InMemoryLedger()
    ledger.record("someone-else@example.com", CAP)
    verdict = check_budget(user(), ledger=ledger, cap=CAP, estimate=0.10)
    assert verdict.allowed


# --- R22.5: the guard belongs to the operation ------------------------------


def test_require_within_budget_raises_when_over():
    ledger = InMemoryLedger()
    ledger.record("candidate@example.com", CAP)
    with pytest.raises(OverBudget):
        require_within_budget(user(), ledger=ledger, cap=CAP, estimate=0.10)


def test_require_within_budget_is_silent_when_in_credit():
    require_within_budget(user(), ledger=InMemoryLedger(), cap=CAP, estimate=0.10)


def test_require_within_budget_raises_when_the_ledger_is_down():
    with pytest.raises(OverBudget):
        require_within_budget(
            user(), ledger=UnreachableLedger(), cap=CAP, estimate=0.10
        )


def test_the_refusal_names_the_identity_and_the_reason():
    """The message is what a caller with no UI — `evals/`, a future service —
    will see, so it carries the facts rather than deferring to a page that may
    not exist.
    """
    ledger = InMemoryLedger()
    ledger.record("candidate@example.com", CAP)
    with pytest.raises(OverBudget) as raised:
        require_within_budget(user(), ledger=ledger, cap=CAP, estimate=0.10)
    assert "candidate@example.com" in str(raised.value)


def test_an_anonymous_identity_cannot_spend():
    with pytest.raises(OverBudget):
        require_within_budget(
            ANONYMOUS, ledger=InMemoryLedger(), cap=CAP, estimate=0.10
        )


def test_a_non_identity_cannot_spend():
    """`require_authorized` checks `isinstance` rather than truthiness, and
    this guard follows it: an object that merely happens to have `.role` and
    `.email` attributes is not an `Identity` and must not buy anything.
    """

    class Impostor:
        email = "candidate@example.com"
        role = Role.DEV

    with pytest.raises(OverBudget):
        require_within_budget(
            Impostor(), ledger=InMemoryLedger(), cap=CAP, estimate=0.10
        )


# --- R22.14: the policy module stays pure -----------------------------------


def test_the_module_imports_no_ui_or_database_driver():
    """Same contract as `permissions.py` (R20.4) and `authorization.py`
    (R21.16), extended to the database: if the policy imports a driver, the
    cap stops being testable without one, and R22.15 quietly stops holding.

    An absolute path, deliberately — a relative one silently scans nothing
    when pytest is invoked from anywhere but the repo root, and a test that
    cannot fail is worse than no test.
    """
    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "interview_prep"
        / "spend.py"
    ).read_text()
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    forbidden = {
        "streamlit",
        "langchain",
        "langgraph",
        "openai",
        "psycopg",
        "psycopg2",
        "sqlalchemy",
        "asyncpg",
    }
    assert not (imported & forbidden), imported & forbidden

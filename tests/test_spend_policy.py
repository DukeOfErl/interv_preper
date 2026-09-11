"""Policy assertions `test_spend_cap.py` does not make, and one it cannot.

That file is the specification of the API and is deliberately left alone. This
one holds the edges a fault-seeding pass found undefended — two seeded defects
survived its 29 tests — plus the refusal-wording question its unreachable-case
test raises but does not settle.

The last test in the file is the only one in the suite that spends a network
call: it checks the policy's flat fallback against **live** prices, because a
constant that is only correct for today's `DEFAULT_MODEL` is correct by
coincidence, and the coincidence is one line in `config.py` from ending.
"""

from __future__ import annotations

import os

import pytest

from interview_prep import context
from interview_prep.authorization import ANONYMOUS, Identity
from interview_prep.config import ASSUMED_REPLY_TOKENS, DEFAULT_MODEL, MAX_TOOL_HOPS
from interview_prep.permissions import Role
from interview_prep.pricing import price_of, turn_cost
from interview_prep.spend import (
    UNMEASURED_TURN_USD,
    InMemoryLedger,
    LedgerUnavailable,
    OverBudget,
    check_budget,
    decide,
    require_within_budget,
)

CAP = 5.00


def user(email="candidate@example.com"):
    return Identity(email=email, role=Role.USER)


# --- R22.9: only an exact `Role.DEV` is uncapped ----------------------------
#
# Seeded into a copy of `spend.py`, a branch that uncapped any non-`None`
# non-`Role` value survived all 29 tests in `test_spend_cap.py`, because every
# one of them passes `Role.DEV`, `Role.USER` or `None`. The untested inputs are
# exactly the ones a bug produces: a role name that arrived as a string, a
# `Role` from a reloaded module, a mock. `permissions.resolve_role` exists
# because untrusted values reach the role slot; nothing downstream of it says
# what happens if one gets past.


@pytest.mark.parametrize(
    "role",
    [
        pytest.param("dev", id="role-name-as-a-string"),
        pytest.param("DEV", id="role-name-shouting"),
        pytest.param(True, id="truthy"),
        pytest.param(1, id="an-int-that-equals-nothing"),
        pytest.param(object(), id="an-object-with-no-opinion"),
    ],
)
def test_only_a_real_dev_role_is_uncapped(role):
    """Uncapped is the expensive direction to be wrong in: it is unbounded
    spend on the operator's credit, and it produces no error and no log line.
    """
    verdict = decide(role=role, spent=CAP * 100, cap=CAP, estimate=1.00)
    assert not verdict.allowed


def test_the_real_dev_role_is_still_uncapped():
    """The other direction of the same rule — a guard that refuses everyone
    passes every test above while locking the operator out of their own app.
    """
    assert decide(role=Role.DEV, spent=CAP * 100, cap=CAP, estimate=1.00).allowed


# --- R22.12: fail closed on *any* unreadable store --------------------------


def test_a_ledger_that_raises_something_else_still_fails_closed():
    """The second survivor. Narrowing `except Exception` to `except
    LedgerUnavailable` passed all 29 tests, because every fake ledger in them
    raises exactly `LedgerUnavailable`. The broad catch is deliberate — an
    adapter that lets a raw driver error escape has still told us the one thing
    that matters — and until now nothing stopped the next person from "tidying"
    it into a requirement violation.
    """

    class LeakyAdapter:
        def total(self, email):
            raise RuntimeError("psycopg said something we did not translate")

        def record(self, email, amount):
            raise RuntimeError("likewise")

    verdict = check_budget(user(), ledger=LeakyAdapter(), cap=CAP, estimate=0.10)
    assert not verdict.allowed
    assert verdict.reason == "ledger_unavailable"


def test_the_declared_exception_is_still_the_adapter_s_contract():
    """Lest the test above be read as "the type does not matter": an adapter
    raising `LedgerUnavailable` is the documented path, and must land in the
    same place.
    """

    class DownLedger:
        def total(self, email):
            raise LedgerUnavailable("connection refused")

        def record(self, email, amount):
            raise LedgerUnavailable("connection refused")

    assert (
        check_budget(user(), ledger=DownLedger(), cap=CAP, estimate=0.10).reason
        == "ledger_unavailable"
    )


# --- R22.13: a refusal names the right party --------------------------------


def test_a_caller_with_no_identity_is_not_told_it_overspent():
    """"You have reached the spend limit" is a statement about a budget. A
    caller with no authorized identity has no budget, so saying it is a lie
    about the wrong party — and this is reachable: `require_authorized` fires
    only when a *real* paid client is constructed, so a caller that injects a
    fake reaches the cap check without § 21 having refused it first.
    """
    verdict = check_budget(
        ANONYMOUS, ledger=InMemoryLedger(), cap=CAP, estimate=0.10
    )
    assert verdict.reason == "unauthorized"

    with pytest.raises(OverBudget) as raised:
        require_within_budget(
            ANONYMOUS, ledger=InMemoryLedger(), cap=CAP, estimate=0.10
        )
    message = str(raised.value).lower()
    assert "no authorized identity" in message
    # The at-cap wording, which this refusal must not be mistaken for. (The
    # message may still use the word "limit" — it says one was *not* reached.)
    assert "has been reached" not in message


def test_an_identity_with_no_usable_address_cannot_spend():
    """A `None` or blank email reads as "has spent nothing" on every read — no
    row matches — and is rejected by the primary key on every write. That is
    uncapped in the direction that costs money and silent in the direction that
    would have made it visible. `authorize()` cannot produce one today, which
    is a property of another module and exactly why it is checked here.
    """
    for email in (None, "", "   "):
        verdict = check_budget(
            Identity(email=email, role=Role.USER),
            ledger=InMemoryLedger(),
            cap=CAP,
            estimate=0.10,
        )
        assert not verdict.allowed, email
        assert verdict.reason == "unauthorized", email


# --- the one constant that is not derived from anything ---------------------


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("OPENROUTER_API_KEY"), reason="needs a key to read live prices"
)
def test_the_unmeasured_fallback_still_covers_a_worst_case_turn():
    """R22.7's bound, checked against today's catalog rather than today's guess.

    `UNMEASURED_TURN_USD` is the floor the policy uses when a caller supplies
    no estimate at all. The page no longer relies on it — it prices the first
    turn from the composed prompt and the model's real rate — but `evals/`, a
    script, or a future driver can still reach `decide` without one, and the
    floor has to be worth something when they do.

    The worst turn this app can produce is `MAX_TOOL_HOPS` model calls, each
    re-sending a grown conversation. Priced from the constants rather than from
    a number I measured once, so that changing `DEFAULT_MODEL` — a one-line
    edit in another file — turns this red instead of quietly halving the
    floor's worth. On `openai/gpt-5-mini` the same turn costs about $0.04; on
    `openai/gpt-5` it is five times that.
    """
    # The catalog fetch is `st.cache_data`-memoised per key, and the offline
    # tests in this suite poison it deliberately: they refuse the socket and
    # cache the empty result that produces. Without this clear, whether this
    # test sees a price depends on what ran before it — which made it fail once
    # in a full run and pass alone, the same shape as any other order-dependent
    # test.
    context.fetch_openrouter_models.clear()
    context.fetch_model_endpoints.clear()

    pricing = price_of(DEFAULT_MODEL, os.environ["OPENROUTER_API_KEY"])
    if not pricing.is_known:
        # Skipped rather than failed: a key that cannot reach OpenRouter is a
        # fact about this machine, not about the cap. Visible in the run either
        # way, which is the point — this must never quietly pass on a zero,
        # because a zero price makes the assertion below trivially true.
        pytest.skip(f"no live price for {DEFAULT_MODEL}; cannot check the floor")

    # A worst-case hop: a full grounded prompt in, a long reply out.
    worst_turn = turn_cost(
        pricing, MAX_TOOL_HOPS * 8000, MAX_TOOL_HOPS * ASSUMED_REPLY_TOKENS
    )
    assert UNMEASURED_TURN_USD >= worst_turn, (
        f"a worst-case turn on {DEFAULT_MODEL} costs ${worst_turn:.4f}, more "
        f"than the ${UNMEASURED_TURN_USD:.4f} fallback covers — an unmeasured "
        "turn would be admitted for less than it costs (R22.7)"
    )

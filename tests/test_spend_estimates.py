"""What a paid call is *predicted* to cost, and what it is *recorded* as.

Both halves of R22.3/R22.6 that a mutation run found undefended, and they
failed in the same way: the code ran in the suite without anything asserting
what it produced.

  * Reverting `check_document`'s own estimate to the chat turn's left the whole
    suite green while a 10 MB upload was admitted again — R22.7's $24 hole,
    reopened silently. Coverage did not show it: `_scan_estimate`'s lines *do*
    execute in other tests, because they reach it with `UNCAPPED` and it
    returns at the first line. The lines ran; nothing read the number.
  * Deleting rung two of `call_cost`, and making rung three return zero, both
    survived everything — every other test hands it a response that already
    carries a reported cost, so nothing below rung one had ever run.

Both are the shape they were written to close: a fix in place, a green suite,
and no assertion touching it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from interview_prep.authorization import Identity
from interview_prep.guardrails import JailbreakGuard
from interview_prep.permissions import Role
from interview_prep.pricing import call_cost
from interview_prep.spend import Budget, InMemoryLedger, OverBudget

IDENTITY = Identity(email="candidate@example.com", role=Role.USER)
CAP = 5.00
MODEL = "priced-model"
#: USD per token, round so every expectation below is arithmetic by hand.
PROMPT_PRICE = 0.000001
COMPLETION_PRICE = 0.000002

CATALOG = [
    {
        "id": MODEL,
        "pricing": {"prompt": str(PROMPT_PRICE), "completion": str(COMPLETION_PRICE)},
    }
]


@pytest.fixture
def priced_catalog(monkeypatch):
    monkeypatch.setattr(
        "interview_prep.pricing.fetch_openrouter_models", lambda api_key: CATALOG
    )


@pytest.fixture
def unpriced_catalog(monkeypatch):
    """Both routes empty — what an unreachable OpenRouter looks like."""
    monkeypatch.setattr(
        "interview_prep.pricing.fetch_openrouter_models", lambda api_key: []
    )
    monkeypatch.setattr(
        "interview_prep.pricing.fetch_model_endpoints", lambda model_id, api_key: []
    )


class FakeClient:
    """A classifier that always allows, and reports nothing about cost."""

    def __init__(self):
        self.calls = 0

    def _create(self, **kwargs):
        self.calls += 1
        message = SimpleNamespace(content='{"is_jailbreak": false, "reason": ""}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    @property
    def chat(self):
        return SimpleNamespace(completions=SimpleNamespace(create=self._create))


def guard(spent, instructions="classifier instructions"):
    ledger = InMemoryLedger()
    ledger.record(IDENTITY.email, spent)
    budget = Budget(ledger=ledger, cap=CAP, estimate=0.01)
    return JailbreakGuard(
        api_key="k",
        model=MODEL,
        instructions=instructions,
        client=FakeClient(),
        identity=IDENTITY,
        budget=budget,
    )


# --- R22.7: a scan is admitted against its own size -------------------------


def test_a_small_document_is_admitted_with_a_small_budget(priced_catalog):
    """First, because every assertion below passes against a guard that refuses
    every document, and a resume that cannot be uploaded is the failure a spend
    cap must not produce.
    """
    verdict = guard(spent=4.00).check_document("resume text " * 300)
    assert verdict.allowed


#: ~55 overlapping windows, which at this catalog's prices costs about $0.06 —
#: more than a chat turn and less than a cap. Big enough for the budget to be
#: the deciding factor, small enough that the admitted case really runs.
MEDIUM_DOCUMENT = "a" * 200_000


def test_a_document_is_refused_when_its_scan_would_not_fit(priced_catalog):
    """The estimate is the scan's, so the budget that matters is the one left
    against *that* — not against the next chat prompt, which is what used to
    admit it.
    """
    with pytest.raises(OverBudget):
        guard(spent=4.98).check_document(MEDIUM_DOCUMENT)


def test_the_same_document_is_admitted_with_the_whole_cap(priced_catalog):
    """The other direction of the same rule: the refusal is about this scan
    against this budget, not a size limit in disguise.
    """
    assert guard(spent=0.0).check_document(MEDIUM_DOCUMENT).allowed


def test_a_scan_far_larger_than_a_turn_is_refused_with_a_turn_s_worth_left(
    priced_catalog,
):
    """The measured hole, at the scale it was measured: a 10 MB upload is
    ~2,800 classifier calls, and a dollar of remaining budget does not buy it.
    R22.7 accepts an overshoot of one turn; this is hundreds.
    """
    with pytest.raises(OverBudget):
        guard(spent=4.00).check_document("a" * 10_000_000)


def test_the_estimate_counts_the_instructions_sent_with_every_window(
    priced_catalog,
):
    """Each window carries the classifier's system prompt as well as the
    excerpt, and the guardrail's instructions are the larger half — about 1,700
    tokens against a window's ~1,000. An estimate that counts only excerpts
    prices the scan at a fraction of what it sends, in the one estimate whose
    entire job is to be bigger than a single call.
    """
    windows = ["window one", "window two"]
    lean = guard(spent=0.0, instructions="short")._scan_estimate(windows)
    heavy = guard(spent=0.0, instructions="x" * 8_000)._scan_estimate(windows)
    assert heavy > lean * 10, "the instructions are not being priced"


def test_an_unpriceable_scan_is_refused_rather_than_guessed(unpriced_catalog):
    """With the catalog unreachable every price is zero, and a zero estimate
    fits any budget that is not quite empty: measured at a 20 MB document
    admitted against $0.001 of remaining budget, spending about $2.44.

    Refused rather than floored, and the difference matters here. The policy's
    unmeasured floor is turn-sized — right for a chat turn, which is one turn —
    and a document scan is the one operation whose fan-out is unbounded, so the
    floor would still admit that 20 MB document to anyone holding six cents.
    The size is knowable without a price; only the dollars are not.
    """
    with pytest.raises(OverBudget) as raised:
        guard(spent=0.0).check_document("a" * 200_000)
    assert raised.value.decision.reason == "unpriced"


def test_the_unpriced_refusal_is_not_dressed_up_as_a_spent_budget(
    unpriced_catalog,
):
    """R22.13 again: a user who cannot upload during a pricing outage has not
    overspent, and their ledger is not down. Three refusals, three messages.
    """
    with pytest.raises(OverBudget) as raised:
        guard(spent=0.0).check_document("a" * 200_000)
    message = str(raised.value).lower()
    assert "cannot be estimated" in message
    assert "has been reached" not in message
    assert "ledger" not in message


def test_a_priceable_scan_is_not_refused(priced_catalog):
    """The direction that keeps the rule from being "refuse every upload"."""
    assert guard(spent=0.0).check_document("a" * 200_000).allowed


def test_a_scan_is_not_estimated_when_nothing_is_counting():
    """No budget, no ledger, no catalog lookup — the estimate would be a
    network round trip in service of a check that is a no-op.
    """
    unbudgeted = JailbreakGuard(
        api_key="k", model=MODEL, instructions="i", client=FakeClient()
    )
    assert unbudgeted._scan_estimate(["window"]) is None


def test_a_screened_prompt_is_billed_for_the_instructions_sent_with_it(
    priced_catalog,
):
    """The twin of the scan-estimate omission, on the chat-turn path.

    Reverting `_bill(response, self.instructions, text)` to `_bill(response,
    text)` left the whole suite green: the behaviour was right and nothing
    asserted it. The instructions are in every request and are the larger part
    — ~1,700 tokens against a prompt's ~50 — so pricing only the user's text
    under-counts a guardrail call by about 97%.
    """
    ledger = InMemoryLedger()
    guard_with_long_instructions = JailbreakGuard(
        api_key="k",
        model=MODEL,
        instructions="x" * 8_000,  # 2,000 estimated tokens
        client=FakeClient(),  # reports no usage at all, so rung three decides
        identity=IDENTITY,
        budget=Budget(ledger=ledger, cap=CAP, estimate=0.01),
    )
    guard_with_long_instructions.check("hi")

    # 2,000 instruction tokens + 1 for the prompt, at this catalog's rate.
    assert ledger.total(IDENTITY.email) == pytest.approx(2001 * PROMPT_PRICE)


# --- R22.3: the rungs below the reported cost -------------------------------


def response(cost=None, prompt_tokens=None, completion_tokens=None):
    """A completion reporting as much or as little as OpenRouter chose to."""
    if cost is None and prompt_tokens is None:
        usage = None
    else:
        usage = SimpleNamespace(
            cost=cost,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model_extra=None,
        )
    return SimpleNamespace(usage=usage)


def test_rung_one_is_the_cost_the_provider_reported(priced_catalog):
    assert call_cost(response(cost=0.42), MODEL, "k") == pytest.approx(0.42)


def test_rung_two_prices_the_tokens_the_provider_reported(priced_catalog):
    """Deleting this rung survived the whole suite. A response carrying token
    counts but no `cost` — one provider quirk, or one dropped `extra_body` —
    billed exactly zero, silently.
    """
    # 1000 x 0.000001 + 100 x 0.000002
    assert call_cost(
        response(prompt_tokens=1000, completion_tokens=100), MODEL, "k"
    ) == pytest.approx(0.0012)


def test_rung_two_is_preferred_to_the_estimate(priced_catalog):
    """Reported tokens beat counted characters whenever both are available;
    the ladder's order is the requirement, not an implementation detail.
    """
    measured = call_cost(
        response(prompt_tokens=1000, completion_tokens=0), MODEL, "k", texts=("x",)
    )
    assert measured == pytest.approx(0.001)


def test_rung_three_prices_the_text_that_was_sent(priced_catalog):
    """Returning zero here survived the whole suite too. 4,000 characters is
    ~1,000 estimated tokens at this catalog's prompt price.
    """
    assert call_cost(response(), MODEL, "k", texts=("a" * 4000,)) == pytest.approx(
        0.001
    )


def test_rung_three_counts_every_text_it_was_given(priced_catalog):
    """The call sites pass the system instructions as well as the user's text,
    because the instructions are in every request and are usually the larger
    part — for the guardrail, ~1,700 tokens against ~50.
    """
    one = call_cost(response(), MODEL, "k", texts=("a" * 4000,))
    both = call_cost(response(), MODEL, "k", texts=("a" * 4000, "b" * 4000))
    assert both == pytest.approx(one * 2)


def test_an_unpriced_model_bills_zero_rather_than_raising(unpriced_catalog):
    """R22.3's named hole, at the bottom of the ladder: an unpriced model is
    uncapped, and refusing to serve one was rejected as disproportionate.
    """
    assert call_cost(response(prompt_tokens=1000), "nobody-prices-this", "k") == 0.0

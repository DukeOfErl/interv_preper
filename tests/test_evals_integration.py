"""Opt-in end-to-end evaluation of the system prompts via DeepEval.

These make real OpenRouter calls (app-under-test + judge), so they are marked
``integration`` and skipped unless ``OPENROUTER_API_KEY`` is set. To keep cost
and time bounded, the run is limited to a few scenarios and the whole dataset
is evaluated once in a module-scoped fixture, then asserted from several tests.

Run just these with:  ``uv run pytest -m integration -k evals``.
"""

import os

import pytest

from evals import evaluate_prompt
from evals.metrics import METRIC_NAMES

pytestmark = pytest.mark.integration

API_KEY = os.getenv("OPENROUTER_API_KEY")
requires_key = pytest.mark.skipif(
    not API_KEY, reason="OPENROUTER_API_KEY not set; skipping live prompt eval"
)

# A low bar: this is a regression guardrail against a badly broken prompt, not a
# quality gate. Raise it once you trust the numbers on your models.
MIN_OVERALL = 0.5
SMOKE_LIMIT = 3


@pytest.fixture(scope="module")
def report():
    return evaluate_prompt(api_key=API_KEY, limit=SMOKE_LIMIT)


@requires_key
def test_all_metrics_ran_without_errors(report):
    errored = [
        (r.case_id, name, m.error)
        for r in report.results
        for name, m in r.metrics.items()
        if m.error
    ]
    assert not errored, f"metric errors during eval: {errored}"


@requires_key
def test_every_metric_was_measured(report):
    for r in report.results:
        assert set(r.metrics) == set(METRIC_NAMES), (
            f"Q{r.case_id} missing metrics: {set(METRIC_NAMES) - set(r.metrics)}"
        )


@requires_key
def test_overall_mean_above_floor(report):
    overall = report.overall_mean()
    assert overall is not None, "no scores were produced"
    assert overall >= MIN_OVERALL, (
        f"overall mean {overall} below floor {MIN_OVERALL}\n{report.format()}"
    )

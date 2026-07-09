"""Orchestration: generate replies, judge them, and aggregate a report.

:func:`evaluate_prompt` is the one public entry point. It loads the real
composed system prompt (unless one is passed in — which is how you A/B test an
edited prompt), generates an app reply per golden, measures every metric on
each reply, and returns an :class:`EvalReport` you can print or inspect.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from deepeval.test_case import LLMTestCase

from interview_prep.config import load_api_key
from interview_prep.prompts import PromptLibrary

from .dataset import SCENARIOS, build_goldens
from .judge import DEFAULT_APP_MODEL, DEFAULT_JUDGE_MODEL, AppUnderTest, OpenRouterJudge
from .metrics import DEFAULT_THRESHOLD, METRIC_NAMES, build_metrics


@dataclass
class MetricOutcome:
    """One metric's verdict on one reply."""

    name: str
    score: float | None
    reason: str | None
    success: bool
    error: str | None = None


@dataclass
class CaseResult:
    """Everything produced for a single scenario."""

    case_id: int
    category: str
    difficulty: str
    question: str
    response: str
    metrics: dict[str, MetricOutcome] = field(default_factory=dict)


@dataclass
class EvalReport:
    """The full run: per-case results plus convenience aggregates."""

    app_model: str
    judge_model: str
    threshold: float
    results: list[CaseResult]

    @property
    def metric_names(self) -> list[str]:
        # Preserve METRIC_NAMES order, but only for metrics actually run.
        run = {n for r in self.results for n in r.metrics}
        return [n for n in METRIC_NAMES if n in run]

    def mean_scores(self) -> dict[str, float | None]:
        """Mean score per metric across all cases (ignoring errored/None)."""
        means: dict[str, float | None] = {}
        for name in self.metric_names:
            scores = [
                r.metrics[name].score
                for r in self.results
                if name in r.metrics and r.metrics[name].score is not None
            ]
            means[name] = round(sum(scores) / len(scores), 3) if scores else None
        return means

    def overall_mean(self) -> float | None:
        means = [v for v in self.mean_scores().values() if v is not None]
        return round(sum(means) / len(means), 3) if means else None

    def format(self) -> str:
        """Render a human-readable text report."""
        lines: list[str] = []
        lines.append("=" * 72)
        lines.append("Interview-prep prompt evaluation")
        lines.append(f"  app model   : {self.app_model}")
        lines.append(f"  judge model : {self.judge_model}")
        lines.append(f"  threshold   : {self.threshold}")
        lines.append(f"  cases       : {len(self.results)}")
        lines.append("=" * 72)

        for r in self.results:
            lines.append("")
            lines.append(f"[Q{r.case_id}] {r.category} / {r.difficulty}")
            lines.append(f"  Q: {r.question}")
            lines.append(f"  A: {_truncate(r.response, 220)}")
            for name in METRIC_NAMES:
                if name not in r.metrics:
                    continue
                m = r.metrics[name]
                if m.error:
                    lines.append(f"    - {name:<20} ERROR: {m.error}")
                    continue
                mark = "PASS" if m.success else "FAIL"
                score = f"{m.score:.2f}" if m.score is not None else " n/a"
                lines.append(f"    - {name:<20} {score} [{mark}]")
                if m.reason:
                    lines.append(f"        {_truncate(m.reason, 200)}")

        lines.append("")
        lines.append("-" * 72)
        lines.append("Mean score per metric:")
        for name, mean in self.mean_scores().items():
            shown = f"{mean:.3f}" if mean is not None else "n/a"
            lines.append(f"  {name:<22} {shown}")
        overall = self.overall_mean()
        lines.append("-" * 72)
        lines.append(
            f"OVERALL MEAN: {overall:.3f}" if overall is not None else "OVERALL MEAN: n/a"
        )
        lines.append("=" * 72)
        return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _measure(metric, test_case) -> MetricOutcome:
    """Run one metric on one test case, capturing failures per-metric.

    A judge/API failure on one metric shouldn't abort the whole run, so it is
    recorded as an errored outcome and the run continues.
    """
    name = getattr(metric, "__name__", None) or getattr(metric, "name", "metric")
    try:
        metric.measure(test_case)
        return MetricOutcome(
            name=name,
            score=metric.score,
            reason=getattr(metric, "reason", None),
            success=bool(getattr(metric, "success", False)),
        )
    except Exception as exc:  # noqa: BLE001 - surface any judge failure, keep going
        return MetricOutcome(name=name, score=None, reason=None, success=False, error=str(exc))


def evaluate_prompt(
    api_key: str | None = None,
    system_prompt: str | None = None,
    scenarios: list[dict] | None = None,
    metric_names: list[str] | None = None,
    app_model: str = DEFAULT_APP_MODEL,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    threshold: float = DEFAULT_THRESHOLD,
    limit: int | None = None,
    progress=None,
) -> EvalReport:
    """Evaluate a system prompt against the interview-scenario dataset.

    Args:
        api_key: OpenRouter key. Falls back to ``config.load_api_key()``.
        system_prompt: Prompt to test. Defaults to the real composed prompt
            from ``prompts/`` (via :class:`PromptLibrary`). Pass an edited
            prompt here to A/B test a change.
        scenarios: The scenario dicts to run (defaults to the full dataset).
        metric_names: Subset of :data:`METRIC_NAMES` to run; ``None`` runs all.
        app_model / judge_model: OpenRouter model ids for the system-under-test
            and the judge.
        threshold: Pass/fail threshold applied to every metric.
        limit: If set, only the first ``limit`` scenarios run (keeps cost down
            for smoke tests / CI).
        progress: Optional ``callable(done, total, message)`` for UIs/CLIs.

    Returns:
        An :class:`EvalReport`.
    """
    api_key = api_key or load_api_key()
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Add it to a .env file (see .env.example) "
            "or export it in your environment."
        )

    if system_prompt is None:
        library = PromptLibrary.load()
        if library.is_empty:
            raise RuntimeError("No markdown prompt files found to evaluate in prompts/.")
        system_prompt = library.system_prompt

    scenarios = SCENARIOS if scenarios is None else scenarios
    if limit is not None:
        scenarios = scenarios[:limit]

    goldens = build_goldens(scenarios)
    app = AppUnderTest(api_key=api_key, model=app_model)
    judge = OpenRouterJudge(api_key=api_key, model=judge_model)

    metrics = build_metrics(judge, system_prompt, threshold=threshold)
    selected = metric_names or METRIC_NAMES
    unknown = [n for n in selected if n not in metrics]
    if unknown:
        raise ValueError(f"Unknown metric(s): {unknown}. Choose from {METRIC_NAMES}.")

    results: list[CaseResult] = []
    total = len(goldens)
    for i, golden in enumerate(goldens, start=1):
        meta = golden.additional_metadata or {}
        if progress:
            progress(i, total, f"Q{meta.get('id', i)} ({meta.get('category', '?')})")

        response = app.reply(system_prompt, golden.input)
        test_case = LLMTestCase(
            input=golden.input,
            actual_output=response,
            context=list(golden.context or []),
        )

        case = CaseResult(
            case_id=meta.get("id", i),
            category=meta.get("category", "?"),
            difficulty=meta.get("difficulty", "?"),
            question=golden.input,
            response=response,
        )
        for name in selected:
            case.metrics[name] = _measure(metrics[name], test_case)
        results.append(case)

    return EvalReport(
        app_model=app_model,
        judge_model=judge_model,
        threshold=threshold,
        results=results,
    )

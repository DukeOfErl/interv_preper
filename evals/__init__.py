"""Prompt-evaluation harness for the interview-prep system prompts.

This package is a standalone evaluation tool, **not** part of the chatbot: the
Streamlit app never imports it. It loads the real composed system prompt (the
markdown files in ``prompts/``, via ``interview_prep.prompts.PromptLibrary``),
runs it against a dataset of interview scenarios, and judges the responses with
`DeepEval <https://deepeval.com>`_ metrics (LLM-as-a-judge).

Run it as a script::

    uv run python -m evals

or drive it programmatically::

    from evals import evaluate_prompt
    report = evaluate_prompt(api_key)
    print(report.format())

Because it only reads the prompt files, editing interviewer behavior (the
markdown) and re-running this harness tells you whether a prompt change helped.
"""

from __future__ import annotations

import os

# Keep DeepEval quiet and offline-friendly: opt out of telemetry / error
# reporting before the framework is imported anywhere in this package. These
# only affect this process and never touch the chatbot.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("ERROR_REPORTING", "NO")
os.environ.setdefault("DEEPEVAL_DISABLE_PROGRESS_BAR", "YES")

from .runner import CaseResult, EvalReport, MetricOutcome, evaluate_prompt  # noqa: E402

__all__ = [
    "evaluate_prompt",
    "EvalReport",
    "CaseResult",
    "MetricOutcome",
]

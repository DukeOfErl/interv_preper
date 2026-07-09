"""Command-line entry point:  ``uv run python -m evals``.

Evaluates the current composed system prompt (the markdown in ``prompts/``)
against the interview-scenario dataset and prints a scored report. Exits
non-zero if the overall mean falls below ``--fail-under`` (useful in CI).

By default it evaluates the exact prompt set the chatbot uses; pass
``--prompt-files`` to evaluate an arbitrary set of markdown files from
``prompts/`` instead (e.g. a single experimental prompt).
"""

from __future__ import annotations

import argparse
import sys

from interview_prep.prompts import PromptLibrary

from .judge import DEFAULT_APP_MODEL, DEFAULT_JUDGE_MODEL
from .metrics import DEFAULT_THRESHOLD, METRIC_NAMES
from .runner import evaluate_prompt


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m evals",
        description="Evaluate the interview-prep system prompts with DeepEval.",
    )
    p.add_argument("--app-model", default=DEFAULT_APP_MODEL, help="OpenRouter model under test.")
    p.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL, help="OpenRouter judge model.")
    p.add_argument(
        "--metrics",
        nargs="+",
        choices=METRIC_NAMES,
        default=None,
        help="Subset of metrics to run (default: all).",
    )
    p.add_argument(
        "--prompt-files",
        nargs="+",
        default=None,
        metavar="FILE",
        help=(
            "Markdown file name(s) in prompts/ to compose and evaluate instead of "
            "the default set the chatbot uses (e.g. --prompt-files simple_interviewer.md). "
            "Order matters; files are concatenated."
        ),
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="Per-metric pass/fail threshold (0-1).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only run the first N scenarios (cheaper smoke run).",
    )
    p.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="Exit non-zero if the overall mean score is below this value.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    def progress(done: int, total: int, message: str) -> None:
        print(f"[{done}/{total}] {message}", file=sys.stderr)

    # Default (None) evaluates the same composed prompt the chatbot uses; a
    # --prompt-files override composes an arbitrary file set from prompts/.
    system_prompt = None
    if args.prompt_files:
        library = PromptLibrary.load(file_names=args.prompt_files)
        if library.is_empty:
            missing = [f.name for f in library.files if not f.exists]
            print(
                f"error: no usable prompt content in {args.prompt_files} "
                f"(missing/empty: {missing or 'all blank'})",
                file=sys.stderr,
            )
            return 2
        system_prompt = library.system_prompt

    try:
        report = evaluate_prompt(
            system_prompt=system_prompt,
            metric_names=args.metrics,
            app_model=args.app_model,
            judge_model=args.judge_model,
            threshold=args.threshold,
            limit=args.limit,
            progress=progress,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(report.format())

    if args.fail_under is not None:
        overall = report.overall_mean()
        if overall is None or overall < args.fail_under:
            print(
                f"\nFAIL: overall mean {overall} < --fail-under {args.fail_under}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

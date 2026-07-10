"""Pre-send guardrail: screen user prompts for jailbreak / injection attempts.

A small, fast classifier (``GUARDRAIL_MODEL``) inspects each user prompt before
it reaches the interviewer LLM. Its instructions live in ``prompts/guardrail.md``
(consistent with the rest of the app's behavior-in-markdown design).

Scope is deliberately narrow: **only** jailbreak / prompt-injection detection.
Off-topic handling is left to the interviewer. The check **fails open** — any
classifier error, timeout, or malformed response allows the prompt through, so a
guardrail outage can never brick the app.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from openai import OpenAI

from .config import (
    GUARDRAIL_MODEL,
    GUARDRAIL_PROMPT_FILE,
    OPENROUTER_BASE_URL,
    PROMPTS_DIR,
)


@dataclass(frozen=True)
class GuardrailResult:
    """Verdict for a single user prompt."""

    allowed: bool
    reason: str = ""
    errored: bool = False  # True when the classifier failed and we failed open


def load_guardrail_prompt(prompt_dir=PROMPTS_DIR, file_name=GUARDRAIL_PROMPT_FILE):
    """Read the classifier's instructions from the markdown prompt file."""
    path = prompt_dir / file_name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


class JailbreakGuard:
    """One-shot jailbreak/injection classifier over the OpenRouter API."""

    def __init__(
        self,
        api_key,
        model=GUARDRAIL_MODEL,
        instructions=None,
        base_url=OPENROUTER_BASE_URL,
        client=None,
    ):
        self.model = model
        self.instructions = (
            instructions if instructions is not None else load_guardrail_prompt()
        )
        # Allow an injected client (tests); otherwise build the real one.
        self._client = client or OpenAI(base_url=base_url, api_key=api_key)

    def check(self, user_text) -> GuardrailResult:
        """Classify ``user_text``; allow on anything that isn't a clear jailbreak.

        Fails open: on any exception or unparseable response, returns an allowed
        result flagged with ``errored=True``.
        """
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.instructions},
                    {"role": "user", "content": user_text},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            verdict = json.loads(content)
        except Exception:
            return GuardrailResult(allowed=True, errored=True)

        is_jailbreak = bool(verdict.get("is_jailbreak", False))
        reason = str(verdict.get("reason", "")).strip()
        return GuardrailResult(allowed=not is_jailbreak, reason=reason)

"""Rewrite a follow-up message into a standalone retrieval query.

On a follow-up turn a message like "tell me more about that" is a poor vector
query — the referent lives in the conversation, not the words. A small, fast
model rewrites it into a self-contained query before retrieval. Its instructions
live in ``prompts/query_rewrite.ignore.md`` (behavior-in-markdown, like the
guardrail classifier).

The rewrite **fails open**: any error, or an empty/blank response, returns the
original message so retrieval still runs and the turn is never blocked.
"""

from __future__ import annotations

from dataclasses import dataclass

from openai import OpenAI

from .config import (
    OPENROUTER_BASE_URL,
    PROMPTS_DIR,
    QUERY_REWRITE_MODEL,
    QUERY_REWRITE_PROMPT_FILE,
)
from .privacy import require_privacy_extra_body


@dataclass(frozen=True)
class QueryRewrite:
    """Result of a condensation attempt.

    ``errored`` is True when the rewrite failed and we fell open to the raw
    message (so the caller can warn the user transparently). ``query`` is always
    usable — the rewritten query on success, or the original question on failure.
    """

    query: str
    errored: bool = False


def load_query_rewrite_prompt(prompt_dir=PROMPTS_DIR, file_name=QUERY_REWRITE_PROMPT_FILE):
    """Read the rewriter's instructions from the markdown prompt file."""
    path = prompt_dir / file_name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _format_history(history) -> str:
    """Render recent messages as ``Role: content`` lines for the rewrite prompt."""
    lines = []
    for message in history:
        role = message.get("role", "").capitalize() or "User"
        content = message.get("content", "")
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


class QueryCondenser:
    """One-shot follow-up → standalone search-query rewriter over OpenRouter."""

    def __init__(
        self,
        api_key,
        model=QUERY_REWRITE_MODEL,
        instructions=None,
        base_url=OPENROUTER_BASE_URL,
        client=None,
    ):
        self.model = model
        self.instructions = (
            instructions if instructions is not None else load_query_rewrite_prompt()
        )
        self.base_url = base_url
        # Fail closed before the client exists — this call ships the user's
        # message and recent history.
        self.privacy_extra_body = require_privacy_extra_body(base_url)
        # Allow an injected client (tests); otherwise build the real one.
        self._client = client or OpenAI(base_url=base_url, api_key=api_key)

    def condense(self, question, history) -> QueryRewrite:
        """Rewrite ``question`` into a standalone query using ``history`` context.

        Fails open: on any exception or an empty/blank response, returns the
        original ``question`` flagged with ``errored=True`` so the caller can
        surface a warning while retrieval still proceeds on the raw text.
        """
        conversation = _format_history(history)
        user_content = (
            f"Conversation so far:\n{conversation}\n\n"
            f"Latest message:\n{question}"
            if conversation
            else question
        )
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.instructions},
                    {"role": "user", "content": user_content},
                ],
                extra_body=dict(self.privacy_extra_body),
            )
            rewritten = (response.choices[0].message.content or "").strip()
        except Exception:
            return QueryRewrite(question, errored=True)
        if not rewritten:
            return QueryRewrite(question, errored=True)
        return QueryRewrite(rewritten, errored=False)

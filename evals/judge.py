"""LLM wiring for the eval harness: the DeepEval judge and the app-under-test.

Two OpenRouter (OpenAI-compatible) callers live here:

* :class:`OpenRouterJudge` — a :class:`DeepEvalBaseLLM` subclass so DeepEval
  metrics can use OpenRouter as the judge model. DeepEval otherwise calls
  OpenAI directly; this is the recommended pattern for any OpenAI-compatible
  provider (see the sprint reference notebooks).
* :class:`AppUnderTest` — a thin, non-streaming caller that plays the role of
  the chatbot: system prompt + one user message in, reply string out. It
  mirrors what ``chat_bot.py`` sends (the composed prompt verbatim), minus the
  streaming/typewriter concerns that are irrelevant to evaluation.
"""

from __future__ import annotations

from openai import OpenAI

from interview_prep.config import OPENROUTER_BASE_URL
from interview_prep.privacy import require_privacy_extra_body
from deepeval.models.base_model import DeepEvalBaseLLM

# The judge should be a capable, cheap instruction-follower; the app-under-test
# defaults to the same family the chatbot targets. Both are OpenRouter model
# ids (the ``openai/`` prefix is required by OpenRouter).
DEFAULT_JUDGE_MODEL = "openai/gpt-4.1-mini"
DEFAULT_APP_MODEL = "openai/gpt-5-mini"


class OpenRouterJudge(DeepEvalBaseLLM):
    """Adapter that lets DeepEval metrics judge via OpenRouter.

    DeepEval may pass a ``schema`` (a Pydantic model) requesting structured
    output. We ignore it and return the raw text: DeepEval falls back to
    parsing JSON out of the string, which is the documented behavior for
    providers without native structured-output support.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_JUDGE_MODEL,
        base_url: str = OPENROUTER_BASE_URL,
    ):
        self._model = model
        self.base_url = base_url
        # Fail closed before the client exists (see interview_prep.privacy).
        self.privacy_extra_body = require_privacy_extra_body(base_url)
        self._client = OpenAI(base_url=base_url, api_key=api_key)

    def load_model(self):
        return self._client

    def generate(self, prompt: str, *args, **kwargs) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            extra_body=dict(self.privacy_extra_body),
        )
        return response.choices[0].message.content or ""

    async def a_generate(self, prompt: str, *args, **kwargs) -> str:
        # The async path falls back to sync; the harness runs metrics serially.
        return self.generate(prompt, *args, **kwargs)

    def get_model_name(self) -> str:
        return self._model


class AppUnderTest:
    """The system-under-test: the interview chatbot, reduced to one turn.

    Sends ``system_prompt`` + a single user message and returns the reply. The
    system prompt is passed verbatim (placeholders such as ``{target_role}``
    intact) exactly as ``chat_bot.py`` sends it, so we evaluate the prompt as it
    actually behaves in the app.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_APP_MODEL,
        base_url: str = OPENROUTER_BASE_URL,
    ):
        self.model = model
        self.base_url = base_url
        # Fail closed before the client exists (see interview_prep.privacy).
        self.privacy_extra_body = require_privacy_extra_body(base_url)
        self._client = OpenAI(base_url=base_url, api_key=api_key)

    def reply(self, system_prompt: str, user_message: str) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            extra_body=dict(self.privacy_extra_body),
        )
        return response.choices[0].message.content or ""

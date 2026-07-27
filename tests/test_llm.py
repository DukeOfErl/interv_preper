from types import SimpleNamespace

import pytest

from interview_prep import llm
from interview_prep.llm import InterviewLLM
from interview_prep.privacy import PrivacyNotEnsuredError


def _chunk(content):
    """Build a fake streaming chunk with the given delta content."""
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


def _usage_chunk(**usage_fields):
    """Build a final streaming chunk carrying a usage object (no content)."""
    return SimpleNamespace(choices=[], usage=SimpleNamespace(**usage_fields))


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self._chunks)


class _FakeClient:
    def __init__(self, chunks):
        self.chat = SimpleNamespace(completions=_FakeCompletions(chunks))


def _build_llm(monkeypatch, chunks):
    """Construct an InterviewLLM whose OpenAI client is a fake stream source."""
    client = _FakeClient(chunks)
    monkeypatch.setattr(llm, "OpenAI", lambda **kwargs: client)
    instance = InterviewLLM(api_key="key", model="test-model", typing_delay=0)
    return instance, client


def test_stream_reply_yields_only_nonempty_tokens(monkeypatch):
    chunks = [
        _chunk("Hello"),
        _chunk(""),  # empty content -> skipped
        _chunk(None),  # None content -> skipped
        _chunk(" world"),
        SimpleNamespace(choices=[]),  # no choices -> skipped
    ]
    instance, _ = _build_llm(monkeypatch, chunks)

    tokens = list(instance.stream_reply("system", []))
    assert tokens == ["Hello", " world"]


def test_stream_reply_prepends_system_then_history(monkeypatch):
    instance, client = _build_llm(monkeypatch, [_chunk("ok")])
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]

    list(instance.stream_reply("SYS", history))

    (call,) = client.chat.completions.calls
    assert call["model"] == "test-model"
    assert call["stream"] is True
    assert call["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_usage_accounting_requested_without_reasoning_by_default(monkeypatch):
    # Usage accounting is always requested (for the real cost); no reasoning
    # param is added for a non-reasoning model. The provider's privacy params
    # always ride along.
    instance, client = _build_llm(monkeypatch, [_chunk("ok")])
    list(instance.stream_reply("SYS", []))
    (call,) = client.chat.completions.calls
    assert call["extra_body"] == {
        "usage": {"include": True},
        "provider": {"data_collection": "deny"},
    }


def test_reasoning_effort_is_sent_alongside_usage_when_set(monkeypatch):
    client = _FakeClient([_chunk("ok")])
    monkeypatch.setattr(llm, "OpenAI", lambda **kwargs: client)
    instance = InterviewLLM(
        api_key="key", model="test-model", reasoning_effort="high", typing_delay=0
    )

    list(instance.stream_reply("SYS", []))

    (call,) = client.chat.completions.calls
    assert call["extra_body"] == {
        "usage": {"include": True},
        "provider": {"data_collection": "deny"},
        "reasoning": {"effort": "high"},
    }


def test_privacy_params_are_per_call_not_shared_state(monkeypatch):
    # extra_body is assembled fresh each call from a copy of the policy's dict.
    # If it were the registry's own dict, the reasoning/usage keys written into
    # it would leak into every later request (and into other call sites).
    instance, client = _build_llm(monkeypatch, [_chunk("ok"), _chunk("ok")])
    list(instance.stream_reply("SYS", []))
    list(instance.stream_reply("SYS", []))

    first, second = client.chat.completions.calls
    assert first["extra_body"] == second["extra_body"]
    assert first["extra_body"]["provider"] is not second["extra_body"]["provider"]


def test_unknown_provider_cannot_be_constructed(monkeypatch):
    # Fail closed: for a provider whose data-usage policy we can't vouch for,
    # there must be no object capable of sending the conversation. Raising at
    # construction (rather than sending with a warning) is the whole point — a
    # disclosure cannot be taken back once the request is out.
    client = _FakeClient([_chunk("ok")])
    monkeypatch.setattr(llm, "OpenAI", lambda **kwargs: client)

    with pytest.raises(PrivacyNotEnsuredError):
        InterviewLLM(
            api_key="key",
            model="test-model",
            base_url="https://gateway.example/v1",
            typing_delay=0,
        )

    assert client.chat.completions.calls == []


def test_captures_cost_and_reasoning_tokens_from_usage(monkeypatch):
    details = SimpleNamespace(reasoning_tokens=128)
    chunks = [
        _chunk("hi"),
        _usage_chunk(
            prompt_tokens=10,
            completion_tokens=200,
            completion_tokens_details=details,
            cost=0.0123,
        ),
    ]
    instance, _ = _build_llm(monkeypatch, chunks)

    list(instance.stream_reply("system", []))

    assert instance.last_cost == pytest.approx(0.0123)
    assert instance.last_reasoning_tokens == 128
    assert instance.last_usage is not None


def test_missing_cost_and_reasoning_leave_none(monkeypatch):
    # No usage chunk at all -> nothing to extract.
    instance, _ = _build_llm(monkeypatch, [_chunk("hi")])

    list(instance.stream_reply("system", []))

    assert instance.last_cost is None
    assert instance.last_reasoning_tokens is None


def test_stream_reply_propagates_client_errors(monkeypatch):
    # chat_bot.main() relies on this: the API error must surface so the entry
    # point can catch it and show a friendly warning (e.g. a rejected key)
    # instead of stream_reply swallowing it.
    class _Boom:
        def create(self, **kwargs):
            raise RuntimeError("api down")

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Boom()))
    monkeypatch.setattr(llm, "OpenAI", lambda **kwargs: client)
    instance = InterviewLLM(api_key="k", model="m", typing_delay=0)

    with pytest.raises(RuntimeError):
        list(instance.stream_reply("sys", [{"role": "user", "content": "hi"}]))

from types import SimpleNamespace

import pytest

from interview_prep.web_research import (
    Citation,
    WebResearcher,
    _validate_links,
)


def annotation(url, title="", content=""):
    return SimpleNamespace(
        type="url_citation",
        url_citation=SimpleNamespace(url=url, title=title, content=content),
    )


class FakeCompletions:
    """Stand-in for client.chat.completions with a scripted response."""

    def __init__(self, content=None, annotations=None, cost=None, exc=None):
        self._content = content
        self._annotations = annotations
        self._cost = cost
        self._exc = exc
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        message = SimpleNamespace(
            content=self._content, annotations=self._annotations
        )
        usage = SimpleNamespace(cost=self._cost, model_extra=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)], usage=usage
        )


def make_researcher(**kwargs):
    completions = FakeCompletions(**kwargs)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    researcher = WebResearcher(
        api_key="key", instructions="extract facts", client=client
    )
    return researcher, completions


def test_returns_bullets_citations_raw_text_and_cost():
    researcher, completions = make_researcher(
        content=(
            "- Acme raised $40M. [acme.com](https://acme.com/news)\n"
            "- Acme employs 200 people. [techsite.com](https://techsite.com/acme)"
        ),
        annotations=[
            annotation("https://acme.com/news", "Acme news", "Acme raised $40M..."),
            annotation("https://techsite.com/acme", "Profile", "200 employees..."),
        ],
        cost=0.007,
    )
    result = researcher.research("Acme Corp funding")

    assert "$40M" in result.bullets
    assert [c.url for c in result.citations] == [
        "https://acme.com/news",
        "https://techsite.com/acme",
    ]
    # Raw text is the verbatim excerpts under per-source headers (tier two).
    assert "## Acme news" in result.raw_text
    assert "Source: https://acme.com/news" in result.raw_text
    assert "Acme raised $40M..." in result.raw_text
    assert result.cost == pytest.approx(0.007)
    assert not result.errored


def test_request_carries_web_plugin_and_usage_accounting():
    researcher, completions = make_researcher(
        content="- fact. [a.com](https://a.com)",
        annotations=[annotation("https://a.com")],
    )
    researcher.research("query")
    body = completions.calls[0]["extra_body"]
    (plugin,) = body["plugins"]
    assert plugin["id"] == "web"
    assert plugin["engine"] == researcher.engine
    assert plugin["max_results"] == researcher.max_results
    assert body["usage"] == {"include": True}
    # Non-streaming by design — the result is machine-consumed, not displayed.
    assert "stream" not in completions.calls[0]


def test_unknown_links_are_stripped_but_annotation_links_kept():
    bullets = (
        "- Real fact. [good.com](https://good.com/page \"excerpt\")\n"
        "- Sneaky fact. [evil.com](https://evil.example/exfil)"
    )
    citations = [Citation(url="https://good.com/page")]
    validated = _validate_links(bullets, citations)
    assert "https://good.com/page" in validated
    assert "https://evil.example/exfil" not in validated
    # The stripped link's text survives; only the URL is removed.
    assert "evil.com" in validated


def test_missing_annotations_yield_empty_citations_and_raw_text():
    researcher, _ = make_researcher(content="- a fact with no sources")
    result = researcher.research("query")
    assert result.citations == []
    assert result.raw_text == ""
    assert not result.errored


def test_api_exception_fails_soft_with_reason():
    researcher, _ = make_researcher(exc=RuntimeError("api down"))
    result = researcher.research("query")
    assert result.errored
    assert result.bullets == "" and result.raw_text == ""
    # The reason is captured for the warnings log — a schema change or outage
    # should be diagnosable there, not swallowed.
    assert result.error_reason == "RuntimeError: api down"


def test_empty_reply_fails_soft():
    researcher, _ = make_researcher(content="   ")
    result = researcher.research("query")
    assert result.errored
    assert result.error_reason == "the model returned an empty reply"


def test_dict_shaped_annotations_are_parsed():
    """Providers vary; annotations may arrive as plain dicts."""
    researcher, completions = make_researcher(
        content="- fact. [a.com](https://a.com)",
        annotations=[
            {"type": "url_citation",
             "url_citation": {"url": "https://a.com", "title": "T", "content": "C"}}
        ],
    )
    result = researcher.research("query")
    assert result.citations == [Citation(url="https://a.com", title="T", content="C")]

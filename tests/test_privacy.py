from interview_prep.config import (
    ANTHROPIC_BASE_URL,
    OPENAI_BASE_URL,
    OPENROUTER_BASE_URL,
    PRIVACY_POLICY_CHECKED_DATE,
)
import pytest

from interview_prep.privacy import (
    PrivacyLevel,
    PrivacyNotEnsuredError,
    policy_for,
    privacy_extra_body,
    require_embedding_privacy_extra_body,
    require_privacy_extra_body,
    status_label,
)


def test_enforced_policy_is_keyed_by_a_real_openrouter_endpoint():
    """Guard against a silent overclaim.

    ``PROVIDER_POLICIES`` is keyed by ``OPENROUTER_BASE_URL`` and the app looks
    up with the same constant, so pointing that constant at a dummy URL (a
    tempting way to eyeball the "not ensured" state) renames the key and the
    lookup together: the UI keeps saying privacy is enforced while every request
    fails. Pin the key to a real OpenRouter endpoint so that mistake breaks the
    suite instead of shipping a false claim. To exercise the UNKNOWN path, drop
    the registry entry, not the constant.
    """
    assert "openrouter.ai" in OPENROUTER_BASE_URL
    assert policy_for(OPENROUTER_BASE_URL).level == PrivacyLevel.ENFORCED


def test_openrouter_privacy_is_enforced_by_request_param():
    policy = policy_for(OPENROUTER_BASE_URL)
    assert policy.level == PrivacyLevel.ENFORCED
    assert policy.extra_body == {"provider": {"data_collection": "deny"}}


def test_direct_providers_are_stated_not_enforced():
    # Neither API exposes a "don't train on this" request parameter, so the
    # posture rests on their published terms. Claiming otherwise would be a lie.
    for base_url in (OPENAI_BASE_URL, ANTHROPIC_BASE_URL):
        assert policy_for(base_url).level == PrivacyLevel.PROVIDER_STATED


def test_unknown_provider_is_flagged_not_assumed_safe():
    policy = policy_for("https://gateway.example/v1")
    assert policy.level == PrivacyLevel.UNKNOWN
    assert not policy.is_ensured
    assert policy.extra_body == {}


def test_status_labels_fit_one_sidebar_line():
    labels = [
        status_label(policy_for(OPENROUTER_BASE_URL)),
        status_label(policy_for(OPENAI_BASE_URL)),
        status_label(policy_for("https://gateway.example/v1")),
    ]
    assert labels == [
        "No data-training risk",
        f"Privacy provider-stated on {PRIVACY_POLICY_CHECKED_DATE}",
        "Privacy not ensured",
    ]


def test_privacy_extra_body_returns_a_fresh_copy_each_call():
    # Call sites merge these params into request bodies they also write their own
    # keys into (llm.py adds usage/reasoning). Handing out the registry's own
    # dict would let one request's params leak into every later one.
    first = privacy_extra_body(OPENROUTER_BASE_URL)
    first["provider"]["data_collection"] = "allow"
    first["injected"] = True

    second = privacy_extra_body(OPENROUTER_BASE_URL)
    assert second == {"provider": {"data_collection": "deny"}}


def _render_status_app(base_url):
    """Render only the privacy status widget, for the given provider base URL.

    Streamlit's AppTest needs a script, so this is the smallest one that puts
    ``render_privacy_status`` on screen — the negative states are unreachable in
    the real app until a provider selector exists, and eyeballing them by editing
    config breaks the app (see the guard test above).
    """
    from streamlit.testing.v1 import AppTest

    return AppTest.from_string(
        "from interview_prep.privacy import policy_for\n"
        "from interview_prep.ui import render_privacy_status\n"
        f"render_privacy_status(policy_for({base_url!r}))\n"
    ).run()


def test_enforced_provider_renders_a_green_status():
    app = _render_status_app(OPENROUTER_BASE_URL)
    assert not app.exception
    assert len(app.success) == 1
    assert not app.info and not app.error


def test_stated_provider_renders_a_blue_status_with_the_checked_date():
    app = _render_status_app(OPENAI_BASE_URL)
    assert not app.exception
    assert len(app.info) == 1
    assert PRIVACY_POLICY_CHECKED_DATE in app.info[0].value
    assert not app.success and not app.error


def test_unknown_provider_renders_a_red_status():
    # The negative check: an unrecognized endpoint must render as an error box,
    # never as success or silence. Absence of a warning would read as safety.
    app = _render_status_app("https://gateway.example/v1")
    assert not app.exception
    assert len(app.error) == 1
    assert not app.success and not app.info


def _run_app(monkeypatch, drop_openrouter):
    """Run the real chat_bot.py under AppTest, optionally with privacy unknown.

    Unregisters the OpenRouter *policy* rather than editing ``OPENROUTER_BASE_URL``
    — the constant is both the registry key and the lookup key, so changing it
    renames both and the app goes on claiming privacy (see the guard test above).
    """
    from streamlit.testing.v1 import AppTest

    from interview_prep import privacy

    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-key")
    if drop_openrouter:
        registry = dict(privacy.PROVIDER_POLICIES)
        registry.pop(OPENROUTER_BASE_URL)
        monkeypatch.setattr(privacy, "PROVIDER_POLICIES", registry)
    return AppTest.from_file("chat_bot.py", default_timeout=90).run()


def test_app_refuses_to_render_an_uploader_when_privacy_is_unknown(monkeypatch):
    """The negative check this whole design exists for.

    A red badge is worthless if the resume has already been embedded, so the
    *absent uploader* is the load-bearing assertion: it proves the app stops
    before any egress is possible, not merely that it complains afterwards.
    """
    app = _run_app(monkeypatch, drop_openrouter=True)

    assert not app.exception
    assert len(app.error) == 2  # the red status badge + the blocking explanation
    assert not app.file_uploader  # no way to hand over a document at all
    assert not app.chat_input  # and no way to send a message


def test_app_operates_normally_when_privacy_is_ensured(monkeypatch):
    # Positive control: the gate must not fire in normal operation.
    app = _run_app(monkeypatch, drop_openrouter=False)

    assert not app.exception
    assert not app.error
    assert len(app.success) == 1  # the green status badge
    assert app.file_uploader
    assert app.chat_input


def test_embedding_body_drops_params_the_endpoint_rejects():
    # OpenRouter's provider routing is honored by /embeddings; OpenAI's `store`
    # is a chat-only param and would be rejected, so it must not be forwarded.
    assert require_embedding_privacy_extra_body(OPENROUTER_BASE_URL) == {
        "provider": {"data_collection": "deny"}
    }
    assert "store" in privacy_extra_body(OPENAI_BASE_URL)
    assert require_embedding_privacy_extra_body(OPENAI_BASE_URL) == {}


def test_both_ensured_levels_are_allowed_to_send():
    # The threshold: a request parameter (enforced) and published terms with a
    # verified-on date (provider-stated) both count as a real no-training basis.
    assert require_privacy_extra_body(OPENROUTER_BASE_URL) == {
        "provider": {"data_collection": "deny"}
    }
    assert require_privacy_extra_body(OPENAI_BASE_URL) == {"store": False}


def test_unknown_provider_is_refused_not_merely_reported():
    # The core of ADR-0110. Disclosure after the fact is a post-mortem, so the
    # request path raises instead of sending with a red badge.
    with pytest.raises(PrivacyNotEnsuredError):
        require_privacy_extra_body("https://gateway.example/v1")
    with pytest.raises(PrivacyNotEnsuredError):
        require_embedding_privacy_extra_body("https://gateway.example/v1")


def test_ui_accessors_stay_non_raising():
    # The sidebar must be able to describe a posture the app refuses to send to;
    # if policy_for raised, the blocked run couldn't explain itself.
    policy = policy_for("https://gateway.example/v1")
    assert policy.level == PrivacyLevel.UNKNOWN
    assert privacy_extra_body("https://gateway.example/v1") == {}

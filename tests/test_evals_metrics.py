"""Unit tests for the eval metric helpers (no network / no judge calls)."""

from evals.metrics import extract_behavior_rules


def test_extracts_bullets_under_a_rules_header():
    prompt = (
        "## Core behavior\n"
        "- not a rule bullet\n"
        "\n"
        "### Rules\n"
        "- Ask exactly one question at a time.\n"
        "- Do not dump a list of questions.\n"
    )
    rules = extract_behavior_rules(prompt)
    assert rules == (
        "- Ask exactly one question at a time.\n- Do not dump a list of questions."
    )


def test_header_match_is_case_insensitive():
    prompt = "# HARD RULES\n- never fabricate.\n"
    assert extract_behavior_rules(prompt) == "- never fabricate."


def test_stops_at_the_next_header():
    prompt = (
        "### Rules\n"
        "- rule one.\n"
        "## Feedback\n"
        "- not a rule.\n"
    )
    assert extract_behavior_rules(prompt) == "- rule one."


def test_ignores_non_bullet_prose_inside_the_section():
    prompt = "### Rules\nSome intro prose that is not a bullet.\n- the only rule.\n"
    assert extract_behavior_rules(prompt) == "- the only rule."


def test_concatenates_multiple_rules_sections():
    prompt = (
        "### Interview Rules\n- rule a.\n"
        "## Other\n- ignored.\n"
        "### More rules\n- rule b.\n"
    )
    assert extract_behavior_rules(prompt) == "- rule a.\n- rule b."


def test_falls_back_to_whole_prompt_when_no_rules_header():
    prompt = "## Core behavior\n- be helpful.\n- be concise."
    assert extract_behavior_rules(prompt) == prompt.strip()

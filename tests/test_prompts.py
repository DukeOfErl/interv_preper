from interview_prep.prompts import (
    PromptLibrary,
    default_source,
    discover_sources,
)


def _write(dir_path, name, text):
    (dir_path / name).write_text(text, encoding="utf-8")


def test_load_composes_files_in_order(tmp_path):
    _write(tmp_path, "a.md", "  Alpha  ")
    _write(tmp_path, "b.md", "Beta\n")
    library = PromptLibrary.load(prompt_dir=tmp_path, file_names=["a.md", "b.md"])

    # Contents are stripped and joined with a blank line, in file-name order.
    assert library.system_prompt == "Alpha\n\nBeta"
    assert not library.is_empty
    assert [f.name for f in library.files] == ["a.md", "b.md"]


def test_missing_and_empty_files_are_skipped(tmp_path):
    _write(tmp_path, "present.md", "Content")
    _write(tmp_path, "blank.md", "   \n  ")
    library = PromptLibrary.load(
        prompt_dir=tmp_path, file_names=["present.md", "blank.md", "absent.md"]
    )

    assert library.system_prompt == "Content"
    present, blank, absent = library.files
    assert present.exists and present.content == "Content"
    assert blank.exists and blank.content == ""
    assert not absent.exists and absent.content == ""


def test_is_empty_when_no_content(tmp_path):
    library = PromptLibrary.load(prompt_dir=tmp_path, file_names=["nope.md"])
    assert library.is_empty
    assert library.system_prompt == ""


def test_is_grounding_aware_detects_placeholder(tmp_path):
    _write(tmp_path, "grounded.md", "Use these:\n\n{retrieved_context}")
    library = PromptLibrary.load(prompt_dir=tmp_path, file_names=["grounded.md"])
    assert library.is_grounding_aware


def test_is_grounding_aware_false_for_other_placeholders(tmp_path):
    # Other {placeholders} are the model's to fill; only {retrieved_context}
    # opts a source into document grounding.
    _write(tmp_path, "plain.md", "Role: {target_role}")
    library = PromptLibrary.load(prompt_dir=tmp_path, file_names=["plain.md"])
    assert not library.is_grounding_aware


def test_preview_truncates_to_max_lines(tmp_path):
    _write(tmp_path, "long.md", "\n".join(f"line{i}" for i in range(20)))
    library = PromptLibrary.load(prompt_dir=tmp_path, file_names=["long.md"])
    (prompt_file,) = library.files

    preview = library.preview(prompt_file, max_lines=3)
    assert preview == "line0\nline1\nline2"


def test_discover_finds_files_and_dirs_and_hides_ignored(tmp_path):
    _write(tmp_path, "simple.md", "Simple")
    _write(tmp_path, "guardrail.ignore.md", "Not a persona")
    sub = tmp_path / "multi"
    sub.mkdir()
    _write(sub, "10_a.md", "A")
    _write(sub, "20_b.md", "B")

    sources = {s.key: s for s in discover_sources(tmp_path)}

    # The ignored file never becomes a source.
    assert "guardrail.ignore.md" not in sources
    assert set(sources) == {"simple.md", "multi"}
    assert sources["simple.md"].is_directory is False
    assert sources["multi"].is_directory is True


def test_directory_source_orders_by_numeric_prefix(tmp_path):
    sub = tmp_path / "multi"
    sub.mkdir()
    # Written out of order and non-alphabetical to prove numeric sorting wins.
    _write(sub, "20_second.md", "Second")
    _write(sub, "10_first.md", "First")
    _write(sub, "15_middle.md", "Middle")  # gap-numbered insertion
    _write(sub, "noprefix.md", "Last")  # unnumbered sorts last

    (source,) = discover_sources(tmp_path)
    library = PromptLibrary.from_source(source)

    assert [f.name for f in library.files] == [
        "10_first.md",
        "15_middle.md",
        "20_second.md",
        "noprefix.md",
    ]
    assert library.system_prompt == "First\n\nMiddle\n\nSecond\n\nLast"


def test_ignored_file_inside_directory_is_skipped(tmp_path):
    sub = tmp_path / "multi"
    sub.mkdir()
    _write(sub, "10_a.md", "A")
    _write(sub, "20_b.ignore.md", "hidden")

    (source,) = discover_sources(tmp_path)
    assert [p.name for p in source.file_paths] == ["10_a.md"]


def test_default_source_prefers_configured_key(tmp_path, monkeypatch):
    monkeypatch.setattr("interview_prep.prompts.DEFAULT_PROMPT_SOURCE", "multi")
    _write(tmp_path, "aaa.md", "A")  # sorts first alphabetically
    sub = tmp_path / "multi"
    sub.mkdir()
    _write(sub, "10_a.md", "A")

    sources = discover_sources(tmp_path)
    assert default_source(sources).key == "multi"


def test_default_source_falls_back_to_first(tmp_path, monkeypatch):
    monkeypatch.setattr("interview_prep.prompts.DEFAULT_PROMPT_SOURCE", "absent")
    _write(tmp_path, "aaa.md", "A")
    _write(tmp_path, "bbb.md", "B")

    sources = discover_sources(tmp_path)
    assert default_source(sources).key == "aaa.md"
    assert default_source([]) is None

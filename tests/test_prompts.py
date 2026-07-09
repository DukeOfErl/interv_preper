from interview_prep.prompts import PromptLibrary


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


def test_preview_truncates_to_max_lines(tmp_path):
    _write(tmp_path, "long.md", "\n".join(f"line{i}" for i in range(20)))
    library = PromptLibrary.load(prompt_dir=tmp_path, file_names=["long.md"])
    (prompt_file,) = library.files

    preview = library.preview(prompt_file, max_lines=3)
    assert preview == "line0\nline1\nline2"

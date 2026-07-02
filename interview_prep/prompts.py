"""Loading and composing the markdown system prompt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import PROMPT_FILE_NAMES, PROMPTS_DIR


@dataclass(frozen=True)
class PromptFile:
    """A single markdown prompt file and its (stripped) contents."""

    name: str
    path: Path
    content: str  # empty string if the file is missing or blank

    @property
    def exists(self) -> bool:
        return self.path.exists()


class PromptLibrary:
    """The ordered set of markdown files that make up the system prompt.

    The system prompt is built by concatenating each non-empty file's content
    in ``PROMPT_FILE_NAMES`` order, so editing behavior means editing markdown,
    not Python.
    """

    def __init__(self, files: list[PromptFile]):
        self.files = files

    @classmethod
    def load(cls, prompt_dir: Path = PROMPTS_DIR, file_names=PROMPT_FILE_NAMES):
        files = []
        for name in file_names:
            path = prompt_dir / name
            content = path.read_text(encoding="utf-8").strip() if path.exists() else ""
            files.append(PromptFile(name=name, path=path, content=content))
        return cls(files)

    @property
    def system_prompt(self) -> str:
        return "\n\n".join(f.content for f in self.files if f.content).strip()

    @property
    def is_empty(self) -> bool:
        return not self.system_prompt

    def preview(self, prompt_file: PromptFile, max_lines: int = 8) -> str:
        return "\n".join(prompt_file.content.splitlines()[:max_lines])

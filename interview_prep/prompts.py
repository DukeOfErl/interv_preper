"""Loading and composing the markdown system prompt.

A "prompt source" is what the user picks in the UI. It is either a single
top-level ``.md`` file or a subdirectory of ``.md`` files that get concatenated
into one prompt. Sources are discovered automatically from ``prompts/`` — see
``discover_sources`` and the conventions documented in ``config`` (``IGNORE_TAG``
and numeric filename prefixes).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import (
    DEFAULT_PROMPT_SOURCE,
    IGNORE_TAG,
    PROMPTS_DIR,
    RETRIEVED_CONTEXT_PLACEHOLDER,
)

# Leading run of digits in a filename, used to order files within a subdirectory.
_ORDER_PREFIX = re.compile(r"^(\d+)")


def _is_ignored(path: Path) -> bool:
    """True if ``path`` carries the ignore tag (e.g. ``guardrail.ignore.md``).

    ``Path.stem`` strips only the final ``.md``, so a ``.ignore.md`` file has a
    stem ending in ``IGNORE_TAG``.
    """
    return path.stem.endswith(IGNORE_TAG)


def _is_prompt_md(path: Path) -> bool:
    """True if ``path`` is a selectable markdown prompt file."""
    return path.is_file() and path.suffix == ".md" and not _is_ignored(path)


def _order_key(path: Path) -> tuple[float, str]:
    """Sort key honoring a leading numeric prefix; unnumbered files sort last."""
    match = _ORDER_PREFIX.match(path.name)
    number = int(match.group(1)) if match else float("inf")
    return (number, path.name.lower())


@dataclass(frozen=True)
class PromptFile:
    """A single markdown prompt file and its (stripped) contents."""

    name: str
    path: Path
    content: str  # empty string if the file is missing or blank

    @property
    def exists(self) -> bool:
        return self.path.exists()


@dataclass(frozen=True)
class PromptSource:
    """A selectable prompt: one top-level file or a directory of files."""

    key: str  # stable identifier (filename or directory name)
    label: str  # human-friendly name shown in the selector
    path: Path
    is_directory: bool
    file_paths: list[Path] = field(default_factory=list)  # ordered, for dirs


def discover_sources(prompt_dir: Path = PROMPTS_DIR) -> list[PromptSource]:
    """Enumerate selectable prompt sources under ``prompt_dir``.

    Top-level ``.md`` files (minus ignored ones) become single-file sources;
    subdirectories that contain at least one usable ``.md`` file become
    directory sources whose files are ordered by numeric prefix.
    """
    sources: list[PromptSource] = []
    if not prompt_dir.exists():
        return sources

    for entry in sorted(prompt_dir.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_dir():
            md_files = sorted(
                (f for f in entry.iterdir() if _is_prompt_md(f)), key=_order_key
            )
            if md_files:
                sources.append(
                    PromptSource(
                        key=entry.name,
                        label=entry.name,
                        path=entry,
                        is_directory=True,
                        file_paths=md_files,
                    )
                )
        elif _is_prompt_md(entry):
            sources.append(
                PromptSource(
                    key=entry.name,
                    label=entry.stem,
                    path=entry,
                    is_directory=False,
                    file_paths=[entry],
                )
            )
    return sources


def default_source(sources: list[PromptSource]) -> PromptSource | None:
    """Pick the configured default source, else the first discovered one."""
    for source in sources:
        if source.key == DEFAULT_PROMPT_SOURCE:
            return source
    return sources[0] if sources else None


class PromptLibrary:
    """The ordered set of markdown files that make up the system prompt.

    The system prompt is built by concatenating each non-empty file's content,
    in order, so editing behavior means editing markdown, not Python.
    """

    def __init__(self, files: list[PromptFile], source: PromptSource | None = None):
        self.files = files
        self.source = source

    @classmethod
    def from_source(cls, source: PromptSource) -> "PromptLibrary":
        files = [cls._read(path) for path in source.file_paths]
        return cls(files, source=source)

    @classmethod
    def load(
        cls,
        prompt_dir: Path = PROMPTS_DIR,
        file_names=None,
        source: PromptSource | None = None,
    ) -> "PromptLibrary":
        """Build a library.

        With ``source`` — load that source. With an explicit ``file_names``
        list — compose exactly those files from ``prompt_dir`` (used by the
        eval CLI's ``--prompt-files`` and tests). With neither, load the default
        discovered source.
        """
        if source is not None:
            return cls.from_source(source)
        if file_names is not None:
            files = [cls._read(prompt_dir / name) for name in file_names]
            return cls(files)
        chosen = default_source(discover_sources(prompt_dir))
        if chosen is None:
            return cls([])
        return cls.from_source(chosen)

    @staticmethod
    def _read(path: Path) -> PromptFile:
        content = path.read_text(encoding="utf-8").strip() if path.exists() else ""
        return PromptFile(name=path.name, path=path, content=content)

    @property
    def system_prompt(self) -> str:
        return "\n\n".join(f.content for f in self.files if f.content).strip()

    @property
    def is_empty(self) -> bool:
        return not self.system_prompt

    @property
    def is_grounding_aware(self) -> bool:
        """True if this prompt opts into retrieved-document context.

        A source declares itself grounding-aware by containing the
        ``{retrieved_context}`` placeholder; retrieved chunks are injected only
        there. Sources without it behave exactly as before documents existed.
        """
        return RETRIEVED_CONTEXT_PLACEHOLDER in self.system_prompt

    def preview(self, prompt_file: PromptFile, max_lines: int = 8) -> str:
        return "\n".join(prompt_file.content.splitlines()[:max_lines])

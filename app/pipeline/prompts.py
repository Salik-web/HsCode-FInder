"""Load versioned prompts from prompts/<doc_type>/<version>.md.

A prompt file holds every piece of model-facing wording, so changing any of it means a
new file and a new version (old versions are never edited).
"""

import re
from functools import cache
from pathlib import Path

from pydantic import BaseModel

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
SECTION_HEADING = re.compile(r"^# (.+)$", re.MULTILINE)
VERSION_PATTERN = re.compile(r"^v\d+$")
REQUIRED_SECTIONS = ("System", "User", "Text layer", "No text layer", "Retry")


class PromptNotFoundError(FileNotFoundError):
    """No prompt file for this document type and version."""


class InvalidPromptError(ValueError):
    """A prompt file is missing a section or a placeholder."""


class Prompt(BaseModel):
    version: str
    system: str
    user: str  # contains {{text_layer}}
    text_layer: str  # contains {{text}}
    no_text_layer: str
    retry: str  # contains {{errors}}

    def user_message(self, text_layer: str | None) -> str:
        block = self.no_text_layer
        if text_layer is not None:
            block = self.text_layer.replace("{{text}}", text_layer)
        return self.user.replace("{{text_layer}}", block)

    def retry_message(self, text_layer: str | None, errors: list[str]) -> str:
        retry = self.retry.replace("{{errors}}", "\n".join(f"- {error}" for error in errors))
        return f"{self.user_message(text_layer)}\n\n{retry}"


@cache
def load_prompt(doc_type: str, version: str) -> Prompt:
    if not VERSION_PATTERN.match(version):
        raise PromptNotFoundError(f"prompt versions look like v1, v2, ...; got {version!r}")
    path = PROMPTS_DIR / doc_type / f"{version}.md"
    if not path.is_file():
        raise PromptNotFoundError(f"no prompt at {path}")
    sections = _split_sections(path.read_text(encoding="utf-8"))
    missing = [name for name in REQUIRED_SECTIONS if name not in sections]
    if missing:
        raise InvalidPromptError(f"{path} is missing sections: {missing}")
    prompt = Prompt(
        version=version,
        system=sections["System"],
        user=sections["User"],
        text_layer=sections["Text layer"],
        no_text_layer=sections["No text layer"],
        retry=sections["Retry"],
    )
    _check_placeholders(prompt, path)
    return prompt


def _split_sections(text: str) -> dict[str, str]:
    """Top-level `# Heading` lines split the file; `##` headings stay inside a section."""
    parts = SECTION_HEADING.split(text)
    return {name.strip(): body.strip() for name, body in zip(parts[1::2], parts[2::2], strict=True)}


def _check_placeholders(prompt: Prompt, path: Path) -> None:
    expected = {"user": "{{text_layer}}", "text_layer": "{{text}}", "retry": "{{errors}}"}
    for field, placeholder in expected.items():
        if placeholder not in getattr(prompt, field):
            raise InvalidPromptError(f"{path}: section {field!r} needs {placeholder}")

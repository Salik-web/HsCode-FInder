"""Load versioned prompts from prompts/<task>/<version>.md (an invoice type, or hs_rerank).

A prompt file holds every piece of model-facing wording, so changing any of it means a
new file and a new version (old versions are never edited).
"""

import re
from functools import cache
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
SECTION_HEADING = re.compile(r"^# (.+)$", re.MULTILINE)
VERSION_PATTERN = re.compile(r"^v\d+$")
REQUIRED_SECTIONS = ("System", "User", "Text layer", "No text layer", "Retry")
TASK_SECTIONS = ("System", "User", "Retry")
RERANK_TASK = "hs_rerank"
QUERY_TASK = "hs_query"
# What each HS task fills into its User section.
TASK_PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    RERANK_TASK: ("description", "candidates"),
    QUERY_TASK: ("description",),
}
NEWLINE = chr(10)
OUTPUT_FORMAT_SECTION = "Output format"
# raw: line items as keyed objects (RawInvoice). compact: as fixed-order arrays.
OutputFormat = Literal["raw", "compact"]
# Prompts written before the section existed (v1) produce raw output.
DEFAULT_OUTPUT_FORMAT: OutputFormat = "raw"


class PromptNotFoundError(FileNotFoundError):
    """No prompt file for this document type and version."""


class InvalidPromptError(ValueError):
    """A prompt file is missing a section or a placeholder."""


ALLOWED_KEYS = "{{allowed_keys}}"  # optional in System and Retry; filled from the schema


class Prompt(BaseModel):
    version: str
    system: str  # may contain {{allowed_keys}}
    user: str  # contains {{text_layer}}
    text_layer: str  # contains {{text}}
    no_text_layer: str
    retry: str  # contains {{errors}}; may contain {{allowed_keys}}
    output_format: OutputFormat

    def user_message(self, text_layer: str | None) -> str:
        block = self.no_text_layer
        if text_layer is not None:
            block = self.text_layer.replace("{{text}}", text_layer)
        return self.user.replace("{{text_layer}}", block)

    def system_message(self, allowed_keys: str) -> str:
        return self.system.replace(ALLOWED_KEYS, allowed_keys)

    def retry_message(self, text_layer: str | None, errors: list[str], allowed_keys: str) -> str:
        retry = self.retry.replace("{{errors}}", "\n".join(f"- {error}" for error in errors))
        retry = retry.replace(ALLOWED_KEYS, allowed_keys)
        return f"{self.user_message(text_layer)}\n\n{retry}"


class TaskPrompt(BaseModel):
    """A System / User / Retry prompt whose User section has named {{placeholders}}."""

    task: str
    version: str
    system: str
    user: str
    retry: str  # contains {{errors}}

    def user_message(self, values: dict[str, str]) -> str:
        message = self.user
        for name, value in values.items():
            message = message.replace("{{" + name + "}}", value)
        return message

    def retry_message(self, values: dict[str, str], errors: list[str]) -> str:
        retry = self.retry.replace("{{errors}}", NEWLINE.join(f"- {error}" for error in errors))
        return f"{self.user_message(values)}{NEWLINE}{NEWLINE}{retry}"


@cache
def load_prompt(doc_type: str, version: str) -> Prompt:
    path, sections = _read_sections(doc_type, version, REQUIRED_SECTIONS)
    prompt = Prompt(
        version=version,
        system=sections["System"],
        user=sections["User"],
        text_layer=sections["Text layer"],
        no_text_layer=sections["No text layer"],
        retry=sections["Retry"],
        output_format=_output_format(sections, path),
    )
    _check_placeholders(prompt, path)
    return prompt


@cache
def load_task_prompt(task: str, version: str) -> TaskPrompt:
    """An HS task prompt; its User section must hold every placeholder the task fills."""
    path, sections = _read_sections(task, version, TASK_SECTIONS)
    prompt = TaskPrompt(
        task=task,
        version=version,
        system=sections["System"],
        user=sections["User"],
        retry=sections["Retry"],
    )
    for name in TASK_PLACEHOLDERS[task]:
        if "{{" + name + "}}" not in prompt.user:
            raise InvalidPromptError(f"{path}: section 'User' needs {{{{{name}}}}}")
    if "{{errors}}" not in prompt.retry:
        raise InvalidPromptError(f"{path}: section 'Retry' needs {{{{errors}}}}")
    return prompt


def _read_sections(
    task: str, version: str, required: tuple[str, ...]
) -> tuple[Path, dict[str, str]]:
    if not VERSION_PATTERN.match(version):
        raise PromptNotFoundError(f"prompt versions look like v1, v2, ...; got {version!r}")
    path = PROMPTS_DIR / task / f"{version}.md"
    if not path.is_file():
        raise PromptNotFoundError(f"no prompt at {path}")
    sections = _split_sections(path.read_text(encoding="utf-8"))
    missing = [name for name in required if name not in sections]
    if missing:
        raise InvalidPromptError(f"{path} is missing sections: {missing}")
    return path, sections


def _output_format(sections: dict[str, str], path: Path) -> OutputFormat:
    value = sections.get(OUTPUT_FORMAT_SECTION, DEFAULT_OUTPUT_FORMAT)
    if value not in get_args(OutputFormat):
        raise InvalidPromptError(f"{path}: unknown output format {value!r}")
    return value


def _split_sections(text: str) -> dict[str, str]:
    """Top-level `# Heading` lines split the file; `##` headings stay inside a section."""
    parts = SECTION_HEADING.split(text)
    return {name.strip(): body.strip() for name, body in zip(parts[1::2], parts[2::2], strict=True)}


def _check_placeholders(prompt: Prompt, path: Path) -> None:
    expected = {"user": "{{text_layer}}", "text_layer": "{{text}}", "retry": "{{errors}}"}
    for field, placeholder in expected.items():
        if placeholder not in getattr(prompt, field):
            raise InvalidPromptError(f"{path}: section {field!r} needs {placeholder}")

"""Default prompts and research topics, loaded from files bundled with the package."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources

import yaml
from pydantic import BaseModel, Field

from ..models import DocType

_TEMPLATES = "templates"


def _read(name: str) -> str:
    return (
        resources.files("sec_research_agent.reports")
        .joinpath(_TEMPLATES, name)
        .read_text(encoding="utf-8")
        .strip()
    )


class Topic(BaseModel):
    """A research topic extracted with RAG before the report is written."""

    id: str
    name: str
    stencil_sections: list[str] = Field(default_factory=list)
    queries: list[str]
    doc_types: list[DocType] = Field(default_factory=list)
    top_k: int = Field(default=15, ge=1, le=100)
    all_periods: bool = False
    prompt: str


@dataclass(frozen=True)
class PromptSet:
    """The two user-editable system prompts."""

    report_generation: str
    report_format: str


@cache
def extraction_prompt() -> str:
    """System prompt for per-topic extraction."""
    return _read("extraction.md")


@cache
def report_generation_instructions() -> str:
    """Instructions appended to the report-generation user message."""
    return _read("report_generation_instructions.md")


@cache
def default_prompts() -> PromptSet:
    """Built-in report generation and formatting prompts."""
    return PromptSet(
        report_generation=_read("report_generation.md"),
        report_format=_read("report_format.md"),
    )


@cache
def load_topics() -> tuple[Topic, ...]:
    """Research topics from ``topics.yaml``."""
    data = yaml.safe_load(_read("topics.yaml")) or {}
    return tuple(Topic.model_validate(t) for t in data.get("topics", []))

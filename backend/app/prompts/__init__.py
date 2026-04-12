"""Prompt template system — loads prompts from YAML files.

Usage::

    from app.prompts import load_prompt, load_prompt_builder

    # Simple: load full prompt with all sections
    tpl = load_prompt("outline")
    system_text = tpl.format()

    # Builder: conditionally include sections
    tpl = (
        load_prompt_builder("plan")
        .include("role", "rules", "output_schema")
        .include_if(has_existing_tags, "existing_tags_context")
        .build()
    )
    system_text = tpl.format()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, FrozenSet

import yaml

PROMPTS_DIR = Path(__file__).parent

# ── Core types ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PromptTemplate:
    """A prompt template with declared required variables and optional defaults."""

    template: str
    required_vars: FrozenSet[str] = field(default_factory=frozenset)
    optional_vars: dict[str, Any] = field(default_factory=dict)

    def format(self, **kwargs: Any) -> str:
        missing = self.required_vars - set(kwargs.keys())
        if missing:
            raise ValueError(
                f"Missing required template variables: {missing}. "
                f"Required: {self.required_vars}, got: {set(kwargs.keys())}"
            )
        merged = {**self.optional_vars, **kwargs}
        return self.template.format(**merged)

    def __str__(self) -> str:
        return self.template


@dataclass
class _PromptSection:
    """Internal representation of a single prompt section loaded from YAML."""

    name: str
    template: str
    required_vars: frozenset[str]
    optional_vars: dict[str, Any]


# ── Loading ───────────────────────────────────────────────────────────


def _load_prompt_sections(path: Path) -> tuple[list[str], dict[str, _PromptSection]]:
    """Load a prompt YAML file and return (section_order, sections_by_name)."""
    data = yaml.safe_load(path.read_text())
    section_order: list[str] = data["section_order"]
    sections: dict[str, _PromptSection] = {}
    for name, spec in data["sections"].items():
        sections[name] = _PromptSection(
            name=name,
            template=spec["template"],
            required_vars=frozenset(spec.get("required_vars", [])),
            optional_vars=spec.get("optional_vars", {}),
        )
    unknown = set(section_order) - set(sections)
    if unknown:
        raise ValueError(f"section_order references undefined sections: {unknown}")
    return section_order, sections


class PromptTemplateBuilder:
    """Fluent builder that assembles a PromptTemplate from YAML sections."""

    def __init__(
        self,
        section_order: list[str],
        sections: dict[str, _PromptSection],
    ) -> None:
        self._section_order = section_order
        self._sections = sections
        self._included: set[str] = set()

    @classmethod
    def from_yaml(cls, path: str | Path) -> PromptTemplateBuilder:
        section_order, sections = _load_prompt_sections(Path(path))
        return cls(section_order, sections)

    def include(self, *names: str) -> PromptTemplateBuilder:
        unknown = set(names) - set(self._sections)
        if unknown:
            raise ValueError(f"Unknown sections: {unknown}")
        self._included.update(names)
        return self

    def include_all(self) -> PromptTemplateBuilder:
        self._included = set(self._sections.keys())
        return self

    def include_if(self, condition: bool, *names: str) -> PromptTemplateBuilder:
        if condition:
            return self.include(*names)
        return self

    def build(self) -> PromptTemplate:
        ordered = [s for s in self._section_order if s in self._included]
        if not ordered:
            raise ValueError("No sections included — call .include() before .build()")

        parts: list[str] = []
        all_required: set[str] = set()
        all_optional: dict[str, Any] = {}
        for name in ordered:
            sec = self._sections[name]
            parts.append(sec.template)
            all_required.update(sec.required_vars)
            all_optional.update(sec.optional_vars)

        for var in all_required:
            all_optional.pop(var, None)

        return PromptTemplate(
            template="\n\n".join(parts),
            required_vars=frozenset(all_required),
            optional_vars=all_optional,
        )


# ── Convenience functions ─────────────────────────────────────────────


def load_prompt_builder(name: str) -> PromptTemplateBuilder:
    """Load a prompt YAML file by name and return a builder."""
    path = PROMPTS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return PromptTemplateBuilder.from_yaml(path)


def load_prompt(name: str) -> PromptTemplate:
    """Load a prompt YAML file and return a PromptTemplate with all sections."""
    return load_prompt_builder(name).include_all().build()

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


def _fix_json_latex_escapes(v: str) -> str:
    """Repair LaTeX commands that JSON parsing corrupted into control characters.

    LLMs sometimes write LaTeX without double-escaping the backslash in JSON
    (e.g. ``\\tag`` instead of ``\\\\tag``).  Standard JSON parsers then
    interpret the lone backslash sequences as control characters:

    * ``\\b`` → U+0008 BACKSPACE  — corrupts \\bar, \\begin, \\beta, \\binom …
    * ``\\f`` → U+000C FORM FEED  — corrupts \\frac, \\forall …
    * ``\\t`` → U+0009 TAB        — corrupts \\tag, \\text, \\theta, \\tau …
    * ``\\r`` → U+000D CR         — corrupts \\right, \\rho, \\rm … (not CRLF)

    All four are extremely rare in normal markdown/prose, so replacing them
    before letters is safe.
    """
    v = re.sub(r'\x08([a-zA-Z])', r'\\b\1', v)   # backspace → \b
    v = re.sub(r'\x0c([a-zA-Z])', r'\\f\1', v)   # form feed → \f
    v = re.sub(r'\x09([a-zA-Z])', r'\\t\1', v)   # tab       → \t
    v = re.sub(r'\x0d(?!\n)([a-zA-Z])', r'\\r\1', v)  # bare CR → \r
    return v


class NoteOutput(BaseModel):
    summary: str = Field(
        min_length=50,
        description="2-3 sentence summary that goes in the [!summary] callout at the top of the note",
    )
    overview: str = Field(
        min_length=200,
        description=(
            "## Overview section — context, motivation, where this concept fits in the broader subject. "
            "At least 2-3 paragraphs."
        ),
    )
    main_content: str = Field(
        min_length=400,
        description=(
            "## Main Content section — detailed explanation with ### and #### subsections. "
            "Include [!definition], [!theorem] callouts with ^block-ids and LaTeX math where relevant. "
            "This is the largest section."
        ),
    )
    examples: str = Field(
        min_length=100,
        description=(
            "## Examples section — one or more concrete worked examples using > [!example] callouts. "
            "If no examples are applicable, explain a typical application or use-case instead."
        ),
    )
    connections: str = Field(
        description=(
            "## Connections section — how this concept relates to others, written in prose. "
            "Use [[Note Title]] wiki-links to reference related notes from the provided list."
        )
    )
    see_also: list[str] = Field(
        min_length=1,
        description=(
            "List of related note titles (plain strings, no [[ ]] brackets). "
            "These become the ## See Also bullet list. Include at least 2 entries."
        ),
    )
    aliases: list[str] = Field(
        default_factory=list,
        description=(
            "Alternate names or abbreviations for the concept in this note "
            "(e.g. ['OLS', 'least squares'] for 'Ordinary Least Squares'). "
            "Leave empty if the concept has no common alternate names."
        ),
    )

    @field_validator("summary", "overview", "main_content", "examples", "connections", mode="after")
    @classmethod
    def _fix_latex_escapes(cls, v: str) -> str:
        return _fix_json_latex_escapes(v)

    @field_validator("main_content")
    @classmethod
    def _fix_inline_display_math(cls, v: str) -> str:
        """Expand single-line display math ($$...$$) to multi-line form."""
        def _expand(m: re.Match) -> str:
            inner = m.group(1).strip()
            return f"$$\n{inner}\n$$"
        return re.sub(r"\$\$([^$\n][^\n]*?)\$\$", _expand, v)

    @field_validator("see_also", mode="before")
    @classmethod
    def _strip_brackets(cls, v: object) -> object:
        """Strip accidental [[ ]] that the model sometimes adds."""
        if isinstance(v, list):
            return [s.strip("[] ").replace("[[", "").replace("]]", "") if isinstance(s, str) else s for s in v]
        return v

    def assemble_markdown(self) -> str:
        """Build the full markdown body from the structured sections."""
        see_also_lines = "\n".join(f"- [[{t}]]" for t in self.see_also) if self.see_also else "- (no related notes yet)"
        return (
            f"> [!summary]\n"
            f"> {self.summary}\n\n"
            f"## Overview\n\n"
            f"{self.overview}\n\n"
            f"## Main Content\n\n"
            f"{self.main_content}\n\n"
            f"## Examples\n\n"
            f"{self.examples}\n\n"
            f"## Connections\n\n"
            f"{self.connections}\n\n"
            f"## See Also\n\n"
            f"{see_also_lines}"
        )

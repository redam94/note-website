"""Deterministic quality checks for generated notes.

Lints the rendered markdown body (post-frontmatter) for structural issues the
LLM produces frequently: unbalanced math delimiters, empty callout bodies,
truncated sentences. Flagged notes are routed through the same Phase-2 retry
path and /repair-stubs endpoint as short stubs.
"""
from __future__ import annotations

import re

# ── Fenced code block handling ────────────────────────────────────────

_FENCE = re.compile(r"```[\s\S]*?```", re.MULTILINE)


def _strip_fences(text: str) -> str:
    """Remove fenced code blocks so they don't interfere with math/punctuation checks."""
    return _FENCE.sub("", text)


# ── Individual linters ────────────────────────────────────────────────


def _unbalanced_display_math(text: str) -> str | None:
    """Flag odd number of `$$` delimiters."""
    stripped = _strip_fences(text)
    count = stripped.count("$$")
    if count % 2 == 1:
        return f"unbalanced display math ({count} `$$` delimiters)"
    return None


def _unbalanced_inline_math(text: str) -> str | None:
    """Flag any line with an odd number of single `$` (after removing `$$` pairs)."""
    stripped = _strip_fences(text)
    for lineno, line in enumerate(stripped.split("\n"), 1):
        without_dd = line.replace("$$", "")
        if without_dd.count("$") % 2 == 1:
            snippet = line.strip()[:80]
            return f"unbalanced inline `$` on line {lineno}: {snippet!r}"
    return None


_CALLOUT_HEADER = re.compile(r"^>\s*\[!([a-zA-Z]+)\]\s*(.*)$")
_BLOCK_ID_ONLY = re.compile(r"^>\s*\^[\w-]+\s*$")


def _empty_callout_bodies(text: str) -> str | None:
    """Flag callouts with broken bodies.

    A callout is broken when:
    - It has `>` body lines but they are all empty or block-id only (the classic
      header-then-blank LLM bug).
    - It has neither a title on the header line nor any body lines.

    A header-only callout with an inline title (e.g. `> [!important] Key point.`)
    is treated as valid — the title acts as the content.
    """
    lines = _strip_fences(text).split("\n")
    i = 0
    while i < len(lines):
        m = _CALLOUT_HEADER.match(lines[i])
        if not m:
            i += 1
            continue
        callout_type = m.group(1)
        title = m.group(2).strip()
        i += 1
        body_lines: list[str] = []
        had_body_line = False
        while i < len(lines) and lines[i].lstrip().startswith(">"):
            had_body_line = True
            if _BLOCK_ID_ONLY.match(lines[i]):
                i += 1
                continue
            body = lines[i].lstrip()[1:].strip()
            if body:
                body_lines.append(body)
            i += 1
        joined = " ".join(body_lines).strip()
        if had_body_line and len(joined) < 10:
            return f"callout `[!{callout_type}]` has empty body (got {joined!r})"
        if not had_body_line and not title:
            return f"callout `[!{callout_type}]` has neither title nor body"
    return None


_TERMINATORS = tuple(".!?:;)]\">`*")
_IGNORE_LAST_PARA_PREFIXES = ("#", "- ", "* ", "+ ", "> ", "|", "```")


def _truncated_final_paragraph(text: str) -> str | None:
    """Flag when the very last prose paragraph ends without terminal punctuation.

    Skips list items, headings, tables, and blockquotes — those legitimately
    end without a period.
    """
    stripped = _strip_fences(text).rstrip()
    if not stripped:
        return None
    # Walk backward to the last non-blank line
    last = ""
    for line in reversed(stripped.split("\n")):
        s = line.strip()
        if s:
            last = s
            break
    if not last:
        return None
    if last.startswith(_IGNORE_LAST_PARA_PREFIXES):
        return None
    # Numbered list items like "1. Foo"
    if re.match(r"^\d+[\.\)]\s", last):
        return None
    # If it ends with a terminator, we're good
    if last.endswith(_TERMINATORS):
        return None
    # Otherwise it's suspicious only if it ends in a word character
    if last[-1].isalnum():
        snippet = last[-60:]
        return f"final paragraph ends without terminal punctuation: ...{snippet!r}"
    return None


_LINTERS = (
    _unbalanced_display_math,
    _unbalanced_inline_math,
    _empty_callout_bodies,
    _truncated_final_paragraph,
)


# ── Public API ────────────────────────────────────────────────────────


def lint_issues(body: str) -> list[str]:
    """Return a list of quality issues found in the note body (post-frontmatter).

    Empty list means the note passes the lint checks.
    """
    issues: list[str] = []
    for linter in _LINTERS:
        result = linter(body)
        if result:
            issues.append(result)
    return issues


def first_lint_issue(body: str) -> str | None:
    """Return the first lint issue or None if the body passes."""
    issues = lint_issues(body)
    return issues[0] if issues else None


def strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter from a saved note's full content."""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            return content[end + 3:]
    return content

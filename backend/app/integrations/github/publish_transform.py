"""Wikilink transform for GitHub wiki publish (push direction).

Pure functions — no DB, no IO. Kept independent of the pipeline so the
transform is unit-testable and can be run in dry-run mode cheaply.

Terminology:
- *title*: the note's display title in the app (e.g. "Node.js Streams")
- *page name*: the GitHub wiki page label (after unsafe-char sanitization)
- *filename*: the file on disk in `{repo}.wiki.git` — page name with spaces → hyphens,
  plus `.md` suffix

Wikilink conventions differ between the app and GitHub:
- **App (Obsidian)**: `[[Target|Display]]` — target first, display after the pipe.
  Existing code in `routers/notes.py` renames the target with this ordering.
- **GitHub wiki (Gollum)**: `[[Display|Target]]` — display first, target after.
  Plain `[[Page Name]]` uses one text as both.

This transform reads Obsidian order on input and emits Gollum order on output
whenever an alias is needed.

File-on-disk: `Page-Name.md`. Spaces become hyphens in the filename;
URL-unsafe characters (`#`, `%`, `/`, `\\`, `:`, `*`, `?`, `"`, `<`, `>`, `|`) must not appear.
All pages live at wiki root — flat namespace in v1.

Known v1 limitations (flagged, not bugs):
- **Non-ASCII passes through unchanged.** `Café Notes` stays `Café Notes`. Modern
  GitHub handles UTF-8 filenames, but older toolchains may not — if you see
  rendering oddities, rename the note to ASCII before republishing.
- **Wikilinks inside code fences or inline code are rewritten.** The regex
  doesn't parse markdown; `` `[[Foo]]` `` will be transformed. Rare in practice.
  Future work: split on fences before applying the regex.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

# Chars that break GitHub wiki filenames or URL resolution — replaced with a dash.
# Includes the Windows reserved seven plus `#` and `%` (URL-special in Gollum).
_UNSAFE_CHARS_RE = re.compile(r'[\\/:*?"<>|#%]+')
_MULTI_WS_RE = re.compile(r"\s+")
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")


def sanitize_page_name(title: str) -> str:
    """Convert a note title into a safe GitHub wiki page display name."""
    if not title:
        return "page"
    s = _UNSAFE_CHARS_RE.sub("-", title)
    s = _MULTI_WS_RE.sub(" ", s).strip()
    # Leading `.` hides the file; leading `-` makes CLI tools treat the
    # filename as a flag. Strip both.
    s = s.lstrip(".-").rstrip(".") or "page"
    return s


def page_filename(page_name: str) -> str:
    """Return the on-disk filename for a page (spaces → hyphens, + .md)."""
    return page_name.replace(" ", "-") + ".md"


def build_page_map(notes: list[tuple[int, str]]) -> dict[int, str]:
    """Build {note_id: page_name} with collision disambiguation.

    Input: list of (note_id, title). Output: {note_id: chosen page_name}.

    Collisions: sorted by note_id so the lowest id keeps the base name, later
    ids get `" (2)"`, `" (3)"`. Deterministic so repeat publishes pick the
    same winner.

    Known limitation: if a note is later renamed and a *different* note takes
    its old sanitized name, the filename mapping swaps — inbound links from
    outside the wiki pointing at the old `Foo.md` URL will silently change
    meaning. Rename-collision is rare enough that v1 accepts it.
    """
    buckets: dict[str, list[int]] = defaultdict(list)
    titles: dict[int, str] = {}
    for nid, title in sorted(notes):
        base = sanitize_page_name(title)
        buckets[base].append(nid)
        titles[nid] = title

    out: dict[int, str] = {}
    for base, ids in buckets.items():
        for idx, nid in enumerate(ids):
            out[nid] = base if idx == 0 else f"{base} ({idx + 1})"
    return out


@dataclass
class OrphanLink:
    """A wikilink whose target isn't in the publish set."""
    source_title: str  # title of the note containing the link
    display_text: str  # text that appeared inside [[...]]
    target_title: str  # the referenced title (after splitting on |)


def transform_body(
    body: str,
    *,
    source_title: str,
    title_to_page: dict[str, str],
    orphan_strategy: Literal["italicize", "plain"] = "italicize",
) -> tuple[str, list[OrphanLink]]:
    """Rewrite wikilinks in `body` for GitHub wiki publication.

    - Known target (present in title_to_page): emit `[[Page Name]]` or
      `[[Display|Page Name]]` if display would resolve differently than target.
    - Unknown target (orphan): rewrite to `_Display_` or plain `Display`,
      and include the occurrence in the returned orphan list.
    """
    orphans: list[OrphanLink] = []

    def _replace(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        if "|" in inner:
            # Obsidian order: `[[target|display]]`
            left, _, right = inner.partition("|")
            target = left.strip()
            display = right.strip()
        else:
            display = target = inner

        page_name = title_to_page.get(target)
        if page_name is None:
            orphans.append(
                OrphanLink(
                    source_title=source_title,
                    display_text=display,
                    target_title=target,
                )
            )
            if orphan_strategy == "italicize":
                return f"_{display}_"
            return display

        # Over-alias when in doubt: if display != page_name by string equality,
        # emit the aliased form. Gollum's actual resolution rules aren't
        # perfectly predictable (especially around non-ASCII); always-aliasing
        # produces correct links and costs a few extra bytes.
        if display == page_name:
            return f"[[{page_name}]]"
        return f"[[{display}|{page_name}]]"

    new_body = _WIKILINK_RE.sub(_replace, body)
    return new_body, orphans


def strip_frontmatter(content: str) -> str:
    """Drop a leading `---` frontmatter block if present."""
    m = re.match(r"^---\n[\s\S]*?\n---\n?", content)
    if not m:
        return content
    return content[m.end():]


def strip_leading_h1(body: str) -> str:
    """GitHub wiki auto-adds a page title — drop a leading H1 to avoid a duplicate."""
    stripped = body.lstrip("\n")
    m = re.match(r"^#\s+.*\n+", stripped)
    if not m:
        return body
    return stripped[m.end():]


def render_page_markdown(body_after_transform: str) -> str:
    """Compose the final page contents.

    GitHub wiki renders the page title above the body automatically, so we
    strip any leading H1 and do NOT re-add one — otherwise the published page
    would show the title twice. Frontmatter is also expected to be stripped
    by the pipeline before calling this (YAML renders as a literal block in
    wiki markdown).
    """
    body = strip_leading_h1(body_after_transform).rstrip()
    return body + "\n" if body else "\n"

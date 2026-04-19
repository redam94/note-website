"""Pure-Python templated rendering for Index notes.

Replaces per-folder and root LLM calls with deterministic markdown emission
from structured facts pulled out of each child note (frontmatter, summary,
tags, cluster_id). All outputs are reproducible — re-rendering from the same
DB state yields byte-identical markdown.

Two adapters build `ChildFacts`:
  • `child_from_note` — parses frontmatter for doc_type / depends_on. Used by
    the maintenance reindex path where no note_plan exists.
  • `child_from_plan_entry` — pulls doc_type / depends_on from the pipeline's
    note_plan entry when available (richer / more accurate).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

_FM_RE = re.compile(r"^---\n([\s\S]*?)\n---")
_WIKI_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]")


@dataclass
class ChildFacts:
    id: int
    title: str
    slug: str | None
    summary: str
    doc_type: str
    depends_on: list[str]
    tags: list[str]
    is_index: bool
    cluster_id: int | None = None


def _extract_frontmatter(content: str) -> str:
    m = _FM_RE.match(content or "")
    return m.group(1) if m else ""


def _fm_scalar(fm: str, field: str) -> str | None:
    m = re.search(rf"^{re.escape(field)}:\s*\"?([^\"\n]+?)\"?\s*$", fm, re.MULTILINE)
    return m.group(1).strip() if m else None


def _fm_wiki_list(fm: str, field: str) -> list[str]:
    """Read [[Title]] entries under a multi-line list field."""
    block = re.search(
        rf"^{re.escape(field)}:(.*?)(?=^\w+:|\Z)",
        fm,
        re.DOTALL | re.MULTILINE,
    )
    if not block:
        return []
    return _WIKI_RE.findall(block.group(1))


def _first_paragraph(content: str, max_chars: int = 200) -> str:
    body = re.sub(r"^---\n[\s\S]*?\n---\n+", "", content or "").strip()
    if not body:
        return ""
    picked: list[str] = []
    for line in body.split("\n"):
        s = line.strip()
        if not s:
            if picked:
                break
            continue
        if s.startswith("#") or s.startswith(">"):
            continue
        picked.append(s)
    para = " ".join(picked)
    if len(para) <= max_chars:
        return para
    return para[:max_chars].rstrip() + "…"


def child_from_note(note, tags_override: str | list | None = None) -> ChildFacts:
    content = note.content or ""
    fm = _extract_frontmatter(content)
    doc_type = _fm_scalar(fm, "doc_type") or "concept"
    depends_on = _fm_wiki_list(fm, "depends_on")
    tags_raw = tags_override if tags_override is not None else note.tags
    try:
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else (list(tags_raw) if tags_raw else [])
    except (json.JSONDecodeError, TypeError):
        tags = []
    summary = note.summary or _first_paragraph(content)
    return ChildFacts(
        id=note.id,
        title=note.title,
        slug=getattr(note, "slug", None),
        summary=summary or "",
        doc_type=doc_type,
        depends_on=depends_on,
        tags=tags,
        is_index=note.title.startswith("Index:"),
        cluster_id=getattr(note, "cluster_id", None),
    )


def child_from_plan_entry(plan: dict, note) -> ChildFacts:
    doc_type = plan.get("doc_type", "concept")
    depends_on = list(plan.get("depends_on", []) or [])
    tags = list(plan.get("tags", []) or [])
    summary = note.summary or _first_paragraph(note.content or "")
    return ChildFacts(
        id=note.id,
        title=note.title,
        slug=getattr(note, "slug", None),
        summary=summary or "",
        doc_type=doc_type,
        depends_on=depends_on,
        tags=tags,
        is_index=note.title.startswith("Index:"),
        cluster_id=getattr(note, "cluster_id", None),
    )


# ── Rendering primitives ─────────────────────────────────────────────


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return singular if n == 1 else (plural or singular + "s")


def _concept_map(content_children: Iterable[ChildFacts]) -> str:
    rows: list[str] = []
    for c in content_children:
        depends = (
            ", ".join(f"[[{d}]]" for d in c.depends_on[:3])
            if c.depends_on
            else "—"
        )
        summary = (c.summary or "")[:80].replace("|", r"\|")
        rows.append(f"| [[{c.title}]] | {c.doc_type} | {depends} | {summary} |")
    if not rows:
        return ""
    header = (
        "| Concept | Type | Depends On | Key Result |\n"
        "|---------|------|-----------|------------|\n"
    )
    return header + "\n".join(rows)


def _routing_lines(children: Iterable[ChildFacts]) -> list[str]:
    kids = list(children)
    # Sub-topics first, then content notes (navigation priority).
    ordered = [c for c in kids if c.is_index] + [c for c in kids if not c.is_index]
    out: list[str] = []
    for c in ordered:
        hint = (c.summary or "").strip().rstrip(".") or c.doc_type
        prefix = "Sub-topic " if c.is_index else ""
        out.append(f"> - {prefix}[[{c.title}]] — {hint[:120]}")
    return out


# ── Folder + Root indexes ────────────────────────────────────────────


def render_folder_body(
    folder_path: str,
    children: list[ChildFacts],
    see_also: list[tuple[str, str]] | None = None,
) -> tuple[str, str]:
    """Return (summary, markdown_body_after_h1) for a folder index."""
    child_indexes = [c for c in children if c.is_index]
    content_children = [c for c in children if not c.is_index]

    lines: list[str] = []

    lines.append("> [!abstract] Routing Summary")
    lines.append(
        f"> Contains {len(children)} {_plural(len(children), 'item')}"
        f" ({len(child_indexes)} {_plural(len(child_indexes), 'sub-topic')},"
        f" {len(content_children)} {_plural(len(content_children), 'note')})."
    )
    lines.extend(_routing_lines(children))
    lines.append("")

    if child_indexes:
        lines.append("## Sub-topics")
        for c in child_indexes:
            hint = (c.summary or "").strip().rstrip(".") or "sub-topic index"
            lines.append(f"- [[{c.title}]] — {hint[:160]}")
        lines.append("")

    concept_map = _concept_map(content_children)
    if concept_map:
        lines.append("## Concept Map")
        lines.append(concept_map)
        lines.append("")

    if content_children:
        lines.append("## Notes")
        for c in content_children:
            tags_inline = f" *({', '.join(c.tags[:3])})*" if c.tags else ""
            covers = (c.summary or "").strip() or c.doc_type
            lines.append(f"- [[{c.title}]]{tags_inline} — COVERS: {covers[:160]}")
        lines.append("")

    if see_also:
        lines.append("## See also")
        for title, reason in see_also:
            lines.append(f"- [[{title}]] — {reason}")
        lines.append("")

    body = "\n".join(lines).rstrip() + "\n"
    leaf = folder_path.rsplit("/", 1)[-1] if "/" in folder_path else folder_path
    summary = (
        f"{leaf}: {len(content_children)} {_plural(len(content_children), 'note')},"
        f" {len(child_indexes)} {_plural(len(child_indexes), 'sub-topic')}."
    )
    return summary, body


def render_folder_index_note(
    folder_path: str,
    children: list[ChildFacts],
    see_also: list[tuple[str, str]] | None = None,
    title_prefix: str = "Index: ",
    today: str | None = None,
) -> tuple[str, str]:
    """Render full frontmatter + H1 + body for a folder index.
    Returns (summary, full_markdown)."""
    summary, body = render_folder_body(folder_path, children, see_also=see_also)
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    index_title = f"{title_prefix}{folder_path}"
    leaf = folder_path.rsplit("/", 1)[-1] if "/" in folder_path else folder_path
    frontmatter = (
        "---\n"
        f'title: "{index_title}"\n'
        "tags:\n"
        "  - type/index\n"
        f"date_updated: {today}\n"
        f"concept_count: {len(children)}\n"
        "---"
    )
    return summary, f"{frontmatter}\n\n# {leaf}\n\n{body}"


def render_root_index_note(
    children: list[ChildFacts],
    total_notes: int,
    today: str | None = None,
) -> tuple[str, str]:
    """Render the root index. Returns (summary, full_markdown)."""
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    topic_indexes = [c for c in children if c.is_index]
    orphans = [c for c in children if not c.is_index]

    lines: list[str] = []
    lines.append("> [!abstract] Knowledge Base")
    lines.append(
        f"> {total_notes} {_plural(total_notes, 'note')} organized into"
        f" {len(topic_indexes)} top-level"
        f" {_plural(len(topic_indexes), 'topic')}."
    )
    for c in topic_indexes:
        hint = (c.summary or "").strip().rstrip(".") or "top-level topic"
        lines.append(f"> - [[{c.title}]] — {hint[:120]}")
    lines.append("")

    if topic_indexes:
        lines.append("## Topics")
        for c in topic_indexes:
            hint = (c.summary or "").strip().rstrip(".") or "topic index"
            lines.append(f"- [[{c.title}]] — {hint[:200]}")
        lines.append("")

    if orphans:
        lines.append("## Unclassified Notes")
        for c in orphans[:50]:
            lines.append(f"- [[{c.title}]]")
        if len(orphans) > 50:
            lines.append(f"- …and {len(orphans) - 50} more")
        lines.append("")

    body = "\n".join(lines).rstrip() + "\n"
    frontmatter = (
        "---\n"
        'title: "Index: Root"\n'
        "tags:\n"
        "  - type/index\n"
        f"date_updated: {today}\n"
        f"concept_count: {total_notes}\n"
        "---"
    )
    summary = f"Knowledge base with {len(topic_indexes)} top-level topics, {total_notes} notes."
    return summary, f"{frontmatter}\n\n# Knowledge Base\n\n{body}"


# ── Cross-cluster see-also derivation (pipeline path only) ───────────


def see_also_from_clusters(
    focal_cluster_id: int,
    cluster_by_id: dict,
    subgraph_edges: Iterable,
    limit: int = 5,
) -> list[tuple[str, str]]:
    """Derive [(index_title, reason)] from persisted SubgraphEdge rows.

    Used where every child's cluster_id is known (pipeline path). The reindex
    path skips see-also because folder ≠ cluster in general.
    """
    scored: list[tuple[int, int]] = []
    for e in subgraph_edges:
        if e.source_cluster_id == focal_cluster_id:
            scored.append((e.target_cluster_id, e.cross_edge_count or int(e.weight or 0)))
        elif e.target_cluster_id == focal_cluster_id:
            scored.append((e.source_cluster_id, e.cross_edge_count or int(e.weight or 0)))
    scored.sort(key=lambda t: -t[1])

    out: list[tuple[str, str]] = []
    for other_id, count in scored[:limit]:
        node = cluster_by_id.get(other_id)
        if not node:
            continue
        label = node.path or node.label
        reason = f"shares {count} cross-{_plural(count, 'link')}"
        out.append((f"Index: {label}", reason))
    return out

"""Built-in extraction profile presets.

Each preset is a ready-to-install ExtractionProfile definition. The
`/api/extraction-profiles/presets` endpoint lists them; `/presets/install`
creates rows in the current space.
"""
from __future__ import annotations

IPYNB_MD_SCRIPT = r'''# Convert .ipynb JSON to Markdown; pass through .md unchanged.
import json, os, sys

path = sys.argv[1]
ext = os.path.splitext(path)[1].lower()

if ext == ".ipynb":
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        nb = json.load(f)
    lang = nb.get("metadata", {}).get("language_info", {}).get("name", "python")
    chunks = []
    for cell in nb.get("cells", []):
        src = cell.get("source", "")
        if isinstance(src, list):
            src = "".join(src)
        ctype = cell.get("cell_type", "")
        if ctype == "markdown":
            chunks.append(src.rstrip())
        elif ctype == "code":
            chunks.append("```" + lang + "\n" + src.rstrip() + "\n```")
            for o in cell.get("outputs", []):
                text = None
                if "text" in o:
                    text = o["text"]
                elif o.get("output_type") in ("execute_result", "display_data"):
                    data = o.get("data", {})
                    text = data.get("text/plain")
                if isinstance(text, list):
                    text = "".join(text)
                if text and text.strip():
                    chunks.append("```\n" + text.rstrip()[:2000] + "\n```")
        chunks.append("")
    sys.stdout.write("\n".join(chunks))
else:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        sys.stdout.write(f.read())
'''


PRESETS: list[dict] = [
    {
        "key": "code_notebooks",
        "name": "Code Examples & Notebooks",
        "description": "Markdown tutorials, blog posts with code (Medium exports), and Jupyter notebooks. Preserves code blocks verbatim.",
        "extensions": ["md", "ipynb"],
        "mime_types": [],
        "doc_type_override": "tutorial",
        "script": IPYNB_MD_SCRIPT,
        "prompt_additions": (
            "This document is a tutorial or code walkthrough.\n"
            "- Preserve every code block verbatim inside fenced ```language ... ``` blocks. "
            "Never paraphrase, summarise, or reformat code.\n"
            "- Create one note per concept, technique, or self-contained example — not per code cell.\n"
            "- Wrap runnable examples in [!example] callouts; keep the surrounding prose explaining the WHY and the expected output.\n"
            "- Preserve import statements and library names so the note is reproducible.\n"
            "- Link related notes with [[wiki-links]] when a concept builds on another."
        ),
    },
    {
        "key": "textbook",
        "name": "Textbook",
        "description": "Academic textbooks. Emphasises definitions, theorems, worked examples, and inter-chapter cross-links. Select explicitly at upload.",
        "extensions": [],
        "mime_types": [],
        "doc_type_override": "textbook",
        "script": "",
        "prompt_additions": (
            "This document is a textbook chapter or section.\n"
            "- Use [!definition] for new terms, [!theorem] for formal statements (with proof sketches), "
            "and [!example] for worked examples.\n"
            "- Preserve all mathematical notation in LaTeX — inline with $...$ and display with $$...$$ on its own line.\n"
            "- Create one note per major concept, not per page. A single note may span several pages of source text.\n"
            "- Cross-link prerequisites and derived results with [[wiki-links]].\n"
            "- Attach ^block-ids to key equations and definitions so other notes can reference them directly."
        ),
    },
    {
        "key": "research_paper",
        "name": "Research Paper",
        "description": "Open-access academic papers. Preserves equations, extracts claims/methods/results, links related work. Select explicitly at upload.",
        "extensions": [],
        "mime_types": [],
        "doc_type_override": "paper",
        "script": "",
        "prompt_additions": (
            "This document is an academic research paper.\n"
            "- Create notes for: the central contribution, key notation and definitions, each major theorem/lemma "
            "(with proof sketch if present), the methodology, the main experimental or analytical results, and open questions.\n"
            "- Preserve every equation in LaTeX — inline with $...$, display with $$...$$ on its own line. Do not drop subscripts, "
            "superscripts, or mathematical operators.\n"
            "- Use [!theorem], [!definition], [!important], and [!example] callouts liberally.\n"
            "- Link theorems to the notes containing their proofs, and cite related work with [[wiki-links]] "
            "to other notes in the space when the concept overlaps.\n"
            "- Attach ^block-ids (e.g. ^thm-main, ^eq-loss) to theorems and named equations so other notes can point to them."
        ),
    },
]


def preset_by_key(key: str) -> dict | None:
    for p in PRESETS:
        if p["key"] == key:
            return p
    return None

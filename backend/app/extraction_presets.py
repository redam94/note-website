"""Built-in extraction profile presets.

Each preset is a ready-to-install ExtractionProfile definition. The
`/api/extraction-profiles/presets` endpoint lists them; `/presets/install`
creates rows in the current space.

Prompt additions live as YAML templates in `app/prompts/preset_*.yaml`
and are loaded via the shared prompt template system.
"""
from __future__ import annotations

from functools import lru_cache

from .prompts import load_prompt

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


@lru_cache(maxsize=None)
def _prompt_additions(name: str) -> str:
    """Render a preset's prompt-additions YAML template. Cached after first load."""
    return load_prompt(name).format()


_PRESET_DEFS: list[dict] = [
    {
        "key": "code_notebooks",
        "name": "Code Examples & Notebooks",
        "description": "Markdown tutorials, blog posts with code (Medium exports), and Jupyter notebooks. Preserves code blocks verbatim.",
        "extensions": ["md", "ipynb"],
        "mime_types": [],
        "doc_type_override": "tutorial",
        "script": IPYNB_MD_SCRIPT,
        "prompt_template": "preset_code_notebooks",
    },
    {
        "key": "textbook",
        "name": "Textbook",
        "description": "Academic textbooks. Emphasises definitions, theorems, worked examples, and inter-chapter cross-links. Select explicitly at upload.",
        "extensions": [],
        "mime_types": [],
        "doc_type_override": "textbook",
        "script": "",
        "prompt_template": "preset_textbook",
    },
    {
        "key": "research_paper",
        "name": "Research Paper",
        "description": "Open-access academic papers. Preserves equations, extracts claims/methods/results, links related work. Select explicitly at upload.",
        "extensions": [],
        "mime_types": [],
        "doc_type_override": "paper",
        "script": "",
        "prompt_template": "preset_research_paper",
    },
    {
        "key": "research_paper_plots",
        "name": "Research Paper + Plots",
        "description": "Same as Research Paper, but notes may embed Plotly charts when the paper states a functional relationship or reports numbers worth visualising. Strict rules against fabricating data.",
        "extensions": [],
        "mime_types": [],
        "doc_type_override": "paper",
        "script": "",
        "prompt_template": "preset_research_paper_plots",
    },
]


def _materialize(preset: dict) -> dict:
    """Expand a preset definition by rendering its prompt template."""
    return {
        "key": preset["key"],
        "name": preset["name"],
        "description": preset["description"],
        "extensions": preset["extensions"],
        "mime_types": preset["mime_types"],
        "doc_type_override": preset["doc_type_override"],
        "script": preset["script"],
        "prompt_additions": _prompt_additions(preset["prompt_template"]),
    }


PRESETS: list[dict] = [_materialize(p) for p in _PRESET_DEFS]


def preset_by_key(key: str) -> dict | None:
    for p in PRESETS:
        if p["key"] == key:
            return p
    return None

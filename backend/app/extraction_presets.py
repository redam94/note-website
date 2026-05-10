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

PYTHON_SOURCE_SCRIPT = r'''# Transform a Python source file into a note-friendly Markdown structure.
#
# The section layout matches what downstream chunking expects: each public
# class / top-level function has its own heading *with its verbatim source*
# inlined in a fenced block, so when the planner asks for "class Foo" the
# note-creation LLM actually sees the class body — not just its signature.
#
# Private helpers (underscore-prefixed) are intentionally omitted. A class
# that calls a private helper won't see that helper's body; the prompt
# already discourages notes about private code, so this is acceptable.
#
# Falls back to a raw fenced source on SyntaxError so partial / Py2 /
# vendored files still flow through the pipeline.
import ast, os, sys

path = sys.argv[1]
basename = os.path.basename(path)
parent = os.path.basename(os.path.dirname(path))
is_test = (
    basename.startswith("test_")
    or basename.endswith("_test.py")
    or parent in ("tests", "test")
)

with open(path, "r", encoding="utf-8", errors="replace") as f:
    source = f.read()

try:
    tree = ast.parse(source, filename=path)
except SyntaxError as e:
    sys.stdout.write(f"# `{basename}`\n\n")
    sys.stdout.write(f"<!-- ast.parse failed: {e} -->\n\n")
    sys.stdout.write("```python\n" + source.rstrip() + "\n```\n")
    sys.exit(0)

source_lines = source.splitlines(keepends=True)

def _u(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "..."

def _signature(node) -> str:
    args = _u(node.args)
    rt = " -> " + _u(node.returns) if node.returns is not None else ""
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    return f"{prefix}{node.name}({args}){rt}"

def _verbatim(node) -> str:
    """Verbatim source of a class/function, including its decorators."""
    start_linenos = [node.lineno]
    for dec in getattr(node, "decorator_list", []) or []:
        start_linenos.append(dec.lineno)
    start = min(start_linenos) - 1  # 0-indexed
    end = getattr(node, "end_lineno", None)
    if end is None:
        try:
            return ast.unparse(node)
        except Exception:
            return ""
    return "".join(source_lines[start:end]).rstrip()

module_doc = ast.get_docstring(tree) or ""

imports = []
classes = []
functions = []
body_chunks = []  # module-level code that isn't a class / function / import / docstring

def _is_docstring_expr(node) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )

for i, node in enumerate(tree.body):
    if isinstance(node, ast.Import):
        for n in node.names:
            imports.append(n.name + (f" as {n.asname}" if n.asname else ""))
    elif isinstance(node, ast.ImportFrom):
        mod = ("." * (node.level or 0)) + (node.module or "")
        names = ", ".join(n.name + (f" as {n.asname}" if n.asname else "") for n in node.names)
        imports.append(f"from {mod} import {names}")
    elif isinstance(node, ast.ClassDef):
        bases = ""
        if node.bases:
            bases = "(" + ", ".join(_u(b) for b in node.bases) + ")"
        classes.append({
            "name": node.name,
            "bases": bases,
            "doc": ast.get_docstring(node) or "",
            "source": _verbatim(node),
        })
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if node.name.startswith("_"):
            continue
        functions.append({
            "name": node.name,
            "sig": _signature(node),
            "doc": ast.get_docstring(node) or "",
            "source": _verbatim(node),
        })
    else:
        # Module-level code: constants, if __name__ == "__main__", etc.
        # Skip the module docstring (handled separately).
        if i == 0 and _is_docstring_expr(node):
            continue
        start_linenos = [node.lineno]
        for dec in getattr(node, "decorator_list", []) or []:
            start_linenos.append(dec.lineno)
        start = min(start_linenos) - 1
        end = getattr(node, "end_lineno", None) or node.lineno
        chunk = "".join(source_lines[start:end]).rstrip()
        if chunk:
            body_chunks.append(chunk)

out = [f"# `{basename}` — structural overview", ""]
if is_test:
    out += [
        "> File classification: **test module**. Notes should describe the behaviour",
        "> being verified and link to the code under test via [[wiki-links]].",
        "",
    ]
if module_doc:
    out += ["## Module docstring", "", module_doc.strip(), ""]
if imports:
    out += ["## Imports", "", "```python", *imports, "```", ""]

if classes:
    out += ["## Public classes", ""]
    for c in classes:
        out.append(f"### `class {c['name']}{c['bases']}`")
        out.append("")
        if c["doc"]:
            out += [c["doc"].strip(), ""]
        out += ["```python", c["source"], "```", ""]

if functions:
    out += ["## Public functions", ""]
    for fn in functions:
        out.append(f"### `{fn['sig']}`")
        out.append("")
        if fn["doc"]:
            out += [fn["doc"].strip(), ""]
        out += ["```python", fn["source"], "```", ""]

if body_chunks:
    out += ["## Module body", "", "```python", "\n\n".join(body_chunks), "```", ""]

sys.stdout.write("\n".join(out))
'''


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
        "key": "python_source",
        "name": "Python Source",
        "description": "Python source files from a git repo. Prepends an AST-derived structural header (module docstring, imports, public API signatures) so notes can be planned around the file's shape, then preserves the full source. Detects test files by path and biases notes toward describing behaviour. Use as the per-repo default profile when ingesting Python code.",
        "extensions": ["py"],
        "mime_types": [],
        "doc_type_override": "tutorial",
        "script": PYTHON_SOURCE_SCRIPT,
        "prompt_template": "preset_python_source",
    },
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

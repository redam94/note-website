from langgraph.graph import END, StateGraph

from .checkpoint import with_checkpoint
from .nodes.community_update import update_communities
from .nodes.create_notes import create_notes
from .nodes.cross_link import detect_cross_links
from .nodes.ensure_tree import ensure_tree
from .nodes.extract_structure import extract_document_structure
from .nodes.index_gen import generate_indices
from .nodes.insert_links import insert_links
from .nodes.outline import detect_outline
from .nodes.parse import parse_document
from .nodes.plan import create_plan
from .state import ProcessingState

# Node order — used for resumption logic
NODE_ORDER = [
    "parse",
    "extract_structure",
    "outline",
    "plan",
    "ensure_tree",
    "create_notes",
    "index_gen",
    "insert_links",
    "cross_link",
    "community_update",
]

# Wrap each node with checkpoint saving
_nodes = {
    "parse": with_checkpoint("parse")(parse_document),
    "extract_structure": with_checkpoint("extract_structure")(extract_document_structure),
    "outline": with_checkpoint("outline")(detect_outline),
    "plan": with_checkpoint("plan")(create_plan),
    "ensure_tree": with_checkpoint("ensure_tree")(ensure_tree),
    "create_notes": with_checkpoint("create_notes")(create_notes),
    "index_gen": with_checkpoint("index_gen")(generate_indices),
    "insert_links": with_checkpoint("insert_links")(insert_links),
    "cross_link": with_checkpoint("cross_link")(detect_cross_links),
    "community_update": with_checkpoint("community_update")(update_communities),
}

workflow = StateGraph(ProcessingState)

for name, fn in _nodes.items():
    workflow.add_node(name, fn)

workflow.set_entry_point("parse")
workflow.add_edge("parse", "extract_structure")
workflow.add_edge("extract_structure", "outline")
workflow.add_edge("outline", "plan")
workflow.add_edge("plan", "ensure_tree")
workflow.add_edge("ensure_tree", "create_notes")
workflow.add_edge("create_notes", "index_gen")
workflow.add_edge("index_gen", "insert_links")
workflow.add_edge("insert_links", "cross_link")
workflow.add_edge("cross_link", "community_update")
workflow.add_edge("community_update", END)

processing_pipeline = workflow.compile()

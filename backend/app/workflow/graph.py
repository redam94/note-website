from langgraph.graph import END, StateGraph

from .nodes.create_notes import create_notes
from .nodes.cross_link import detect_cross_links
from .nodes.extract_structure import extract_document_structure
from .nodes.index_gen import generate_indices
from .nodes.insert_links import insert_links
from .nodes.outline import detect_outline
from .nodes.parse import parse_document
from .nodes.plan import create_plan
from .state import ProcessingState

workflow = StateGraph(ProcessingState)

workflow.add_node("parse", parse_document)
workflow.add_node("extract_structure", extract_document_structure)
workflow.add_node("outline", detect_outline)
workflow.add_node("plan", create_plan)
workflow.add_node("create_notes", create_notes)
workflow.add_node("index_gen", generate_indices)
workflow.add_node("insert_links", insert_links)
workflow.add_node("cross_link", detect_cross_links)

workflow.set_entry_point("parse")
workflow.add_edge("parse", "extract_structure")
workflow.add_edge("extract_structure", "outline")
workflow.add_edge("outline", "plan")
workflow.add_edge("plan", "create_notes")
workflow.add_edge("create_notes", "index_gen")
workflow.add_edge("index_gen", "insert_links")
workflow.add_edge("insert_links", "cross_link")
workflow.add_edge("cross_link", END)

processing_pipeline = workflow.compile()

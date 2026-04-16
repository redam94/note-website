from sqlalchemy import Column, Float, ForeignKey, Integer, Text

from .base import Base


class GraphEdge(Base):
    __tablename__ = "graph_edges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, ForeignKey("notes.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = Column(Integer, ForeignKey("notes.id", ondelete="CASCADE"), nullable=False, index=True)
    relationship_type = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False, default=0.5)
    created_by = Column(Text, nullable=False, default="llm")
    created_at = Column(Text, nullable=False)

from sqlalchemy import Column, Float, ForeignKey, Integer, Text

from .base import Base


class SubgraphEdge(Base):
    __tablename__ = "subgraph_edges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_cluster_id = Column(
        Integer, ForeignKey("subgraph_nodes.id", ondelete="CASCADE"), nullable=False
    )
    target_cluster_id = Column(
        Integer, ForeignKey("subgraph_nodes.id", ondelete="CASCADE"), nullable=False
    )
    weight = Column(Float, nullable=False, default=0.0)
    cross_edge_count = Column(Integer, nullable=False, default=0)
    created_at = Column(Text, nullable=False)

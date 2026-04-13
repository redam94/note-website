from sqlalchemy import Column, ForeignKey, Integer, Text

from .base import Base


class SubgraphNode(Base):
    __tablename__ = "subgraph_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_cluster_id = Column(
        Integer, ForeignKey("subgraph_nodes.id", ondelete="SET NULL"), nullable=True
    )
    label = Column(Text, nullable=False)
    path = Column(Text, nullable=True)  # Full hierarchical path, e.g. "Statistics/Bayesian Methods"
    level = Column(Integer, nullable=False, default=0)  # Depth in hierarchy (0=top)
    member_node_ids = Column(Text, nullable=False, default="[]")  # JSON array
    summary = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    space_id = Column(
        Integer, ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False, default=1
    )

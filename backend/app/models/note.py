from sqlalchemy import Column, ForeignKey, Integer, Text

from .base import Base


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True)
    parent_id = Column(Integer, nullable=True, index=True)
    title = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    slug = Column(Text, nullable=False, unique=True)
    tags = Column(Text, default="[]")  # JSON array stored as text
    level = Column(Integer, nullable=False, default=1)
    created_at = Column(Text, nullable=False)
    # New fields
    source = Column(Text, nullable=True)
    chapter = Column(Text, nullable=True)
    page = Column(Integer, nullable=True)
    summary = Column(Text, nullable=True)
    cluster_id = Column(
        Integer, ForeignKey("subgraph_nodes.id", ondelete="SET NULL"), nullable=True
    )
    space_id = Column(
        Integer, ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False, default=1, index=True
    )

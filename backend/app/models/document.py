from sqlalchemy import Column, Integer, Text

from .base import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(Text, nullable=False)
    original_name = Column(Text, nullable=False)
    mime_type = Column(Text, nullable=False)
    content_raw = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="pending")
    error = Column(Text, nullable=True)
    processing_step = Column(Text, nullable=True)
    notes_count = Column(Integer, nullable=True)
    checkpoint = Column(Text, nullable=True)  # JSON-serialized pipeline state for resumption
    last_completed_node = Column(Text, nullable=True)  # e.g., "outline", "plan", "create_notes"
    created_at = Column(Text, nullable=False)

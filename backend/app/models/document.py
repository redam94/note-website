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
    processing_step = Column(Text, nullable=True)  # current pipeline step description
    notes_count = Column(Integer, nullable=True)    # number of notes created so far
    created_at = Column(Text, nullable=False)

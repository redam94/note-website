from sqlalchemy import Column, ForeignKey, Integer, Text

from .base import Base


class NoteComment(Base):
    __tablename__ = "note_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    note_id = Column(Integer, ForeignKey("notes.id", ondelete="CASCADE"), nullable=False)
    author = Column(Text, nullable=False, default="user")  # "admin" or "user"
    content = Column(Text, nullable=False)
    resolved = Column(Integer, nullable=False, default=0)  # 0=open, 1=resolved
    created_at = Column(Text, nullable=False)

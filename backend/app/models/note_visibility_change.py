from sqlalchemy import Column, ForeignKey, Index, Integer, Text

from .base import Base


class NoteVisibilityChange(Base):
    __tablename__ = "note_visibility_changes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    note_id = Column(
        Integer,
        ForeignKey("notes.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_visibility = Column(Text, nullable=False)
    to_visibility = Column(Text, nullable=False)
    actor = Column(Text, nullable=False)
    changed_at = Column(Text, nullable=False)

    __table_args__ = (
        Index("ix_note_visibility_changes_note_id", "note_id"),
        Index("ix_note_visibility_changes_changed_at", "changed_at"),
    )

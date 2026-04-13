from sqlalchemy import Column, Integer, Text

from .base import Base


class Space(Base):
    __tablename__ = "spaces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    slug = Column(Text, nullable=False, unique=True)
    description = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False)

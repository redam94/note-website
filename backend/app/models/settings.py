from sqlalchemy import Column, Text

from .base import Base


class Settings(Base):
    __tablename__ = "settings"

    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=False)

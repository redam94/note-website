from sqlalchemy import Column, ForeignKey, Integer, Text

from .base import Base


class ExtractionProfile(Base):
    __tablename__ = "extraction_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    description = Column(Text, default="")
    # JSON list of file extensions without leading dot, e.g. ["eml", "zip"]
    extensions = Column(Text, default="[]")
    # JSON list of MIME type strings, e.g. ["message/rfc822"]
    mime_types = Column(Text, default="[]")
    # Override the classifier — e.g. "report", "email", "codebase"
    doc_type_override = Column(Text, nullable=True)
    # Python source code executed in a subprocess to transform raw text.
    # Receives file_path as sys.argv[1]; must print transformed text to stdout.
    script = Column(Text, default="")
    # Extra instructions appended to the LLM system prompt during note creation.
    prompt_additions = Column(Text, default="")
    created_at = Column(Text, nullable=False)
    space_id = Column(
        Integer, ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False, default=1
    )

from sqlalchemy import Column, ForeignKey, Integer, Text, UniqueConstraint

from .base import Base


class IntegrationPublishTarget(Base):
    __tablename__ = "integration_publish_targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(
        Integer,
        ForeignKey("connected_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    space_id = Column(
        Integer,
        ForeignKey("spaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Optional link back to the tracked IntegrationResource (so disabling
    # tracking also cleans up the target via the nullable FK).
    resource_id = Column(
        Integer,
        ForeignKey("integration_resources.id", ondelete="SET NULL"),
        nullable=True,
    )
    repo_full_name = Column(Text, nullable=False)  # e.g. "alice/proj"
    wiki_branch = Column(Text, nullable=False, default="master")
    last_published_sha = Column(Text, nullable=True)
    last_published_at = Column(Text, nullable=True)
    config_json = Column(Text, nullable=True, default="{}")
    created_at = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("space_id", name="uq_integration_publish_targets_space"),
    )

from sqlalchemy import Column, ForeignKey, Index, Integer, Text

from .base import Base


class IntegrationResource(Base):
    __tablename__ = "integration_resources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(
        Integer,
        ForeignKey("connected_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource_type = Column(Text, nullable=False)  # e.g. 'repo'
    external_id = Column(Text, nullable=False)  # GitHub repo id as string
    name = Column(Text, nullable=False)  # full_name, e.g. "owner/repo"
    enabled = Column(Integer, nullable=False, default=1)  # bool 0/1
    last_synced_at = Column(Text, nullable=True)
    sync_cursor = Column(Text, nullable=True)
    # JSON blob — for resource_type='repo': {issues_enabled, prs_enabled, wiki_enabled,
    # default_branch, private, etc.}
    config_json = Column(Text, nullable=True, default="{}")
    space_id = Column(
        Integer,
        ForeignKey("spaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(Text, nullable=False)

    __table_args__ = (
        Index(
            "ix_integration_resources_account_type_ext",
            "account_id",
            "resource_type",
            "external_id",
            unique=True,
        ),
    )

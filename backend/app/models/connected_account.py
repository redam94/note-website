from sqlalchemy import Column, Index, Integer, Text

from .base import Base


class ConnectedAccount(Base):
    __tablename__ = "connected_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    integration_type = Column(Text, nullable=False)
    label = Column(Text, nullable=False)
    external_account_id = Column(Text, nullable=True)
    oauth_access_token_enc = Column(Text, nullable=True)
    oauth_refresh_token_enc = Column(Text, nullable=True)
    token_expires_at = Column(Text, nullable=True)
    scopes = Column(Text, nullable=True, default="[]")
    metadata_json = Column(Text, nullable=True, default="{}")
    created_at = Column(Text, nullable=False)

    __table_args__ = (
        Index("ix_connected_accounts_type_ext", "integration_type", "external_account_id"),
    )

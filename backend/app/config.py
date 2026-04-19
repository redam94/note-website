import secrets
from pathlib import Path

from pydantic_settings import BaseSettings


class AppSettings(BaseSettings):
    database_url: str = str(Path(__file__).resolve().parent.parent.parent / "data" / "knowledge.db")
    uploads_dir: str = str(Path(__file__).resolve().parent.parent.parent / "uploads")
    redis_url: str = "redis://localhost:6379"
    use_redis: bool = False
    cors_origins: list[str] = ["http://localhost:3000"]

    # How many documents to process in parallel during batch upload.
    # Set to 1 on memory-constrained instances (e.g. Cloud Run small).
    doc_concurrency: int = 2

    # Auth
    admin_password: str = "admin"  # Override via ADMIN_PASSWORD env var
    jwt_secret: str = secrets.token_hex(32)  # Override via JWT_SECRET for persistence across restarts

    # Integrations
    integration_enc_key: str | None = None  # Fernet key; required before any OAuth token is stored

    # GitHub App (Phase 1 integration). All four must be set before
    # /api/integrations/github/* will succeed. Private key should be the full
    # PEM contents (multi-line or with literal \n — normalized on read).
    github_app_id: str | None = None
    github_app_slug: str | None = None
    github_app_private_key: str | None = None
    github_webhook_secret: str | None = None
    # The GitHub App's "Setup URL" (where GitHub redirects after install) is
    # configured on github.com in the App settings, not here. It should point
    # to {your-frontend-origin}/api/integrations/github/callback, which the
    # Next.js rewrite forwards to FastAPI.

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = AppSettings()

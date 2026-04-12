import secrets
from pathlib import Path

from pydantic_settings import BaseSettings


class AppSettings(BaseSettings):
    database_url: str = str(Path(__file__).resolve().parent.parent.parent / "data" / "knowledge.db")
    uploads_dir: str = str(Path(__file__).resolve().parent.parent.parent / "uploads")
    redis_url: str = "redis://localhost:6379"
    use_redis: bool = False
    cors_origins: list[str] = ["http://localhost:3000"]

    # Auth
    admin_password: str = "admin"  # Override via ADMIN_PASSWORD env var
    jwt_secret: str = secrets.token_hex(32)  # Override via JWT_SECRET for persistence across restarts

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = AppSettings()

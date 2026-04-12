from pathlib import Path

from pydantic_settings import BaseSettings


class AppSettings(BaseSettings):
    database_url: str = str(Path(__file__).resolve().parent.parent.parent / "data" / "knowledge.db")
    uploads_dir: str = str(Path(__file__).resolve().parent.parent.parent / "uploads")
    redis_url: str = "redis://localhost:6379"
    use_redis: bool = False
    cors_origins: list[str] = ["http://localhost:3000"]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = AppSettings()

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./rsa_project.db"
    secret_key: str = "development-only-change-me"
    cors_origins: str = "http://localhost:5173"
    upload_dir: Path = Path("uploads")
    max_zip_bytes: int = 1_000_000
    max_unzipped_bytes: int = 5_000_000


settings = Settings()

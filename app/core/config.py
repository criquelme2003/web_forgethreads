from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    auth_username: str
    auth_password: str
    session_secret_key: str


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

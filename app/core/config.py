from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    auth_username: str
    auth_password: str
    session_secret_key: str
    cuda3_ip:str
    cuda3_username:str
    cuda3_password:str
    
# ENABLE CACHE PERSISTENCE AND SINGLETON PATTERN
@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

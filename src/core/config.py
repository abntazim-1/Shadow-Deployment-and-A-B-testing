from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import HttpUrl

class Settings(BaseSettings):
    experiment_salt: str
    admin_api_key: str
    
    redis_url: str = "redis://localhost:6379/0"
    sqlite_db_path: str = "sqlite:///./evaluations.db"
    
    primary_llm_url: HttpUrl
    primary_model_name: str
    
    shadow_llm_url: HttpUrl
    shadow_model_name: str
    
    shadow_enabled_global: bool = True
    challenger_traffic_weight: float = 0.5
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

# Fail-fast instantiation. The app crashes here on startup if required env vars are missing or invalid.
settings = Settings()

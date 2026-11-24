from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MarBet"
    app_env: str = "development"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./marbet.db"
    jwt_secret_key: str = "change-this-secret-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30
    cors_origins: str = "http://localhost:5173,http://localhost:8000,http://127.0.0.1:5173,http://127.0.0.1:8000,https://futebool.onrender.com"
    timezone: str = "Africa/Luanda"
    api_football_key: str = ""
    api_football_base_url: str = "https://v3.football.api-sports.io"
    football_data_key: str = ""
    football_data_token: str = ""
    football_data_base_url: str = "https://api.football-data.org/v4"
    api_football_daily_limit: int = 100
    football_data_daily_limit: int = 500
    football_data_requests_per_minute: int = 10
    data_cache_ttl_seconds: int = 300
    intelligence_min_history_matches: int = 30
    intelligence_top8_min_probability: float = 0.58
    intelligence_top8_min_data_quality: float = 0.40
    intelligence_pick_min_probability: float = 0.55
    intelligence_pick_min_edge: float = 0.03
    intelligence_model_version: str = "1.0.0"
    intelligence_min_market_gain: float = 0.005
    intelligence_min_market_samples: int = 30
    intelligence_min_ev: float = 0.03

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

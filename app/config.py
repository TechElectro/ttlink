from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ttlock_client_id: str = ""
    ttlock_client_secret: str = ""
    ttlock_username: str = ""
    ttlock_password_md5: str = ""
    ttlock_api_base: str = "https://euapi.ttlock.com"
    public_base_url: str = "http://localhost:8000"
    admin_api_key: str = "change-me"
    hotel_name: str = "Hotel"
    database_url: str = "sqlite:///./ttlink.db"
    unlock_min_interval_s: int = 5


settings = Settings()

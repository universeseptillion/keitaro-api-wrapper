from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    keitaro_base_url: str
    keitaro_api_key: str

    keitaro_domain_id: int
    keitaro_group_id: int
    keitaro_traffic_source_id: int
    campaign_prefix: str

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
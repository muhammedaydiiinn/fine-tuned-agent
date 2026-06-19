from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Proje
    project_name: str = "anrufblocker-platform"
    environment: str = "staging"
    log_level: str = "INFO"

    # PostgreSQL
    postgres_db: str = "anrufblocker"
    postgres_user: str = "anrufblocker"
    postgres_password: str = "change_me"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # vLLM
    # "mock" → GPU'suz lokal geliştirme; "real" → GPU sunucu
    vllm_mode: str = "mock"
    vllm_base_url: str = "http://vllm-server:8000/v1"
    vllm_model_name: str = "anrufblocker-v14"

    # Model
    model_active_version: str = "anrufblocker-v14"
    model_dir: str = "/models"

    # Auth
    jwt_secret: str = "change_me"
    admin_user: str = "admin"
    admin_password: str = "change_me"

    # API Key koruması — boş bırakılırsa kontrol atlanır (local dev)
    # Prod'da mutlaka doldur: openssl rand -hex 32
    api_key: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()

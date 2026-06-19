from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    postgres_db: str = "anrufblocker"
    postgres_user: str = "anrufblocker"
    postgres_password: str = "change_me"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    redis_url: str = "redis://redis:6379/0"
    data_dir: str = "/data"
    model_dir: str = "/models"
    agent_backend_url: str = "http://agent-backend:8010"
    api_key: str = ""
    eval_request_timeout_seconds: float = 45.0
    eval_pass_threshold: float = 0.80

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()

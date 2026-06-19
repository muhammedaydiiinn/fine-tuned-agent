from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_db: str = "anrufblocker"
    postgres_user: str = "anrufblocker"
    postgres_password: str = "change_me"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    admin_user: str = "admin"
    admin_password: str = "change_me"
    jwt_secret: str = "change_me"

    agent_backend_url: str = "http://agent-backend:8010"
    model_active_version: str = "anrufblocker-v14"

    # Data directory — no ./data:/data mount on panel; used for config reference only
    data_dir: str = "/data"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()

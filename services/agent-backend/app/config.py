from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    # Project
    project_name: str = "fine-tuned-agent"
    environment: str = "staging"
    log_level: str = "INFO"

    # PostgreSQL
    postgres_db: str = "fine_tuned_agent"
    postgres_user: str = "fine_tuned_agent"
    postgres_password: str = "change_me"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # vLLM
    # "mock" -> local development without GPU; "real" -> GPU server
    vllm_mode: str = "mock"
    vllm_base_url: str = "http://vllm-server:8000/v1"
    vllm_model_name: str = "fine-tuned-agent-v14"
    model_health_timeout_seconds: float = 15.0
    allow_mock_production_deploy: bool = False

    # Model
    model_active_version: str = "fine-tuned-agent-v14"
    model_dir: str = "/models"
    model_merged_path: str = "/models/merged/fine-tuned-agent-v14"

    # Data directory — docker-compose: ./data:/data
    data_dir: str = "/data"

    # Auth
    jwt_secret: str = "change_me"
    admin_user: str = "admin"
    admin_password: str = "change_me"

    # API key guard — skip check if empty (local dev)
    # Production: set with openssl rand -hex 32
    api_key: str = ""
    eval_internal_token: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()

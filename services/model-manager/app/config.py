from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model_dir: str = "/models"
    production_model_path: str = "/models/production/current"
    model_manager_token: str = ""
    docker_service_name: str = "vllm-server"
    docker_project_name: str = ""
    restart_timeout_seconds: int = 30


settings = Settings()

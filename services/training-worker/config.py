from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_db: str = "anrufblocker"
    postgres_user: str = "anrufblocker"
    postgres_password: str = "change_me"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    redis_url: str = "redis://redis:6379/0"

    model_dir: str = "/models"
    data_dir: str = "/data"
    model_active_version: str = "anrufblocker-v14"

    # LoRA hyperparameters (overridable per-job via input_json)
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    training_epochs: int = 3
    training_lr: float = 2e-4
    training_batch_size: int = 4
    gradient_accumulation_steps: int = 4
    max_seq_length: int = 2048
    warmup_ratio: float = 0.05

    # "mock" skips torch imports — useful for unit tests without GPU
    training_mode: str = "real"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()

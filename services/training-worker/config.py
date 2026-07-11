from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    postgres_db: str = "fine_tuned_agent"
    postgres_user: str = "fine_tuned_agent"
    postgres_password: str = "change_me"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    redis_url: str = "redis://redis:6379/0"

    model_dir: str = "/models"
    data_dir: str = "/data"
    model_active_version: str = "fine-tuned-agent-v14"

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

    # Default to mock for the local Docker workflow. GPU hosts should override
    # this to "real" in .env and install requirements-gpu.txt in a matching venv.
    training_mode: str = "mock"
    # Mask loss to assistant tokens only (train on responses, not the fixed
    # system+user context). Real path only; mock path is unaffected.
    train_on_responses_only: bool = True
    candidate_vllm_base_url: str = "http://vllm-candidate:8000/v1"
    candidate_model_name: str = "fine-tuned-agent-candidate"
    candidate_publish_path: str = "/models/candidates/current"
    # Candidate evaluation serves the freshly trained LoRA adapter on the shared
    # production vLLM server (no second 24B model, no downtime). This is the base
    # URL of that shared server; the adapter is mounted at /adapters/<version>.
    candidate_lora_base_url: str = "http://vllm-server:8000/v1"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()

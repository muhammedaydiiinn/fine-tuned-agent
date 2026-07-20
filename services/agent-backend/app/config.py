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
    candidate_vllm_base_url: str = "http://vllm-candidate:8000/v1"
    # Constrain agent policy output to the canonical intent/next_action enums via
    # vLLM structured outputs (response_format json_schema). Keeps model output,
    # guardrails and eval on one taxonomy. Judge/customer-sim calls are untouched.
    policy_guided_decoding: bool = True
    model_health_timeout_seconds: float = 15.0
    vllm_start_timeout_seconds: float = 900.0
    allow_mock_production_deploy: bool = False
    production_model_path: str = "/models/production/current"
    production_served_model_name: str = "fine-tuned-agent-production"
    model_manager_url: str = "http://model-manager:8030"
    model_manager_token: str = ""

    # Model
    model_active_version: str = "fine-tuned-agent-v14"
    model_dir: str = "/models"
    model_merged_path: str = "/models/merged/fine-tuned-agent-v14"

    # Data directory — docker-compose: ./data:/data
    data_dir: str = "/data"

    # Recording upload → transcription (training from audio recordings)
    # "mock" -> upload writes canned segments, no GPU/worker needed;
    # "real" -> jobs go to the transcribe-worker via Redis.
    transcribe_mode: str = "mock"
    recording_max_bytes: int = 300 * 1024 * 1024
    recording_allowed_exts: str = ".wav,.mp3,.m4a,.ogg,.flac,.opus"

    @property
    def recordings_dir(self) -> str:
        return f"{self.data_dir.rstrip('/')}/recordings"

    # Auth
    jwt_secret: str = "change_me"
    admin_user: str = "admin"
    admin_password: str = "change_me"

    # API key guard — skip check if empty (local dev)
    # Production: set with openssl rand -hex 32
    api_key: str = ""
    eval_internal_token: str = ""

    # LLM-as-judge (rubric scoring by the production base model itself)
    judge_enabled: bool = True
    judge_model_name: str = ""  # empty -> production_served_model_name
    judge_temperature: float = 0.1
    judge_max_tokens: int = 400
    judge_pass_threshold: float = 0.7  # soft, visibility only — never gates deploy

    # Natural-language review compiler mode: "auto" (LLM then deterministic
    # fallback), "llm", or "deterministic".
    review_compiler_mode: str = "auto"

    # Auto-train scheduler (background thread in agent-backend lifespan)
    auto_train_enabled: bool = False
    auto_train_threshold: int = 30
    auto_train_check_interval_seconds: int = 900

    # Customer simulator (reactive LLM customer → conversations judged as tests)
    self_base_url: str = "http://localhost:8010"
    sim_customer_temperature: float = 0.5
    sim_max_turns: int = 8
    sim_default_count: int = 12

    # CORS — comma-separated list of allowed origins.
    # Leave empty to allow all origins (local dev only; not suitable for prod).
    # Example: "https://panel.example.com,http://localhost:8020"
    cors_origins: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()

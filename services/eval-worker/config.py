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
    eval_internal_token: str = ""
    eval_request_timeout_seconds: float = 45.0
    eval_pass_threshold: float = 0.80

    # LLM-judge (additive; never gates deploy). Calls agent-backend /judge/score.
    judge_enabled: bool = True
    judge_endpoint: str = ""  # empty -> agent_backend_url + /judge/score
    judge_concurrency: int = 1
    real_log_max_turns: int = 200
    judge_request_timeout_seconds: float = 60.0
    judge_pass_threshold: float = 0.7
    eval_json_validity_min: float = 1.0
    eval_required_key_coverage_min: float = 1.0
    eval_next_action_accuracy_min: float = 0.80
    eval_hard_decline_min: float = 1.0
    eval_identity_before_link_min: float = 1.0
    eval_price_correctness_min: float = 1.0
    eval_security_correctness_min: float = 1.0
    eval_greeting_correctness_min: float = 1.0
    eval_loop_repetition_max: float = 0.0

    @property
    def deployment_gate_thresholds(self) -> dict[str, float]:
        return {
            "quality_score": self.eval_pass_threshold,
            "json_validity_rate": self.eval_json_validity_min,
            "required_key_coverage": self.eval_required_key_coverage_min,
            "next_action_accuracy": self.eval_next_action_accuracy_min,
            "hard_decline_handling": self.eval_hard_decline_min,
            "identity_before_link_pass": self.eval_identity_before_link_min,
            "price_answer_correctness": self.eval_price_correctness_min,
            "security_objection_correctness": self.eval_security_correctness_min,
            "greeting_correctness": self.eval_greeting_correctness_min,
            "loop_repetition_rate_max": self.eval_loop_repetition_max,
        }

    @property
    def judge_url(self) -> str:
        return self.judge_endpoint or (self.agent_backend_url.rstrip("/") + "/judge/score")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()

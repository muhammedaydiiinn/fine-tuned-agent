from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "staging"
    log_level: str = "INFO"

    livekit_url: str = "ws://livekit-server:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "devsecretdevsecretdevsecretdevsecret"
    livekit_agent_name: str = "fine-tuned-agent-voice"

    agent_backend_url: str = "http://agent-backend:8010"
    api_key: str = ""

    whisper_model_path: str = "/models/whisper/whisper-large-v3-turbo-german"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    whisper_language: str = "de"
    whisper_beam_size: int = 1

    speech_rms_threshold: int = 350
    speech_min_ms: int = 250
    speech_end_silence_ms: int = 700
    speech_max_ms: int = 20000
    speech_preroll_ms: int = 240

    tts_mode: str = "fish"
    fish_api_key: str = ""
    fish_tts_reference_id: str = ""
    fish_tts_model: str = "s2-pro"
    fish_tts_url: str = "https://api.fish.audio/v1/tts"
    tts_sample_rate: int = 24000
    tts_request_timeout_seconds: float = 45.0


@lru_cache
def get_settings() -> Settings:
    return Settings()

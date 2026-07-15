import logging
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "staging"
    log_level: str = "INFO"

    livekit_url: str = "ws://livekit-server:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "devsecretdevsecretdevsecretdevsecret"
    livekit_agent_name: str = "anrufblocker-voice"

    agent_backend_url: str = "http://agent-backend:8010"
    api_key: str = ""

    # Backend HTTP timeouts (seconds) — configurable for high-latency environments
    backend_timeout_seconds: float = 120.0
    backend_connect_timeout_seconds: float = 15.0
    backend_circuit_breaker_failures: int = 3
    backend_circuit_breaker_reset_seconds: float = 20.0

    whisper_model_path: str = "/models/whisper/whisper-large-v3-turbo-german"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    whisper_language: str = "de"
    whisper_beam_size: int = 1

    # Batch transcription of uploaded recordings (transcribe_worker)
    redis_url: str = "redis://redis:6379/0"
    transcribe_queue: str = "anruf:transcribe_jobs"
    # Relief valve for GPU memory pressure: e.g. "int8_float16"
    transcribe_compute_type: str = "float16"
    transcribe_max_duration_seconds: float = 7200.0

    speech_rms_threshold: int = 350
    speech_min_ms: int = 250
    speech_end_silence_ms: int = 700
    speech_max_ms: int = 20000
    speech_preroll_ms: int = 240
    utterance_queue_size: int = 8
    duplicate_transcript_window_seconds: float = 2.5
    backchannel_phrases: str = (
        "mhm,hm,hmm,mmh,ja,jaja,ja ja,na ja,naja,na,okay,ok,ah okay,ah ja,"
        "alles klar,verstehe,genau,aha,ah,schon,klar,ja klar,gut,ja gut,"
        "ja natürlich,natürlich,ja genau,ja okay,okay okay,ok ok"
    )
    barge_in_min_ms: int = 800
    # Per-overlap barge-in windows (None = fall back to barge_in_min_ms).
    # playback overlap: shorter so a genuine interruption cuts in faster while
    #   the customer is hearing the agent. The early-interrupt partial path
    #   (see early_interrupt_min_speech_ms) stops real speech even sooner.
    # active_turn overlap: kept conservative so a backend-busy turn is not
    #   discarded; the new utterance is queued and runs after the current turn.
    backchannel_window_ms: int | None = 600
    interrupt_confirm_ms: int | None = 800
    # Before cancelling playback, inspect the buffered audio and only cancel on
    # genuine customer speech. Rejects self-echo (the agent hearing its own
    # playback through the caller's speaker) and ambient noise.
    barge_in_verify_content: bool = True
    # Loud audio that the STT could not decode (short/clipped shouting, speech
    # mixed with echo) is still treated as a real interruption when its RMS is
    # at or above this level. Echo through a speaker is attenuated and stays
    # below it. Compare against speech_rms_threshold (ambient/echo ~ threshold).
    barge_in_loud_rms: int = 1500
    # Loud-but-undecodable audio (STT returned empty) only counts as a real
    # interruption when it is also SUSTAINED for at least this long. An emphatic
    # short "JA!" backchannel is loud but brief, so this guard keeps it from
    # silencing the agent. Measured against segmenter.speech_ms.
    barge_in_loud_min_ms: int = 400
    # Partial transcript settings — realtime customer hearing + early barge-in.
    enable_partial_transcripts: bool = True
    partial_interval_ms: int = 300
    partial_min_speech_ms: int = 400
    early_interrupt_min_speech_ms: int = 500
    # Estimated speaking rate used to reconstruct how much of an interrupted
    # response the customer actually heard (German TTS at normal pace).
    speaking_chars_per_second: float = 14.0
    # Adaptive VAD settings (default OFF — legacy fixed-threshold when False)
    speech_adaptive_vad: bool = False
    speech_noise_floor_margin: float = 2.5
    speech_noise_ema_alpha: float = 0.05
    speech_exit_threshold_ratio: float = 0.6
    # Multi-token backchannel
    backchannel_max_tokens: int = 6
    job_memory_warn_mb: int = 1800

    tts_mode: str = "fish"
    fish_api_key: str = ""
    fish_tts_reference_id: str = ""
    fish_tts_model: str = "s2-pro"
    fish_tts_url: str = "https://api.fish.audio/v1/tts"
    tts_sample_rate: int = 24000
    tts_request_timeout_seconds: float = 45.0
    tts_fallback_to_mock: bool = True


    def validate_runtime(self) -> None:
        """Fail fast at startup if required runtime configuration is missing.

        Raises RuntimeError with a clear message so the container exits immediately
        rather than dying mid-session on the first real request.
        """
        errors: list[str] = []

        if self.tts_mode == "fish":
            if not self.fish_api_key:
                errors.append("FISH_API_KEY is required when TTS_MODE=fish")
            if not self.fish_tts_reference_id:
                errors.append("FISH_TTS_REFERENCE_ID is required when TTS_MODE=fish")

        whisper_path = Path(self.whisper_model_path)
        if not whisper_path.exists():
            msg = f"WHISPER_MODEL_PATH does not exist: {self.whisper_model_path}"
            if self.whisper_device == "cuda":
                errors.append(msg)
            else:
                logger.warning("Config warning: %s", msg)
        elif not whisper_path.is_dir():
            errors.append(
                f"WHISPER_MODEL_PATH is not a directory: {self.whisper_model_path}"
            )
        elif not (whisper_path / "model.bin").is_file():
            msg = (
                "WHISPER_MODEL_PATH must point to a Faster-Whisper/CTranslate2 model "
                f"directory containing model.bin: {self.whisper_model_path}"
            )
            if self.whisper_device == "cuda":
                errors.append(msg)
            else:
                logger.warning("Config warning: %s", msg)

        if errors:
            for msg in errors:
                logger.error("Config validation failed: %s", msg)
            raise RuntimeError(
                "Voice-runtime configuration is incomplete:\n"
                + "\n".join(f"  • {e}" for e in errors)
            )

        logger.info(
            "Config validated — tts_mode=%s whisper_device=%s env=%s",
            self.tts_mode,
            self.whisper_device,
            self.environment,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

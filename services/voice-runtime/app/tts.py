import asyncio
from collections.abc import AsyncIterator

import httpx

from app.config import Settings


def pace_to_speed(pace: str) -> float:
    return {
        "slow": 0.9,
        "normal": 1.0,
        "fast": 1.08,
    }.get(pace.lower(), 1.0)


class FishTTS:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def stream(
        self,
        text: str,
        voice_style: dict,
    ) -> AsyncIterator[bytes]:
        if self.settings.tts_mode == "mock":
            async for chunk in self._mock_stream(text):
                yield chunk
            return
        if not self.settings.fish_api_key:
            raise RuntimeError("FISH_API_KEY is required when TTS_MODE=fish")
        if not self.settings.fish_tts_reference_id:
            raise RuntimeError("FISH_TTS_REFERENCE_ID is required when TTS_MODE=fish")

        payload = {
            "text": text,
            "reference_id": self.settings.fish_tts_reference_id,
            "format": "pcm",
            "sample_rate": self.settings.tts_sample_rate,
            "latency": "low",
            "chunk_length": 150,
            "min_chunk_length": 30,
            "normalize": True,
            "prosody": {
                "speed": pace_to_speed(voice_style.get("pace", "normal")),
                "volume": 0,
                "normalize_loudness": True,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.settings.fish_api_key}",
            "Content-Type": "application/json",
            "model": self.settings.fish_tts_model,
        }
        timeout = httpx.Timeout(self.settings.tts_request_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                self.settings.fish_tts_url,
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk

    async def _mock_stream(self, text: str) -> AsyncIterator[bytes]:
        samples_per_chunk = self.settings.tts_sample_rate // 20
        chunk = b"\x00\x00" * samples_per_chunk
        chunk_count = max(2, min(20, len(text) // 8))
        for _ in range(chunk_count):
            yield chunk
            await asyncio.sleep(0.05)

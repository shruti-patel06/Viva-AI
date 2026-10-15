"""Speech, behind our own interface. Text to speech now; speech to text arrives in stage 6.

The rest of the code calls synthesize() and never names Sarvam, so a different voice
provider means changing this file only.
"""

import base64

import httpx

from app.ai.llm import RateLimited
from app.core.config import get_settings

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"


def synthesize(text: str) -> bytes:
    """Speak `text` in Indian English. Returns MP3 bytes."""
    settings = get_settings()
    if not settings.sarvam_api_key:
        raise RuntimeError("Question audio needs SARVAM_API_KEY in .env")
    body = {
        "text": text,
        "language_code": "en-IN",
        "model": settings.sarvam_tts_model,
        "output_audio_codec": "mp3",
    }
    if settings.sarvam_speaker:
        body["speaker"] = settings.sarvam_speaker
    response = httpx.post(
        SARVAM_TTS_URL,
        headers={"api-subscription-key": settings.sarvam_api_key},
        json=body,
        timeout=60,
    )
    if response.status_code == 429:
        raise RateLimited(float(response.headers.get("retry-after", 30)))
    response.raise_for_status()
    return base64.b64decode(response.json()["audios"][0])

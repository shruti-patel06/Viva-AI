"""Speech, behind our own interface: text to speech for questions, speech to text for answers.

The rest of the code calls synthesize() and transcribe() and never names Sarvam, so a
different voice provider means changing this file only.
"""

import base64

import httpx

from app.ai.llm import RateLimited
from app.core.config import get_settings

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
# A student is waiting on this call, so it gives up quickly and the page offers typing.
TRANSCRIBE_TIMEOUT_SECONDS = 20


class SpeechUnavailable(Exception):
    """Speech to text could not be done right now: the service is down, slow, out of credit
    or refusing requests. Typing still works, so this is never the end of an answer."""


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


def transcribe(audio: bytes, content_type: str) -> str:
    """Turn a spoken answer (up to 30 seconds, any common format) into Indian English text."""
    settings = get_settings()
    if not settings.sarvam_api_key:
        raise SpeechUnavailable("Speech to text is not set up (SARVAM_API_KEY is empty)")
    try:
        response = httpx.post(
            SARVAM_STT_URL,
            headers={"api-subscription-key": settings.sarvam_api_key},
            files={"file": ("answer", audio, content_type)},
            data={"model": settings.sarvam_stt_model, "language_code": "en-IN"},
            timeout=TRANSCRIBE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as error:
        raise SpeechUnavailable(f"Could not reach the speech service: {type(error).__name__}")
    if response.status_code == 429:
        raise SpeechUnavailable("The speech service is busy (too many requests)")
    if response.status_code >= 400:
        raise SpeechUnavailable(f"The speech service refused the recording ({response.status_code}): {response.text[:200]}")
    return (response.json().get("transcript") or "").strip()

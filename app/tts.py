"""VoiceLab (voicelab.uz) text-to-speech: matn -> 24 kHz mono WAV."""

import asyncio
import io
import uuid
import wave

import httpx

from . import config

BASE_URL = "https://api.voicelab.uz/v1"


class TTSError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(config.VOICELAB_API_KEY)


async def synthesize(text: str, voice_id: str = "", language: str = "uz", speed: float = 1.0) -> bytes:
    """Return WAV bytes for the given text."""
    headers = {
        "Authorization": f"Bearer {config.VOICELAB_API_KEY}",
        "Idempotency-Key": f"tg-{uuid.uuid4().hex}",
    }
    body = {
        "text": text,
        "language": language,
        "voice_id": voice_id or config.VOICELAB_VOICE_ID,
        "speed": speed,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{BASE_URL}/tts", json=body, headers=headers)
        if resp.status_code == 200:
            return resp.content
        if resp.status_code == 202:
            # Uzun matn: fon rejimida tayyorlanadi, tayyor bo'lguncha kutamiz
            gen_id = resp.json()["id"]
            for _ in range(60):
                await asyncio.sleep(2)
                status = await client.get(f"{BASE_URL}/tts/generations/{gen_id}", headers=headers)
                data = status.json()
                if data.get("status") == "completed":
                    audio = await client.get(data["audio_url"])
                    audio.raise_for_status()
                    return audio.content
                if data.get("status") in ("failed", "canceled"):
                    raise TTSError(f"VoiceLab: {data}")
            raise TTSError("VoiceLab: kutish vaqti tugadi")
        raise TTSError(f"VoiceLab {resp.status_code}: {resp.text[:300]}")


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


def build_track(clips: list[tuple[float, bytes]], total_seconds: float) -> bytes:
    """Place (start_seconds, wav_bytes) clips onto one silent WAV track."""
    params = None
    track = bytearray()
    for start, data in clips:
        with wave.open(io.BytesIO(data)) as w:
            if params is None:
                params = w.getparams()
                frame_size = params.sampwidth * params.nchannels
                track = bytearray(int(total_seconds * params.framerate) * frame_size)
            frames = w.readframes(w.getnframes())
        offset = int(start * params.framerate) * frame_size
        end = min(len(track), offset + len(frames))
        track[offset:end] = frames[: end - offset]
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(bytes(track))
    return out.getvalue()

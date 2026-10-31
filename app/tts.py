"""Text-to-speech: VoiceLab (voicelab.uz) asosiy, Edge TTS bepul zaxira. Natija: 24 kHz mono WAV."""

import asyncio
import io
import logging
import subprocess
import time
import uuid
import wave

import edge_tts
import httpx

from . import config

log = logging.getLogger(__name__)

BASE_URL = "https://api.voicelab.uz/v1"

# VoiceLab ayol ovozlari: Edge zaxirasida ham ayol ovozi tanlanadi
FEMALE_VOICES = {
    "voice_4ef06e6b-2229-4023-ad66-c2a7a2b736ff",
    "voice_3602ec76-5393-43b6-9e0d-784328cb77ba",
    "voice_7b350d91-4e56-4bc3-869c-8cd6ba646c3c",
    "voice_f7179d1c-07ab-4b3d-b5be-84dcf2935388",
    "voice_47260d60-5635-4fb0-904d-0f3c19c04ce6",
    "voice_83a39d68-22bc-4062-b86e-0a9dd998ba7a",
}
EDGE_VOICES = {
    ("uz", "male"): "uz-UZ-SardorNeural",
    ("uz", "female"): "uz-UZ-MadinaNeural",
    ("ru", "male"): "ru-RU-DmitryNeural",
    ("ru", "female"): "ru-RU-SvetlanaNeural",
    ("en", "male"): "en-US-AndrewNeural",
    ("en", "female"): "en-US-AvaNeural",
}


class TTSError(RuntimeError):
    pass


def is_configured() -> bool:
    return True  # Edge TTS kalitsiz ishlaydi


async def _voicelab(client: httpx.AsyncClient, text: str, voice_id: str, language: str) -> bytes:
    headers = {
        "Authorization": f"Bearer {config.VOICELAB_API_KEY}",
        "Idempotency-Key": f"tg-{uuid.uuid4().hex}",
    }
    body = {"text": text, "language": language, "voice_id": voice_id or config.VOICELAB_VOICE_ID}
    resp = await client.post(f"{BASE_URL}/tts", json=body, headers=headers)
    if resp.status_code == 200:
        return resp.content
    if resp.status_code == 202:
        # Uzun matn: fon rejimida tayyorlanadi, tayyor bo'lguncha kutamiz
        gen_id = resp.json()["id"]
        for _ in range(60):
            await asyncio.sleep(2)
            data = (await client.get(f"{BASE_URL}/tts/generations/{gen_id}", headers=headers)).json()
            if data.get("status") == "completed":
                audio = await client.get(data["audio_url"])
                audio.raise_for_status()
                return audio.content
            if data.get("status") in ("failed", "canceled"):
                raise TTSError(f"VoiceLab: {data}")
        raise TTSError("VoiceLab: kutish vaqti tugadi")
    try:
        message = resp.json()["error"]["message"]
    except Exception:
        message = resp.text[:200]
    raise TTSError(f"VoiceLab {resp.status_code}: {message}")


def _to_wav(audio: bytes) -> bytes:
    """Any audio -> 24 kHz mono 16-bit WAV (so clips can be mixed into one track)."""
    # Xom PCM olamiz: pipe'ga yozilgan WAV sarlavhasida uzunlik noto'g'ri bo'ladi
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ar", "24000", "-ac", "1",
         "-f", "s16le", "pipe:1"],
        input=audio, capture_output=True, check=True,
    )
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(proc.stdout)
    return out.getvalue()


async def _edge(text: str, voice_id: str, language: str) -> bytes:
    gender = "female" if voice_id in FEMALE_VOICES else "male"
    voice = voice_id if voice_id.count("-") >= 2 and voice_id.endswith("Neural") else (
        EDGE_VOICES.get((language, gender), EDGE_VOICES[("uz", gender)])
    )
    mp3 = b""
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            mp3 += chunk["data"]
    if not mp3:
        raise TTSError("Edge TTS ovoz qaytarmadi")
    return await asyncio.to_thread(_to_wav, mp3)


async def synthesize_many(texts: list[str], voice_id: str = "", language: str = "uz") -> tuple[list[bytes | None], str]:
    """Synthesize several texts. Returns (wav clips, provider name). Empty texts -> None."""
    use_voicelab = (
        config.TTS_PROVIDER in ("auto", "voicelab")
        and config.VOICELAB_API_KEY
        and not voice_id.endswith("Neural")
    )
    if use_voicelab:
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                clips = await asyncio.gather(
                    *(_voicelab(client, t, voice_id, language) if t else asyncio.sleep(0, None) for t in texts)
                )
            return [await asyncio.to_thread(_to_wav, c) if c else None for c in clips], "VoiceLab"
        except (TTSError, httpx.HTTPError) as e:
            if config.TTS_PROVIDER == "voicelab":
                raise TTSError(str(e)) from e
            log.warning("VoiceLab ishlamadi, Edge TTS'ga o'tildi: %s", e)
    clips = await asyncio.gather(
        *(_edge(t, voice_id, language) if t else asyncio.sleep(0, None) for t in texts)
    )
    return list(clips), "Edge TTS"


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


def build_track(clips: list[tuple[float, bytes]], total_seconds: float) -> bytes:
    """Place (start_seconds, wav_bytes) clips onto one silent WAV track (all clips 24 kHz mono)."""
    rate, width = 24000, 2
    track = bytearray(int(total_seconds * rate) * width)
    for start, data in clips:
        with wave.open(io.BytesIO(data)) as w:
            frames = w.readframes(w.getnframes())
        offset = int(start * rate) * width
        end = min(len(track), offset + len(frames))
        track[offset:end] = frames[: end - offset]
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(track))
    return out.getvalue()


# ---------- O'zbekcha ovozlar katalogi ----------

FREE_VOICES = [
    {"id": "uz-UZ-SardorNeural", "name": "Sardor (bepul)", "gender": "male", "description": "Edge TTS", "free": True},
    {"id": "uz-UZ-MadinaNeural", "name": "Madina (bepul)", "gender": "female", "description": "Edge TTS", "free": True},
]
_voice_cache: tuple[float, list[dict]] | None = None


async def list_uzbek_voices(fresh: bool = False) -> list[dict]:
    """All Uzbek VoiceLab voices (cached 5 min) followed by the free Edge voices."""
    global _voice_cache
    if _voice_cache and not fresh and time.time() - _voice_cache[0] < 300:
        return _voice_cache[1]
    voices: list[dict] = []
    if config.VOICELAB_API_KEY:
        headers = {"Authorization": f"Bearer {config.VOICELAB_API_KEY}"}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                page = 1
                while page:
                    data = (await client.get(f"{BASE_URL}/voices", params={"page": page}, headers=headers)).json()
                    for v in data.get("data", []):
                        if v.get("language") == "uz":
                            voices.append({
                                "id": v["id"],
                                "name": v.get("display_name") or v["name"],
                                "gender": v.get("gender") or "",
                                "description": v.get("short_description") or v.get("category") or "",
                                "audio_url": v.get("audio_url"),
                                "free": False,
                            })
                    page = data.get("pagination", {}).get("next_page")
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("VoiceLab ovozlar ro'yxati olinmadi: %s", e)
    voices += FREE_VOICES
    for v in voices:
        if v["gender"] == "female":
            FEMALE_VOICES.add(v["id"])
    _voice_cache = (time.time(), voices)
    return voices


async def voice_sample(voice: dict) -> bytes:
    """Short MP3 sample of a voice (VoiceLab preview or generated Edge sample)."""
    if voice.get("free"):
        wav = await _edge("Assalomu alaykum! Men sizning videolaringizni ovozlashtiraman.", voice["id"], "uz")
    else:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(voice["audio_url"])
            resp.raise_for_status()
            wav = resp.content
    proc = await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-b:a", "96k", "-f", "mp3", "pipe:1"],
        input=wav, capture_output=True, check=True,
    )
    return proc.stdout

"""Text-to-speech. Asosiy: Microsoft Neural o'zbekcha ovozlar (Edge TTS, bepul, ravon) +
so'zma-so'z vaqtlar. Ixtiyoriy: VoiceLab (TTS_PROVIDER=voicelab). Natija: 24 kHz mono WAV."""

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

# Microsoft Neural ovozlari asosida uslublar (tezlik va ohang sozlangan)
VOICE_PRESETS = [
    {"id": "sardor", "name": "Sardor", "voice": "uz-UZ-SardorNeural", "rate": "+5%", "pitch": "+0Hz",
     "gender": "male", "description": "ravon, tabiiy diktor"},
    {"id": "sardor-energik", "name": "Sardor · energik", "voice": "uz-UZ-SardorNeural", "rate": "+13%",
     "pitch": "+3Hz", "gender": "male", "description": "tez, reklama uslubi"},
    {"id": "sardor-chuqur", "name": "Sardor · chuqur", "voice": "uz-UZ-SardorNeural", "rate": "-3%",
     "pitch": "-7Hz", "gender": "male", "description": "past, jiddiy, hikoyachi"},
    {"id": "madina", "name": "Madina", "voice": "uz-UZ-MadinaNeural", "rate": "+5%", "pitch": "+0Hz",
     "gender": "female", "description": "ravon, iliq diktor"},
    {"id": "madina-quvnoq", "name": "Madina · quvnoq", "voice": "uz-UZ-MadinaNeural", "rate": "+11%",
     "pitch": "+4Hz", "gender": "female", "description": "yorqin, energik"},
    {"id": "madina-yumshoq", "name": "Madina · yumshoq", "voice": "uz-UZ-MadinaNeural", "rate": "-4%",
     "pitch": "-3Hz", "gender": "female", "description": "sokin, his-tuyg'uli"},
]
PRESETS = {p["id"]: p for p in VOICE_PRESETS}
OTHER_LANG = {
    ("ru", "male"): "ru-RU-DmitryNeural", ("ru", "female"): "ru-RU-SvetlanaNeural",
    ("en", "male"): "en-US-AndrewNeural", ("en", "female"): "en-US-AvaNeural",
}
FEMALE_VOICES: set[str] = set()

# so'z vaqtlari: (matn, boshlanish_s, tugash_s)
WordTimes = list[tuple[str, float, float]]


class TTSError(RuntimeError):
    pass


def is_configured() -> bool:
    return True


def _use_voicelab(voice_id: str) -> bool:
    return (config.TTS_PROVIDER == "voicelab" and bool(config.VOICELAB_API_KEY)
            and voice_id.startswith("voice_"))


# ---------- Microsoft Neural (Edge TTS) ----------

def _resolve(voice_id: str, language: str) -> dict:
    if voice_id in PRESETS:
        preset = PRESETS[voice_id]
    elif voice_id.endswith("Neural"):
        preset = {"voice": voice_id, "rate": "+5%", "pitch": "+0Hz",
                  "gender": "female" if "Madina" in voice_id else "male"}
    else:
        female = voice_id in FEMALE_VOICES
        preset = PRESETS[config.TTS_VOICE if config.TTS_VOICE in PRESETS else "sardor"]
        if voice_id:
            preset = PRESETS["madina" if female else "sardor"]
    if language != "uz":
        preset = {**preset, "voice": OTHER_LANG.get((language, preset["gender"]), preset["voice"])}
    return preset


def _to_wav(audio: bytes) -> bytes:
    """Any audio -> 24 kHz mono 16-bit WAV (so clips can be mixed into one track)."""
    # Xom PCM olamiz: pipe'ga yozilgan WAV sarlavhasida uzunlik noto'g'ri bo'ladi
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ar", "24000", "-ac", "1", "-f", "s16le", "pipe:1"],
        input=audio, capture_output=True, check=True,
    )
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(proc.stdout)
    return out.getvalue()


async def _edge(text: str, voice_id: str, language: str) -> tuple[bytes, WordTimes]:
    p = _resolve(voice_id, language)
    mp3, words = b"", []
    for attempt in range(3):
        try:
            com = edge_tts.Communicate(text, p["voice"], rate=p["rate"], pitch=p["pitch"], boundary="WordBoundary")
            mp3, words = b"", []
            async for chunk in com.stream():
                if chunk["type"] == "audio":
                    mp3 += chunk["data"]
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 1e7
                    words.append((chunk["text"], start, start + chunk["duration"] / 1e7))
            if mp3:
                break
        except Exception as e:  # tarmoq uzilishi: qayta urinamiz
            log.warning("Edge TTS urinish %d: %s", attempt + 1, e)
            await asyncio.sleep(1.5)
    if not mp3:
        raise TTSError("Edge TTS ovoz qaytarmadi")
    return await asyncio.to_thread(_to_wav, mp3), words


# ---------- VoiceLab (ixtiyoriy) ----------

async def _voicelab(client: httpx.AsyncClient, text: str, voice_id: str, language: str) -> bytes:
    headers = {"Authorization": f"Bearer {config.VOICELAB_API_KEY}", "Idempotency-Key": f"tg-{uuid.uuid4().hex}"}
    body = {"text": text, "language": language, "voice_id": voice_id}
    resp = await client.post(f"{BASE_URL}/tts", json=body, headers=headers)
    if resp.status_code == 200:
        return resp.content
    if resp.status_code == 202:
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


# ---------- Fish Audio: foydalanuvchining klon ovozi ----------

MY_VOICE_ID = "men"


def fish_available() -> bool:
    return bool(config.FISH_API_KEY and config.FISH_REFERENCE_AUDIO and config.FISH_REFERENCE_AUDIO.exists()
                and config.FISH_REFERENCE_TEXT and config.FISH_REFERENCE_TEXT.exists())


_fish_ref: tuple[bytes, str] | None = None


def _fish_reference() -> tuple[bytes, str]:
    """Reference sample (denoised 44.1 kHz WAV, leading silence trimmed) + its transcript.
    Foydalanuvchi sinovda shu variantni (C_shovqinsiz) eng yaxshi deb tanladi; loudnorm buzilish berardi."""
    global _fish_ref
    if _fish_ref is None:
        proc = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", str(config.FISH_REFERENCE_AUDIO), "-af",
             "highpass=f=80,afftdn=nf=-25,silenceremove=start_periods=1:start_threshold=-45dB",
             "-ar", "44100", "-ac", "1", "-f", "wav", "pipe:1"],
            capture_output=True, check=True)
        wav = _to_wav_rate(proc.stdout, 44100)
        _fish_ref = (wav, config.FISH_REFERENCE_TEXT.read_text(encoding="utf-8").strip())
    return _fish_ref


def _to_wav_rate(audio: bytes, rate: int) -> bytes:
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ar", str(rate), "-ac", "1", "-f", "s16le", "pipe:1"],
        input=audio, capture_output=True, check=True)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(proc.stdout)
    return out.getvalue()


def estimate_word_times(text: str, wav: bytes) -> WordTimes:
    """Word timings without ASR: find pauses in the audio and match them to punctuation breaks,
    then spread words inside each phrase by length."""
    import re

    import numpy as np

    with wave.open(io.BytesIO(wav)) as w:
        rate = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    hop = int(rate * 0.02)
    frames = len(pcm) // hop
    if frames == 0:
        return []
    energy = np.sqrt((pcm[: frames * hop].reshape(frames, hop) ** 2).mean(axis=1) + 1e-9)
    db = 20 * np.log10(energy)
    voiced = db > max(db.max() - 35, -50)
    idx = np.flatnonzero(voiced)
    if not len(idx):
        return []
    speech_start, speech_end = idx[0] * 0.02, (idx[-1] + 1) * 0.02
    # pauzalar: >= 0.16 s jimlik
    pauses, run = [], None
    for i in range(idx[0], idx[-1] + 1):
        if not voiced[i]:
            run = i if run is None else run
        elif run is not None:
            if (i - run) * 0.02 >= 0.16:
                pauses.append((run * 0.02, i * 0.02))
            run = None

    words = text.split()
    phrases, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[.,!?;:—…]$", w):
            phrases.append(cur)
            cur = []
    if cur:
        phrases.append(cur)
    # eng uzun pauzalarni tinish belgilariga biriktiramiz (vaqt tartibida)
    n_breaks = len(phrases) - 1
    chosen = sorted(sorted(pauses, key=lambda p: p[1] - p[0], reverse=True)[:n_breaks])
    if len(chosen) < n_breaks:  # pauza kam: hammasini bitta ibora deb olamiz
        phrases, chosen = [words], []
    bounds = [speech_start] + [x for p in chosen for x in p] + [speech_end]
    out: WordTimes = []
    for k, phrase in enumerate(phrases):
        a, b = bounds[2 * k], bounds[2 * k + 1]
        weights = [len(w) + 2 for w in phrase]
        total, t = sum(weights), a
        for w, wt in zip(phrase, weights):
            d = (b - a) * wt / total
            out.append((w, t, t + d))
            t += d
    return out


async def _fish(client: httpx.AsyncClient, text: str) -> tuple[bytes, WordTimes]:
    import msgpack

    ref_audio, ref_text = await asyncio.to_thread(_fish_reference)
    body = {
        "text": text,
        "references": [{"audio": ref_audio, "text": ref_text}],
        "format": "wav",
        "latency": "normal",
        "normalize": False,
        "temperature": config.FISH_TEMPERATURE,
        "top_p": config.FISH_TOP_P,
        "prosody": {"speed": config.FISH_SPEED},
    }
    headers = {"Authorization": f"Bearer {config.FISH_API_KEY}", "Content-Type": "application/msgpack",
               "model": config.FISH_MODEL}
    for attempt in range(3):
        resp = await client.post("https://api.fish.audio/v1/tts", content=msgpack.packb(body, use_bin_type=True),
                                 headers=headers)
        if resp.status_code == 200 and resp.content:
            wav = await asyncio.to_thread(_to_wav, resp.content)
            return wav, estimate_word_times(text, wav)
        if resp.status_code in (429, 500, 502, 503) and attempt < 2:
            await asyncio.sleep(2 + attempt * 3)
            continue
        raise TTSError(f"Fish Audio {resp.status_code}: {resp.text[:200]}")
    raise TTSError("Fish Audio javob bermadi")


async def synthesize_many(texts: list[str], voice_id: str = "", language: str = "uz"
                          ) -> tuple[list[bytes | None], list[WordTimes | None], str]:
    """Synthesize several texts -> (wav clips, word timings, provider). Empty texts -> None."""
    want_mine = voice_id == MY_VOICE_ID or (not voice_id and config.TTS_VOICE == MY_VOICE_ID)
    if want_mine and fish_available():
        try:
            sem = asyncio.Semaphore(3)  # bepul tarifda ko'p parallel so'rov yubormaymiz

            async def one(client, t):
                async with sem:
                    return await _fish(client, t)

            async with httpx.AsyncClient(timeout=180) as client:
                results = await asyncio.gather(
                    *(one(client, t) if t else asyncio.sleep(0, (None, None)) for t in texts))
            return [r[0] for r in results], [r[1] for r in results], "Sizning ovozingiz (Fish Audio)"
        except (TTSError, httpx.HTTPError, OSError) as e:
            log.warning("Fish Audio ishlamadi, Microsoft Neural'ga o'tildi: %s", e)
            voice_id = ""
    elif want_mine:
        voice_id = ""
    if _use_voicelab(voice_id):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                clips = await asyncio.gather(
                    *(_voicelab(client, t, voice_id, language) if t else asyncio.sleep(0, None) for t in texts))
            wavs = [await asyncio.to_thread(_to_wav, c) if c else None for c in clips]
            return wavs, [None] * len(texts), "VoiceLab"
        except (TTSError, httpx.HTTPError) as e:
            log.warning("VoiceLab ishlamadi, Microsoft Neural'ga o'tildi: %s", e)
    results = await asyncio.gather(
        *(_edge(t, voice_id, language) if t else asyncio.sleep(0, (None, None)) for t in texts))
    return [r[0] for r in results], [r[1] for r in results], "Microsoft Neural"


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

_voice_cache: tuple[float, list[dict]] | None = None


async def list_uzbek_voices(fresh: bool = False) -> list[dict]:
    """Uzbek voice styles (Microsoft Neural presets; VoiceLab voices too if TTS_PROVIDER=voicelab)."""
    global _voice_cache
    if _voice_cache and not fresh and time.time() - _voice_cache[0] < 300:
        return _voice_cache[1]
    voices = [{**p, "free": True} for p in VOICE_PRESETS]
    if fish_available():
        voices.insert(0, {"id": MY_VOICE_ID, "name": "Mening ovozim", "gender": "", "free": True,
                          "description": "foydalanuvchining o'z klon ovozi (Fish Audio)"})
    if config.TTS_PROVIDER == "voicelab" and config.VOICELAB_API_KEY:
        headers = {"Authorization": f"Bearer {config.VOICELAB_API_KEY}"}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                page = 1
                while page:
                    data = (await client.get(f"{BASE_URL}/voices", params={"page": page}, headers=headers)).json()
                    for v in data.get("data", []):
                        if v.get("language") == "uz":
                            voices.append({
                                "id": v["id"], "name": v.get("display_name") or v["name"],
                                "gender": v.get("gender") or "",
                                "description": v.get("short_description") or v.get("category") or "",
                                "audio_url": v.get("audio_url"), "free": False,
                            })
                            if v.get("gender") == "female":
                                FEMALE_VOICES.add(v["id"])
                    page = data.get("pagination", {}).get("next_page")
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("VoiceLab ovozlar ro'yxati olinmadi: %s", e)
    _voice_cache = (time.time(), voices)
    return voices


async def voice_sample(voice: dict) -> bytes:
    """Short MP3 sample of a voice."""
    if voice["id"] == MY_VOICE_ID:
        clips, _t, _p = await synthesize_many(
            ["Assalomu alaykum! Bu mening ovozim. Videolaringizni shu ovozda tayyorlayman."], MY_VOICE_ID)
        wav = clips[0]
    elif voice.get("free"):
        wav, _ = await _edge("Assalomu alaykum! Bugun sizga juda qiziqarli narsani ko'rsataman. Qani, boshladik!",
                             voice["id"], "uz")
    else:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(voice["audio_url"])
            resp.raise_for_status()
            wav = resp.content
    proc = await asyncio.to_thread(
        subprocess.run, ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-b:a", "96k", "-f", "mp3", "pipe:1"],
        input=wav, capture_output=True, check=True,
    )
    return proc.stdout

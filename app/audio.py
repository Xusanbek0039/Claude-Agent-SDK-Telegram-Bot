"""Dublaj miksi: diktor ovozi + sintez qilingan fon musiqasi + animatsiyaga mos ovoz effektlari.

Musiqa va effektlar kodda sintez qilinadi (mualliflik huquqi muammosi yo'q).
Diktor gapirganda musiqa avtomatik pasayadi (ducking).
"""

import io
import wave

import numpy as np

SR = 44100
rng = np.random.default_rng(11)

MAJOR, MINOR = (0, 4, 7), (0, 3, 7)
NOTE = {"C": 48, "D": 50, "E": 52, "F": 53, "G": 55, "A": 57, "Bb": 58, "B": 59}

MOODS = {
    # tech: qorong'i, texnologik (terminal)
    "tech": {"bpm": 104, "prog": [("A", MINOR), ("F", MAJOR), ("C", MAJOR), ("G", MAJOR)],
             "drums": "soft", "arp": True, "level": 0.20},
    # bright: yorqin, ijobiy (glass, ta'lim)
    "bright": {"bpm": 112, "prog": [("C", MAJOR), ("G", MAJOR), ("A", MINOR), ("F", MAJOR)],
               "drums": "soft", "arp": True, "level": 0.19},
    # upbeat: energik, reklama (slides, classic)
    "upbeat": {"bpm": 120, "prog": [("F", MAJOR), ("C", MAJOR), ("G", MAJOR), ("A", MINOR)],
               "drums": "full", "arp": True, "level": 0.19},
    # emotional: sekin, his-tuyg'uli (kinetic)
    "emotional": {"bpm": 72, "prog": [("A", MINOR), ("F", MAJOR), ("C", MAJOR), ("G", MAJOR)],
                  "drums": None, "arp": True, "level": 0.22},
    # calm: tinch fon
    "calm": {"bpm": 88, "prog": [("D", MINOR), ("Bb", MAJOR), ("F", MAJOR), ("C", MAJOR)],
             "drums": None, "arp": False, "level": 0.20},
}
THEME_MOOD = {"terminal": "tech", "glass": "bright", "slides": "upbeat", "kinetic": "emotional", "classic": "upbeat"}


def _freq(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * SR)) / SR


def _env(n: int, attack: float, release: float) -> np.ndarray:
    e = np.ones(n, dtype=np.float32)
    a, r = min(n, int(attack * SR)), min(n, int(release * SR))
    if a:
        e[:a] = np.linspace(0, 1, a)
    if r:
        e[-r:] *= np.linspace(1, 0, r)
    return e


def _soft_saw(freq: float, t: np.ndarray, harmonics: int = 7, detune: float = 0.0) -> np.ndarray:
    out = np.zeros_like(t)
    for h in range(1, harmonics + 1):
        out += np.sin(2 * np.pi * freq * (1 + detune) * h * t) / h ** 1.4
    return out


def _lowpass(x: np.ndarray, k: int) -> np.ndarray:
    """Cheap lowpass: moving average of k samples."""
    if k <= 1:
        return x
    return np.convolve(x, np.ones(k) / k, mode="same")


def _add(buf: np.ndarray, sig: np.ndarray, at: float, gain: float = 1.0) -> None:
    """Mix a mono or stereo signal into the stereo buffer at time `at`."""
    i = int(at * SR)
    if i >= len(buf) or i + len(sig) <= 0:
        return
    if sig.ndim == 1:
        sig = np.stack([sig, sig], axis=1)
    s0 = max(0, -i)
    i = max(0, i)
    n = min(len(sig) - s0, len(buf) - i)
    buf[i : i + n] += sig[s0 : s0 + n] * gain


# ---------- Musiqa ----------

def music(duration: float, mood: str = "tech") -> np.ndarray:
    m = MOODS.get(mood, MOODS["tech"])
    beat = 60 / m["bpm"]
    bar = beat * 4
    buf = np.zeros((int(duration * SR) + SR, 2), dtype=np.float32)
    n_bars = int(duration / bar) + 2
    for b in range(n_bars):
        root_name, quality = m["prog"][b % len(m["prog"])]
        root = NOTE[root_name]
        start = b * bar
        # Pad: yumshoq akkord, chap/o'ng biroz farqli (keng stereo)
        t = _t(bar + 0.6)
        pad = np.zeros((len(t), 2), dtype=np.float32)
        for iv in quality:
            f = _freq(root + 12 + iv)
            pad[:, 0] += _soft_saw(f, t, 6, -0.003)
            pad[:, 1] += _soft_saw(f, t, 6, 0.003)
        pad *= _env(len(t), 0.35, 0.6)[:, None] * 0.07
        _add(buf, pad, start)
        # Bas: sakkizlik notalar
        for k in range(8):
            tb = _t(beat / 2)
            f = _freq(root - 12)
            bass = (np.sin(2 * np.pi * f * tb) + 0.3 * np.sin(4 * np.pi * f * tb)) * np.exp(-tb * 6)
            _add(buf, bass * 0.16, start + k * beat / 2)
        # Arpedjio: akkord notalari, plucked
        if m["arp"]:
            notes = [root + 24 + iv for iv in quality] + [root + 36]
            step = beat / 2 if m["bpm"] < 90 else beat / 4
            for k in range(int(bar / step)):
                tp = _t(0.45)
                f = _freq(notes[k % len(notes)])
                pl = (np.sin(2 * np.pi * f * tp) + 0.25 * np.sin(4 * np.pi * f * tp)) * np.exp(-tp * 9)
                pan = 0.5 + 0.35 * np.sin(k)
                _add(buf, np.stack([pl * (1 - pan), pl * pan], axis=1) * 0.07, start + k * step)
        # Barabanlar
        if m["drums"]:
            for k in range(4):
                tk = _t(0.3)
                kick = np.sin(2 * np.pi * (45 + 90 * np.exp(-tk * 30)) * tk) * np.exp(-tk * 12)
                _add(buf, kick * 0.32, start + k * beat)
                hat = np.diff(rng.standard_normal(int(0.05 * SR) + 1)) * np.exp(-_t(0.05) * 90)
                _add(buf, hat * 0.025, start + k * beat + beat / 2)
                if m["drums"] == "full" and k in (1, 3):
                    tc = _t(0.18)
                    clap = _lowpass(rng.standard_normal(len(tc)), 3) * np.exp(-tc * 22)
                    _add(buf, clap * 0.07, start + k * beat)
    buf = buf[: int(duration * SR)]
    fade = _env(len(buf), 1.0, 2.0)
    return buf * fade[:, None]


# ---------- Ovoz effektlari ----------

def whoosh(dur: float = 0.55, bright: float = 1.0) -> np.ndarray:
    n = int(dur * SR)
    noise = rng.standard_normal(n)
    lo = _lowpass(noise, 24)
    hi = noise - _lowpass(noise, 4)
    x = np.linspace(0, 1, n)
    sweep = lo * (1 - x) + hi * x * 0.5 * bright
    shape = np.sin(np.pi * x) ** 2
    pan = x  # chapdan o'ngga uchib o'tadi
    s = sweep * shape * 0.6
    return np.stack([s * (1 - pan * 0.7), s * (0.3 + pan * 0.7)], axis=1)


def pop(pitch: float = 1.0) -> np.ndarray:
    t = _t(0.12)
    f = 520 * pitch * (1 + 1.2 * np.exp(-t * 60))
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 38) * 0.5


def tick() -> np.ndarray:
    n = int(0.018 * SR)
    noise = rng.standard_normal(n)
    return (noise - _lowpass(noise, 3)) * np.exp(-_t(0.018) * 260) * 0.35


def ding() -> np.ndarray:
    t = _t(0.9)
    return (np.sin(2 * np.pi * 1318 * t) + 0.4 * np.sin(2 * np.pi * 1976 * t)) * np.exp(-t * 5) * 0.22


def riser(dur: float = 1.2) -> np.ndarray:
    t = _t(dur)
    x = t / dur
    noise = rng.standard_normal(len(t))
    tone = np.sin(2 * np.pi * np.cumsum(200 + 900 * x ** 2) / SR)
    return (noise * 0.25 + tone * 0.2) * x ** 2


def impact() -> np.ndarray:
    t = _t(1.4)
    boom = np.sin(2 * np.pi * (38 + 60 * np.exp(-t * 8)) * t) * np.exp(-t * 3.5)
    crack = _lowpass(rng.standard_normal(len(t)), 2) * np.exp(-t * 30)
    return boom * 0.8 + crack * 0.25


SFX = {
    "whoosh": (lambda: whoosh(), 0.38),
    "swoosh": (lambda: whoosh(0.35, 1.6), 0.18),
    "pop": (lambda: pop(rng.uniform(0.9, 1.25)), 0.30),
    "tick": (lambda: tick(), 0.30),
    "ding": (lambda: ding(), 0.5),
    "riser": (lambda: riser(), 0.30),
    "impact": (lambda: impact(), 0.45),
}


# ---------- Miks ----------

def _voice_to_stereo(wav_bytes: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate, ch = w.getframerate(), w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if ch == 2:
        data = data.reshape(-1, 2).mean(axis=1)
    if rate != SR:
        x_old = np.arange(len(data)) / rate
        x_new = np.arange(int(len(data) * SR / rate)) / SR
        data = np.interp(x_new, x_old, data)
    peak = np.abs(data).max() or 1.0
    data = data * (0.92 / peak)
    return np.stack([data, data], axis=1).astype(np.float32)


def _duck_gain(voice: np.ndarray, n: int) -> np.ndarray:
    """Music gain envelope: drops while the narrator speaks."""
    env = np.zeros(n, dtype=np.float32)
    v = np.abs(voice[:n, 0]) if len(voice) else np.zeros(0)
    env[: len(v)] = v
    block = int(0.05 * SR)
    pad = (-len(env)) % block
    blocks = np.pad(env, (0, pad)).reshape(-1, block).max(axis=1)
    # sekin qo'yib yuborish (release ~0.4 s)
    for i in range(1, len(blocks)):
        blocks[i] = max(blocks[i], blocks[i - 1] * 0.88)
    active = np.clip(blocks / 0.08, 0, 1)
    gain = 1 - 0.62 * active
    return np.repeat(gain, block)[:n]


def mix(duration: float, voice_wav: bytes | None, events: list[tuple[float, str]],
        mood: str | None = "tech") -> bytes:
    """Final soundtrack as 44.1 kHz stereo WAV bytes."""
    n = int(duration * SR)
    out = np.zeros((n, 2), dtype=np.float32)
    voice = _voice_to_stereo(voice_wav) if voice_wav else np.zeros((0, 2), dtype=np.float32)
    if mood and mood != "none":
        bed = music(duration, mood) * MOODS.get(mood, MOODS["tech"])["level"]
        bed *= _duck_gain(voice, n)[:, None]
        _add(out, bed, 0)
    for at, kind in events:
        if kind in SFX:
            make, gain = SFX[kind]
            _add(out, make(), at, gain)
    _add(out, voice, 0)
    peak = np.abs(out).max()
    if peak > 0.98:
        out *= 0.98 / peak
    pcm = (out * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} muhit o'zgaruvchisi o'rnatilmagan (.env yoki Render Environment)")
    return value


TELEGRAM_BOT_TOKEN = _required("TELEGRAM_BOT_TOKEN")
# Claude autentifikatsiyasi: claude.ai obunasi (`claude setup-token`) yoki API key.
# Ikkalasi ham Claude Code CLI tomonidan muhitdan o'qiladi.
if not (os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")):
    raise RuntimeError("CLAUDE_CODE_OAUTH_TOKEN (yoki ANTHROPIC_API_KEY) o'rnatilmagan")

# Faqat shu Telegram user ID'lar botdan foydalana oladi (vergul bilan ajrating)
ALLOWED_USER_IDS = {
    int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x
}

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "medium")

# Instagram Graph API (Business/Creator akkaunt)
IG_USER_ID = os.getenv("IG_USER_ID", "")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")
# Facebook Login orqali token olingan bo'lsa graph.facebook.com,
# "Instagram Login" orqali bo'lsa graph.instagram.com
IG_GRAPH_HOST = os.getenv("IG_GRAPH_HOST", "graph.facebook.com")
IG_API_VERSION = os.getenv("IG_API_VERSION", "v23.0")

# Render avtomatik beradi: https://<servis>.onrender.com
PUBLIC_URL = (os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
PORT = int(os.getenv("PORT", "8000"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "media")).resolve()
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

FONT_PATH = os.getenv("FONT_PATH", "")

# VoiceLab TTS (ovoz). Default: "Javohir — Bright presenter"
VOICELAB_API_KEY = os.getenv("VOICELAB_API_KEY", "")
VOICELAB_VOICE_ID = os.getenv("VOICELAB_VOICE_ID", "voice_fa56cecf-9b0b-45ba-9ec3-494efb5b301f")
# auto: VoiceLab, kredit tugasa/xato bo'lsa bepul Edge TTS | voicelab | edge
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "auto").lower()

# OpenAI rasm generatsiyasi (qahramonlar, stikerlar, fonlar) - ixtiyoriy
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")
OPENAI_IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "medium")

# Shaxsiy avatar: shu papkadagi rasmlaringizdan 3D qahramon-stikerlar yasaladi
_ROOT = Path(__file__).resolve().parent.parent
AVATAR_DIR = Path(os.getenv("AVATAR_DIR", _ROOT / "husanbek")).resolve()
# Yaratilgan avatarlar keshi (repo'ga qo'shsangiz Render'da qayta pul to'lanmaydi)
AVATAR_CACHE_DIR = Path(os.getenv("AVATAR_CACHE_DIR", _ROOT / "avatar_cache")).resolve()
AVATAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
AVATAR_STYLE = os.getenv("AVATAR_STYLE", "Wearing a dark grey hoodie, modern young IT creator look")

# Pechat: har bir videoning o'ng pastki burchagidagi logo (WATERMARK_PATH= bo'sh qoldirsangiz o'chadi)
_wm = os.getenv("WATERMARK_PATH", str(_ROOT / "husanbek-coder.png"))
WATERMARK_PATH = Path(_wm).resolve() if _wm else None
WATERMARK_WIDTH = int(os.getenv("WATERMARK_WIDTH", "300"))
WATERMARK_OPACITY = float(os.getenv("WATERMARK_OPACITY", "0.9"))

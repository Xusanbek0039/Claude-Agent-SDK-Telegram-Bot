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
ANTHROPIC_API_KEY = _required("ANTHROPIC_API_KEY")

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

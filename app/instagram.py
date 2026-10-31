"""Instagram Graph API: Reels publishing (Business/Creator account)."""

import asyncio

import httpx

from . import config


class InstagramError(RuntimeError):
    pass


def _base() -> str:
    return f"https://{config.IG_GRAPH_HOST}/{config.IG_API_VERSION}"


def is_configured() -> bool:
    return bool(config.IG_USER_ID and config.IG_ACCESS_TOKEN)


async def _call(client: httpx.AsyncClient, method: str, path: str, **params) -> dict:
    params["access_token"] = config.IG_ACCESS_TOKEN
    resp = await client.request(method, f"{_base()}/{path}", params=params)
    data = resp.json()
    if resp.status_code >= 400 or "error" in data:
        raise InstagramError(data.get("error", {}).get("message", resp.text))
    return data


async def publish_reel(video_url: str, caption: str, timeout_s: int = 600) -> str:
    """Upload a public video URL as a Reel and return the published media id."""
    if not is_configured():
        raise InstagramError("IG_USER_ID / IG_ACCESS_TOKEN sozlanmagan")
    async with httpx.AsyncClient(timeout=60) as client:
        container = await _call(
            client, "POST", f"{config.IG_USER_ID}/media",
            media_type="REELS", video_url=video_url, caption=caption, share_to_feed="true",
        )
        container_id = container["id"]

        # Instagram videoni yuklab, qayta ishlashini kutamiz
        for _ in range(timeout_s // 5):
            status = await _call(client, "GET", container_id, fields="status_code,status")
            code = status.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise InstagramError(f"Video qayta ishlanmadi: {status.get('status')}")
            await asyncio.sleep(5)
        else:
            raise InstagramError("Instagram video qayta ishlashini kutish vaqti tugadi")

        published = await _call(
            client, "POST", f"{config.IG_USER_ID}/media_publish", creation_id=container_id
        )
        return published["id"]


async def account_info() -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        return await _call(
            client, "GET", config.IG_USER_ID,
            fields="username,followers_count,media_count",
        )

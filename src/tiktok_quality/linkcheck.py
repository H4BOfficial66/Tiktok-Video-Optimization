"""Check a posted TikTok URL: which resolutions and frame rates TikTok actually serves.

Based on 7-u8/TikTok-Quality-Toolkit (HaiYTB / TikTok-Tweaks).
Checks TikTok rehydration JSON and tikwm API fallback.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
URL_RE = re.compile(r"https?://(?:[\w-]+\.)?tiktok\.com/[^\s\"'<>]+", re.I)
CODECS = {
    "h264": "H.264",
    "h265_hvc1": "H.265",
    "bytevc1": "H.265",
    "h265": "H.265",
}


class Session:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.addheaders = [("User-Agent", UA), ("Accept-Language", "en-US,en;q=0.9")]

    def get(self, url: str, headers: Optional[dict] = None, data: Optional[bytes] = None, timeout: int = 25):
        req = urllib.request.Request(url, data=data, headers=headers or {})
        return self.opener.open(req, timeout=timeout)


def extract_url(text: str) -> str:
    m = URL_RE.search(text or "")
    if not m:
        raise ValueError("No TikTok link found - paste a link like https://www.tiktok.com/@user/video/123...")
    return m.group(0).rstrip(".,)>]")


def _page_streams(session: Session, url: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    resp = session.get(url)
    final = resp.geturl()
    html = resp.read().decode("utf-8", "replace")
    if "/photo/" in final:
        raise ValueError("This link is a photo post, not a video.")
    m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return final, None
    data = json.loads(m.group(1))
    detail = data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {})
    item = detail.get("itemInfo", {}).get("itemStruct", {})
    video = item.get("video") or {}
    if not video:
        return final, None
    streams = []
    for b in video.get("bitrateInfo") or []:
        pa = b.get("PlayAddr") or {}
        w, h = pa.get("Width") or 0, pa.get("Height") or 0
        streams.append({
            "name": b.get("GearName") or "?",
            "fps": b.get("BitrateFPS") or 0,
            "width": w,
            "height": h,
            "codec": CODECS.get((b.get("CodecType") or "").lower(), b.get("CodecType") or "?"),
            "mbps": round((b.get("Bitrate") or 0) / 1e6, 2),
            "size": int(pa.get("DataSize") or 0),
        })
    return final, {
        "source": "tiktok",
        "author": (item.get("author") or {}).get("uniqueId", ""),
        "title": (item.get("desc") or "")[:80],
        "duration": video.get("duration") or 0,
        "width": video.get("width") or 0,
        "height": video.get("height") or 0,
        "streams": sorted(streams, key=lambda s: (s["fps"], s["height"], s["mbps"]), reverse=True),
    }


def _tikwm(session: Session, url: str) -> Dict[str, Any]:
    body = urllib.parse.urlencode({"url": url, "hd": 1}).encode()
    resp = session.get("https://www.tikwm.com/api/", data=body, headers={"Accept": "application/json"})
    j = json.loads(resp.read())
    d = j.get("data") or {}
    if j.get("code") != 0 or not (d.get("hdplay") or d.get("play")):
        raise ValueError(f"Video not found ({j.get('msg') or 'private, deleted or region-locked'}).")
    streams = []
    if d.get("hdplay"):
        streams.append({
            "name": "HD 1080p",
            "fps": 60,
            "width": 1080,
            "height": 1920,
            "codec": "H.264",
            "mbps": 0.0,
            "size": int(d.get("hd_size") or 0),
        })
    if d.get("play"):
        streams.append({
            "name": "SD Play",
            "fps": 30,
            "width": 720,
            "height": 1280,
            "codec": "H.264",
            "mbps": 0.0,
            "size": int(d.get("size") or 0),
        })
    return {
        "source": "tikwm",
        "author": (d.get("author") or {}).get("unique_id", ""),
        "title": (d.get("title") or "")[:80],
        "duration": d.get("duration") or 0,
        "width": 1080,
        "height": 1920,
        "streams": streams,
    }


def check_tiktok(text: str) -> Dict[str, Any]:
    """Inspect a TikTok video URL. Returns streams, best FPS, and quality verdict."""
    url = extract_url(text)
    session = Session()
    info = None
    try:
        url, info = _page_streams(session, url)
    except Exception:
        info = None

    if not info or not info.get("streams"):
        try:
            info = _tikwm(session, url)
        except Exception as e:
            raise ValueError(f"Could not inspect TikTok video: {e}")

    info["url"] = url
    best_fps = max((s["fps"] for s in info["streams"]), default=0)
    best_h = max((s["height"] for s in info["streams"]), default=0)
    info["best_fps"] = best_fps
    info["best_height"] = best_h
    info["is_1080p"] = best_h >= 1080 or any(s["width"] >= 1080 for s in info["streams"])
    info["is_60fps"] = best_fps >= 50

    if info["is_1080p"] and info["is_60fps"]:
        info["verdict"] = "Ultra HD 1080p 60FPS Verified! Trick Successful."
        info["status"] = "success"
    elif info["is_1080p"]:
        info["verdict"] = "1080p HD Active (30 FPS Standard)."
        info["status"] = "warning"
    else:
        info["verdict"] = f"Standard Quality ({best_h}p). Check upload settings."
        info["status"] = "normal"

    return info

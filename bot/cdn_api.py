import os
import re
import uuid
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple, List
import aiohttp
import aiofiles
from bot.config import config

logger = logging.getLogger(__name__)

# Fast Public Direct CDN Mirrors
COBALT_INSTANCES = [
    "https://api.cobalt.tools",
    "https://cobalt.api.scot",
]

INVIDIOUS_INSTANCES = [
    "https://invidious.nerdvpn.de",
    "https://inv.tux.pizza",
    "https://vid.priv.au",
]

YOUTUBE_ID_REGEX = re.compile(
    r'(?:v=|\/|youtu\.be\/|embed\/|shorts\/|\/v\/|watch\?v=|\&v=)([a-zA-Z0-9_-]{11})'
)
TIKTOK_REGEX = re.compile(r'(tiktok\.com|douyin\.com)')


def extract_youtube_id(url: str) -> Optional[str]:
    """Extracts YouTube 11-char video ID from any YouTube URL."""
    if not url:
        return None
    match = YOUTUBE_ID_REGEX.search(url)
    return match.group(1) if match else None


async def expand_redirect_url(url: str, timeout: float = 4.0) -> str:
    """
    Follows HTTP redirects (301/302) to resolve short links
    (e.g., vt.tiktok.com, xhslink.com, v.kuaishou.com, pin.it, fb.watch, t.co, youtu.be, bit.ly)
    to their full canonical destination URL.
    """
    if not url or not url.startswith("http"):
        return url

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, headers=headers, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status < 400 and str(resp.url) != url:
                    logger.info(f"🔗 Expanded redirect {url} -> {resp.url}")
                    return str(resp.url)
    except Exception:
        # Fallback to GET if HEAD method is disallowed by server
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    if resp.status < 400 and str(resp.url) != url:
                        logger.info(f"🔗 Expanded redirect {url} -> {resp.url}")
                        return str(resp.url)
        except Exception:
            pass

    return url


async def fetch_cobalt_mirror(url: str, is_audio: bool = False, timeout: float = 4.0) -> Optional[Dict[str, Any]]:
    """
    Resolves media directly via Cobalt API instances.
    Provides ultra-fast CDN downloads for Instagram, Twitter/X, Reddit, TikTok, Facebook, Pinterest, and YouTube.
    """
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    payload = {
        "url": url,
        "videoQuality": "1080",
        "downloadMode": "audio" if is_audio else "auto",
        "audioFormat": "mp3",
        "youtubeVideoCodec": "h264",
    }

    for instance in COBALT_INSTANCES:
        api_endpoint = instance.rstrip("/")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_endpoint,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        status = data.get("status")

                        image_urls = []
                        media_url = None
                        if status in ("tunnel", "redirect"):
                            media_url = data.get("url")
                        elif status == "picker" and data.get("picker"):
                            for p_item in data["picker"]:
                                u = p_item.get("url")
                                if u:
                                    image_urls.append(u)
                            if data["picker"]:
                                media_url = data["picker"][0].get("url")

                        if media_url or image_urls:
                            logger.info(f"✅ Cobalt mirror succeeded via {instance} for {url}")
                            filename = data.get("filename") or ("Audio" if is_audio else "Video")
                            return {
                                "success": True,
                                "source": f"Cobalt CDN ({instance})",
                                "title": filename,
                                "uploader": "Direct CDN",
                                "duration": 0,
                                "video_url": None if is_audio else media_url,
                                "audio_url": media_url if is_audio else None,
                                "thumbnail_url": data.get("thumb"),
                                "image_urls": image_urls,
                            }
        except Exception as e:
            logger.debug(f"Cobalt instance {instance} query failed for {url}: {e}")
            continue

    return None


class DirectCDN:
    """
    Lightning-Fast Multi-Mirror Stream Resolver:
    Resolves streams via parallel CDN mirror queries with ultra-low latency.
    """

    @staticmethod
    async def fetch_tikwm_mirror(url: str) -> Optional[Dict[str, Any]]:
        """Mirror 1 (TikTok): TikWM HD CDN Mirror."""
        api_url = "https://www.tikwm.com/api/"
        data = {"url": url, "count": 12, "cursor": 0, "web": 1, "hd": 1}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=3.0)) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        if res.get("code") == 0 and "data" in res:
                            item = res["data"]
                            video_cdn = item.get("hdplay") or item.get("play")
                            music_cdn = item.get("music")
                            title = item.get("title") or "TikTok Video"
                            author = item.get("author", {}).get("nickname") or "TikTok Creator"
                            duration = item.get("duration") or 0
                            thumbnail = item.get("cover")

                            images = item.get("images") or []
                            logger.info(f"✅ TikWM Mirror 1 succeeded for {url} (images: {len(images)})")
                            return {
                                "success": True,
                                "source": "TikWM HD Mirror",
                                "title": title,
                                "uploader": author,
                                "duration": duration,
                                "video_url": video_cdn,
                                "audio_url": music_cdn,
                                "thumbnail_url": thumbnail,
                                "image_urls": images,
                            }
        except Exception:
            pass
        return None

    @staticmethod
    async def fetch_tikmate_mirror(url: str) -> Optional[Dict[str, Any]]:
        """Mirror 2 (TikTok): TikMate HD Mirror."""
        api_url = "https://api.tikmate.app/api/lookup"
        data = {"url": url}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://tikmate.app/"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=3.0)) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        video_id = res.get("id")
                        token = res.get("token")
                        if video_id and token:
                            video_url = f"https://tikmate.app/download/{token}/{video_id}.mp4?hd=1"
                            title = res.get("author_name", "TikTok Video")
                            author = res.get("author_id", "Creator")
                            thumbnail = res.get("author_avatar")

                            logger.info(f"✅ TikMate Mirror 2 succeeded for {url}")
                            return {
                                "success": True,
                                "source": "TikMate Mirror",
                                "title": f"TikTok by {title}",
                                "uploader": author,
                                "duration": 0,
                                "video_url": video_url,
                                "audio_url": None,
                                "thumbnail_url": thumbnail,
                            }
        except Exception:
            pass
        return None

    @staticmethod
    async def _query_single_invidious(instance: str, yt_id: str, is_audio: bool) -> Optional[Dict[str, Any]]:
        api_url = f"{instance}/api/v1/videos/{yt_id}"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers=headers, timeout=aiohttp.ClientTimeout(total=2.5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        title = data.get("title", "YouTube Video")
                        uploader = data.get("author", "YouTube Channel")
                        duration = data.get("lengthSeconds", 0)
                        thumbnail = f"https://img.youtube.com/vi/{yt_id}/maxresdefault.jpg"

                        if is_audio:
                            audio_streams = data.get("adaptiveFormats", [])
                            audio_streams = [s for s in audio_streams if "audio" in s.get("type", "")]
                            if audio_streams:
                                best_audio = sorted(audio_streams, key=lambda x: int(x.get("bitrate", 0)), reverse=True)[0]
                                return {
                                    "success": True,
                                    "source": f"Invidious ({instance})",
                                    "title": title,
                                    "uploader": uploader,
                                    "duration": duration,
                                    "audio_url": best_audio.get("url"),
                                    "thumbnail_url": thumbnail,
                                }
                        else:
                            format_streams = data.get("formatStreams", [])
                            if format_streams:
                                best_video = format_streams[-1]
                                return {
                                    "success": True,
                                    "source": f"Invidious ({instance})",
                                    "title": title,
                                    "uploader": uploader,
                                    "duration": duration,
                                    "video_url": best_video.get("url"),
                                    "thumbnail_url": thumbnail,
                                }
        except Exception:
            pass
        return None

    @classmethod
    async def resolve_multi_mirror(cls, url: str, is_audio: bool = False) -> Optional[Dict[str, Any]]:
        """
        Parallel Multi-Mirror Stream Resolver:
        Queries mirrors with hard timeouts; falls back to yt-dlp if unhandled.
        """
        # 1. TikTok / Douyin
        if TIKTOK_REGEX.search(url):
            res = await cls.fetch_tikwm_mirror(url)
            if res and (res.get("video_url") or (is_audio and res.get("audio_url"))):
                return res

            if not is_audio:
                res = await cls.fetch_tikmate_mirror(url)
                if res and res.get("video_url"):
                    return res

        # 2. Universal Cobalt fallback
        cobalt_res = await fetch_cobalt_mirror(url, is_audio=is_audio, timeout=3.0)
        if cobalt_res and (cobalt_res.get("video_url") or (is_audio and cobalt_res.get("audio_url"))):
            return cobalt_res

        # 3. YouTube -> Parallel Invidious query
        yt_id = extract_youtube_id(url)
        if yt_id:
            tasks = [cls._query_single_invidious(inst, yt_id, is_audio) for inst in INVIDIOUS_INSTANCES]
            try:
                for coro in asyncio.as_completed(tasks, timeout=2.5):
                    try:
                        res = await coro
                        if res and (res.get("video_url") or (is_audio and res.get("audio_url"))):
                            return res
                    except Exception:
                        pass
            except asyncio.TimeoutError:
                pass

        return None


async def download_direct_cdn_stream(
    cdn_url: str,
    output_filepath: str,
    max_size_mb: float = 2000.0,
    max_retries: int = 3,
    headers: Optional[Dict[str, str]] = None
) -> Tuple[bool, int, float, Optional[str]]:
    """
    Auto-Retry streaming download directly from high-speed CDN.
    Enforces maximum size limit and ensures robust chunk writing.
    """
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    if headers:
        req_headers.update(headers)

    # Route Bilibili UPOS CDN streams away from throttled Akamai to high-speed mirror
    for akam in ["upos-hz-mirrorakam.akamaized.net", "upos-sz-mirrorali.bilivideo.com"]:
        if akam in cdn_url:
            cdn_url = cdn_url.replace(akam, "upos-sz-upcdnbda2.bilivideo.com")
            break

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(cdn_url, headers=req_headers, timeout=aiohttp.ClientTimeout(total=900, sock_read=60)) as resp:
                    if resp.status not in (200, 206):
                        last_error = f"CDN returned HTTP {resp.status}"
                        await asyncio.sleep(1)
                        continue

                    content_length = resp.headers.get("Content-Length")
                    if content_length:
                        try:
                            est_bytes = int(content_length)
                            est_mb = round(est_bytes / (1024 * 1024), 2)
                            if est_mb > max_size_mb:
                                return False, 0, 0.0, f"FILE_TOO_LARGE:{est_mb}:{max_size_mb}"
                        except ValueError:
                            pass

                    total_bytes = 0
                    max_bytes = int(max_size_mb * 1024 * 1024)

                    async with aiofiles.open(output_filepath, "wb") as f:
                        async for chunk in resp.content.iter_chunked(256 * 1024):
                            total_bytes += len(chunk)
                            if total_bytes > max_bytes:
                                return False, 0, 0.0, f"FILE_TOO_LARGE:{round(total_bytes/(1024*1024), 2)}:{max_size_mb}"
                            await f.write(chunk)

                    filesize_mb = round(total_bytes / (1024 * 1024), 2)
                    return True, total_bytes, filesize_mb, None

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_error = str(e)
            if os.path.exists(output_filepath):
                try:
                    os.remove(output_filepath)
                except Exception:
                    pass
            await asyncio.sleep(attempt)
        except Exception as e:
            last_error = str(e)
            break

    return False, 0, 0.0, last_error or "Download failed after retries"

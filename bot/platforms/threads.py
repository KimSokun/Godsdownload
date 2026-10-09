import os
import re
import uuid
import html
import time
import json
import base64
import asyncio
import logging
from typing import Optional, Dict, Any, List

import aiohttp

from bot.config import config
from bot.platforms.base import (
    BasePlatform,
    download_thumbnail,
    convert_to_mp3,
    cleanup_files,
    TELEGRAM_FILE_LIMIT_BYTES,
)
from bot.cdn_api import download_direct_cdn_stream, expand_redirect_url, fetch_cobalt_mirror

logger = logging.getLogger(__name__)

SHORTCODE_RE = re.compile(r'(?:/t/|/post/|/share/)([A-Za-z0-9_-]+)')
USERNAME_RE = re.compile(r'threads\.(?:net|com)/@([A-Za-z0-9_.-]+)')

# In-memory TTL cache to eliminate duplicate network calls between fast_scan and download
_threads_cache: Dict[str, Dict[str, Any]] = {}


async def resolve_threads_share_url(url: str) -> str:
    """Follows HTTP 301/302 redirects for mobile /share/ links to get canonical post URL."""
    if "/share/" not in url:
        return url
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
                loc = resp.headers.get("Location")
                if loc:
                    return loc
    except Exception as e:
        logger.debug(f"Threads share URL resolution error: {e}")
    return url


class ThreadsPlatform(BasePlatform):
    platform_id = "threads"
    display_name = "🧵 Threads"
    patterns = [
        re.compile(r'(?:https?://)?(?:www\.)?(?:threads\.net|threads\.com)'),
    ]

    def _threads_custom_opts(self) -> Dict[str, Any]:
        return {
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            }
        }

    @staticmethod
    def extract_shortcode(url: str) -> Optional[str]:
        m = SHORTCODE_RE.search(url)
        return m.group(1) if m else None

    @staticmethod
    def extract_username(url: str) -> Optional[str]:
        m = USERNAME_RE.search(url)
        return m.group(1) if m else None

    async def resolve_threads_media(self, url: str) -> Dict[str, Any]:
        """
        Multi-tier extraction for Threads (Photos, Carousels, and Videos).
        Caches successful results for 180 seconds to avoid duplicate requests.
        """
        global _threads_cache

        # 1. Clean URL and expand redirects
        clean_url = await resolve_threads_share_url(url)
        clean_url = await expand_redirect_url(clean_url)

        shortcode = self.extract_shortcode(clean_url)
        username = self.extract_username(clean_url) or "Threads Creator"

        cache_key = shortcode or clean_url
        if cache_key in _threads_cache:
            cached = _threads_cache[cache_key]
            if time.time() - cached.get("_cached_at", 0) < 180:
                return cached["data"]

        result: Dict[str, Any] = {
            "shortcode": shortcode,
            "username": username,
            "title": "Threads Post",
            "uploader": username,
            "video_url": None,
            "image_urls": [],
            "thumbnail_url": None,
            "media_type": None,
        }

        # Tier 1: LoveThreads API (supports both share URLs and direct posts)
        # Works natively with photo carousels & full HD videos
        try:
            endpoint = "https://lovethreads.net/api/ajaxSearch"
            headers_api = {
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://lovethreads.net",
                "Referer": "https://lovethreads.net/en",
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            }
            data_payload = {"q": clean_url, "t": "media", "lang": "en"}
            async with aiohttp.ClientSession() as session:
                async with session.post(endpoint, data=data_payload, headers=headers_api, timeout=aiohttp.ClientTimeout(total=8.0)) as resp:
                    if resp.status == 200:
                        api_json = await resp.json()
                        if api_json.get("status") == "ok":
                            html_data = api_json.get("data", "")
                            tokens = re.findall(r'https://dl\.snapcdn\.app/get\?token=([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)', html_data)

                            found_video = None
                            found_images: List[str] = []

                            for tok in tokens:
                                full_url = f"https://dl.snapcdn.app/get?token={tok}"
                                try:
                                    payload_b64 = tok.split(".")[1] + "=="
                                    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                                    direct_u = payload.get("url", "")
                                    if ".mp4" in direct_u or "video" in direct_u:
                                        if not found_video:
                                            found_video = full_url
                                    else:
                                        if full_url not in found_images:
                                            found_images.append(full_url)
                                except Exception:
                                    pass

                            if found_video:
                                result["video_url"] = found_video
                                result["media_type"] = "video"
                                if found_images:
                                    result["thumbnail_url"] = found_images[0]
                            elif found_images:
                                result["image_urls"] = found_images
                                result["thumbnail_url"] = found_images[0]
                                result["media_type"] = "images"

                            caption_m = re.search(r'class="download-box"[^>]*>.*?<p[^>]*>(.*?)</p>', html_data, re.DOTALL)
                            if caption_m:
                                c_text = re.sub(r'<[^>]+>', '', caption_m.group(1)).strip()
                                if c_text:
                                    result["title"] = html.unescape(c_text)[:120]

                            if result["video_url"] or result["image_urls"]:
                                _threads_cache[cache_key] = {"data": result, "_cached_at": time.time()}
                                return result
        except Exception as e:
            logger.debug(f"Threads LoveThreads API query failed: {e}")

        # Tier 2: Facebook External Hit Crawler (for public Threads posts)
        if shortcode:
            try:
                main_url = f"https://www.threads.net/t/{shortcode}"
                headers_bot = {
                    "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
                    "Accept": "text/html,*/*",
                }
                async with aiohttp.ClientSession() as session:
                    async with session.get(main_url, headers=headers_bot, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                        if resp.status == 200:
                            raw_html = await resp.text()
                            og_v = re.findall(r'<meta property="og:video(?::secure_url)?" content="([^"]+)"', raw_html)
                            og_i = re.findall(r'<meta property="og:image" content="([^"]+)"', raw_html)
                            og_d = re.findall(r'<meta property="og:description" content="([^"]+)"', raw_html)

                            if og_d:
                                desc = html.unescape(og_d[0]).strip()
                                if desc:
                                    result["title"] = desc[:120]

                            if og_v:
                                result["video_url"] = html.unescape(og_v[0])
                                result["media_type"] = "video"

                            if og_i:
                                imgs = [html.unescape(i) for i in og_i]
                                result["image_urls"] = imgs
                                result["thumbnail_url"] = imgs[0]
                                if not result["media_type"]:
                                    result["media_type"] = "images"

                            if result["video_url"] or result["image_urls"]:
                                _threads_cache[cache_key] = {"data": result, "_cached_at": time.time()}
                                return result
            except Exception as e:
                logger.debug(f"Threads Tier 2 crawler failed: {e}")

        # Tier 3: Cobalt mirror fallback
        try:
            cdn_info = await fetch_cobalt_mirror(clean_url, is_audio=False, timeout=3.0)
            if cdn_info:
                if cdn_info.get("video_url"):
                    result["video_url"] = cdn_info["video_url"]
                    result["media_type"] = "video"
                if cdn_info.get("image_urls"):
                    result["image_urls"] = cdn_info["image_urls"]
                    if not result["media_type"]:
                        result["media_type"] = "images"
                if cdn_info.get("thumbnail_url"):
                    result["thumbnail_url"] = cdn_info["thumbnail_url"]
                if cdn_info.get("title"):
                    result["title"] = cdn_info["title"]
                if cdn_info.get("uploader"):
                    result["uploader"] = cdn_info["uploader"]

                if result["video_url"] or result["image_urls"]:
                    _threads_cache[cache_key] = {"data": result, "_cached_at": time.time()}
                    return result
        except Exception as e:
            logger.debug(f"Threads Cobalt fallback failed: {e}")

        return result

    async def fast_scan(self, url: str) -> Optional[Dict[str, Any]]:
        """Fast scan Threads link (video vs single photo vs carousel)."""
        info = await self.resolve_threads_media(url)
        if info.get("media_type") == "images" or (not info.get("video_url") and info.get("image_urls")):
            return {
                "type": "images",
                "platform": self.display_name,
                "platform_id": self.platform_id,
                "image_urls": info.get("image_urls", []),
                "title": info.get("title", "Threads Photos"),
                "uploader": info.get("uploader", "Threads Creator"),
                "thumbnail_url": info.get("thumbnail_url"),
            }
        if info.get("video_url"):
            return {
                "type": "video",
                "platform": self.display_name,
                "platform_id": self.platform_id,
                "image_urls": [],
                "title": info.get("title", "Threads Video"),
                "uploader": info.get("uploader", "Threads Creator"),
                "thumbnail_url": info.get("thumbnail_url"),
            }
        return None

    async def download_images(self, url: str) -> Any:
        from bot.downloader import DownloadResult

        scan = await self.fast_scan(url)
        if scan and scan.get("image_urls"):
            return DownloadResult(
                success=True,
                is_image=True,
                image_urls=scan["image_urls"],
                title=scan.get("title", "Threads Photos"),
                uploader=scan.get("uploader", "Threads Creator"),
                direct_url=scan["image_urls"][0] if scan["image_urls"] else None,
            )
        return await super().download_images(url)

    async def download_video(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        file_id = uuid.uuid4().hex
        output_video = os.path.join(config.temp_dir, f"{file_id}.mp4")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        # Step 1: Multi-tier resolver (LoveThreads / Crawler / Cobalt)
        try:
            info = await self.resolve_threads_media(url)
            if info and info.get("video_url"):
                cdn_url = info["video_url"]
                stream_headers = {
                    "Referer": "https://lovethreads.net/",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                }
                ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                    cdn_url, output_video, max_size_mb, max_retries=2, headers=stream_headers
                )
                if ok:
                    thumb_path = await download_thumbnail(info.get("thumbnail_url"), output_thumb)
                    return DownloadResult(
                        success=True,
                        filepath=output_video,
                        filename=f"{file_id}.mp4",
                        filesize_bytes=total_bytes,
                        filesize_mb=filesize_mb,
                        title=info.get("title", "Threads Video"),
                        uploader=info.get("uploader", "Threads Creator"),
                        duration=info.get("duration", 0),
                        thumbnail_path=thumb_path,
                        is_over_50mb=(total_bytes > TELEGRAM_FILE_LIMIT_BYTES),
                        is_audio=False,
                        direct_url=cdn_url,
                    )
                elif err and "FILE_TOO_LARGE" in err:
                    return DownloadResult(
                        success=True,
                        is_over_50mb=True,
                        direct_url=cdn_url,
                        title=info.get("title", "Threads Video"),
                        uploader=info.get("uploader", "Threads Creator"),
                        filesize_mb=filesize_mb,
                        duration=info.get("duration", 0),
                        thumbnail_path=info.get("thumbnail_url"),
                        is_audio=False,
                    )
            elif info and info.get("image_urls") and not info.get("video_url"):
                return DownloadResult(
                    success=False,
                    error="NO_VIDEO: This Threads post contains photos only (no video). Please choose '🖼️ Photos' to download."
                )
        except Exception as e:
            logger.debug(f"Threads direct video resolution failed: {e}")

        # Step 2: yt-dlp fallback (with clean expanded url)
        clean_url = await expand_redirect_url(url)
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb, self._threads_custom_opts()
        )

    async def download_audio(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        file_id = uuid.uuid4().hex
        raw_video = os.path.join(config.temp_dir, f"{file_id}_raw.mp4")
        final_mp3 = os.path.join(config.temp_dir, f"{file_id}.mp3")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        # Step 1: Direct stream download + FFmpeg MP3 conversion
        try:
            info = await self.resolve_threads_media(url)
            if info and info.get("video_url"):
                cdn_url = info["video_url"]
                stream_headers = {
                    "Referer": "https://lovethreads.net/",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                }
                ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                    cdn_url, raw_video, max_size_mb, max_retries=2, headers=stream_headers
                )
                if ok:
                    converted = await convert_to_mp3(raw_video, final_mp3)
                    chosen_filepath = final_mp3 if converted else raw_video
                    cleanup_files(raw_video if converted else None)

                    filesize = os.path.getsize(chosen_filepath)
                    filesize_mb = round(filesize / (1024 * 1024), 2)
                    thumb_path = await download_thumbnail(info.get("thumbnail_url"), output_thumb)

                    return DownloadResult(
                        success=True,
                        filepath=chosen_filepath,
                        filename=f"{file_id}.mp3",
                        filesize_bytes=filesize,
                        filesize_mb=filesize_mb,
                        title=info.get("title", "Threads Audio"),
                        uploader=info.get("uploader", "Threads Creator"),
                        duration=info.get("duration", 0),
                        thumbnail_path=thumb_path,
                        is_over_50mb=(filesize > TELEGRAM_FILE_LIMIT_BYTES),
                        is_audio=True,
                        direct_url=cdn_url,
                    )
            elif info and info.get("image_urls") and not info.get("video_url"):
                return DownloadResult(
                    success=False,
                    error="NO_AUDIO: This Threads post contains photos only. Audio is only available for videos."
                )
        except Exception as e:
            logger.debug(f"Threads direct audio extraction failed: {e}")

        # Step 2: yt-dlp fallback
        clean_url = await expand_redirect_url(url)
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, self._threads_custom_opts()
        )

import os
import re
import uuid
import asyncio
import logging
from typing import Optional, Dict, Any, List
import aiohttp
import json

from bot.config import config
from bot.platforms.base import (
    BasePlatform,
    download_thumbnail,
    convert_to_mp3,
    cleanup_files,
    TELEGRAM_FILE_LIMIT_BYTES,
)
from bot.cdn_api import download_direct_cdn_stream, expand_redirect_url

logger = logging.getLogger(__name__)


class DouyinPlatform(BasePlatform):
    """
    Dedicated platform handler for Douyin (Chinese TikTok: douyin.com / iesdouyin.com).
    Kept strictly independent from TikTok to avoid impacting global tiktok.com downloads.
    """
    platform_id = "douyin"
    display_name = "🇨🇳 Douyin (抖音)"
    patterns = [
        re.compile(r'(?:https?://)?(?:[a-zA-Z0-9_\.]+\.)?(?:douyin\.com|iesdouyin\.com)'),
    ]

    @staticmethod
    def extract_aweme_id(url: str) -> Optional[str]:
        """Extracts aweme/video id from long URL, note URL, or query parameters."""
        patterns = (
            r'/video/(\d{10,30})',
            r'/note/(\d{10,30})',
            r'[?&](?:modal_id|aweme_id|item_id)=(\d{10,30})',
        )
        for pattern in patterns:
            m = re.search(pattern, url)
            if m:
                return m.group(1)
        return None

    async def _fetch_ttwid(self) -> Optional[str]:
        """Registers a guest session with ByteDance to acquire a valid ttwid cookie."""
        url = "https://ttwid.bytedance.com/ttwid/union/register/"
        post_data = {
            "region": "cn",
            "aid": 1768,
            "needFid": "false",
            "service": "www.ixigua.com",
            "migrate_info": {"ticket": "", "source": "node"},
            "cbUrlProtocol": "https",
            "union": "true",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=post_data, headers=headers, timeout=aiohttp.ClientTimeout(total=3.5)) as resp:
                    if resp.status == 200:
                        cookie = resp.cookies.get("ttwid")
                        if cookie:
                            return cookie.value
        except Exception:
            pass
        return None

    async def _fetch_iesdouyin_api(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        """Queries the mobile iesdouyin iteminfo API."""
        api_url = f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={aweme_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
            "Referer": "https://www.douyin.com/",
        }
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        if text:
                            data = json.loads(text)
                            items = data.get("item_list", [])
                            if items:
                                return items[0]
        except Exception as e:
            logger.debug(f"iesdouyin API error: {e}")
        return None

    async def _fetch_web_detail(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        """Queries the web detail API with guest ttwid session and browser headers."""
        ttwid = await self._fetch_ttwid()
        cookie_header = f"ttwid={ttwid}; s_v_web_id=verify_guest;" if ttwid else "s_v_web_id=verify_guest;"
        custom_cookie = getattr(config, "douyin_cookie", "")
        if custom_cookie:
            cookie_header = f"{custom_cookie}; {cookie_header}"

        url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
        params = {
            "aweme_id": aweme_id,
            "aid": "6383",
            "device_platform": "webapp",
            "cookie_enabled": "true",
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Chrome",
            "browser_version": "128.0.0.0",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Referer": "https://www.douyin.com/",
            "Cookie": cookie_header,
        }
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        detail = data.get("aweme_detail")
                        if detail:
                            return detail
        except Exception as e:
            logger.debug(f"Douyin web detail API error: {e}")
        return None

    async def _resolve_play_stream(self, video_uri: str) -> Optional[str]:
        """Resolves watermark-free play stream URL by following redirect."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.douyin.com/",
        }
        play_url = f"https://aweme.snssdk.com/aweme/v1/play/?video_id={video_uri}&ratio=1080p&line=0"
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(play_url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
                    location = resp.headers.get("Location")
                    if location:
                        return location
        except Exception:
            pass
        return None

    async def fast_scan(self, url: str) -> Optional[Dict[str, Any]]:
        """Fast scan Douyin links to detect slideshow images vs video."""
        clean_url = await expand_redirect_url(url)
        aweme_id = self.extract_aweme_id(clean_url)

        # 1. Try mobile iteminfo
        if aweme_id:
            item = await self._fetch_iesdouyin_api(aweme_id)
            if item:
                title = item.get("desc") or "Douyin Video"
                author = item.get("author", {}).get("nickname") or "Douyin Creator"
                images_raw = item.get("images") or []
                if images_raw:
                    image_urls = []
                    for img in images_raw:
                        urls = img.get("url_list", [])
                        if urls:
                            image_urls.append(urls[-1])
                    if image_urls:
                        return {
                            "type": "images",
                            "platform": self.display_name,
                            "platform_id": self.platform_id,
                            "image_urls": image_urls,
                            "title": title,
                            "uploader": author,
                            "audio_url": item.get("music", {}).get("play_url", {}).get("uri"),
                        }

                video_info = item.get("video", {})
                play_addr = video_info.get("play_addr", {})
                urls = play_addr.get("url_list", [])
                video_url = None
                if urls:
                    video_url = urls[0].replace("playwm", "play")

                vid = video_info.get("vid")
                if vid:
                    direct_stream = await self._resolve_play_stream(vid)
                    if direct_stream:
                        video_url = direct_stream

                if video_url:
                    return {
                        "type": "video",
                        "platform": self.display_name,
                        "platform_id": self.platform_id,
                        "image_urls": [],
                        "title": title,
                        "uploader": author,
                        "video_url": video_url,
                        "audio_url": item.get("music", {}).get("play_url", {}).get("uri"),
                        "thumbnail_url": video_info.get("cover", {}).get("url_list", [None])[0],
                    }

        # 2. Try web detail API
        if aweme_id:
            detail = await self._fetch_web_detail(aweme_id)
            if detail:
                title = detail.get("desc") or "Douyin Video"
                author = detail.get("author", {}).get("nickname") or "Douyin Creator"
                images_raw = detail.get("images") or []
                if images_raw:
                    image_urls = []
                    for img in images_raw:
                        urls = img.get("url_list", [])
                        if urls:
                            image_urls.append(urls[-1])
                    if image_urls:
                        return {
                            "type": "images",
                            "platform": self.display_name,
                            "platform_id": self.platform_id,
                            "image_urls": image_urls,
                            "title": title,
                            "uploader": author,
                            "audio_url": detail.get("music", {}).get("play_url", {}).get("uri"),
                        }

                video_info = detail.get("video", {})
                play_addr = video_info.get("play_addr", {})
                video_uri = play_addr.get("uri")
                video_url = None
                if video_uri:
                    video_url = await self._resolve_play_stream(video_uri)
                if not video_url and play_addr.get("url_list"):
                    video_url = play_addr["url_list"][0].replace("playwm", "play")

                if video_url:
                    return {
                        "type": "video",
                        "platform": self.display_name,
                        "platform_id": self.platform_id,
                        "image_urls": [],
                        "title": title,
                        "uploader": author,
                        "video_url": video_url,
                        "audio_url": detail.get("music", {}).get("play_url", {}).get("uri"),
                        "thumbnail_url": video_info.get("cover", {}).get("url_list", [None])[0],
                    }

        # 3. Fallback: yt-dlp metadata dump
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
            }
            custom_cookie = getattr(config, "douyin_cookie", "")
            if custom_cookie:
                ydl_opts["http_headers"] = {"Cookie": custom_cookie}
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(None, lambda: self._dump_ytdlp(clean_url, ydl_opts))
            if info:
                title = info.get("title") or "Douyin Video"
                author = info.get("uploader") or "Douyin Creator"
                url_direct = info.get("url")
                if url_direct:
                    return {
                        "type": "video",
                        "platform": self.display_name,
                        "platform_id": self.platform_id,
                        "image_urls": [],
                        "title": title,
                        "uploader": author,
                        "video_url": url_direct,
                        "thumbnail_url": info.get("thumbnail"),
                    }
        except Exception:
            pass

        return None

    def _dump_ytdlp(self, url: str, opts: dict) -> Optional[dict]:
        import yt_dlp
        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                return ydl.extract_info(url, download=False)
            except Exception:
                return None

    async def download_video(self, url: str, quality: str = "best") -> Any:
        from bot.downloader import DownloadResult

        clean_url = await expand_redirect_url(url)
        scan = await self.fast_scan(clean_url)

        # If fast scan found direct watermark-free video CDN URL
        if scan and scan.get("video_url"):
            video_url = scan["video_url"]
            temp_file = os.path.join(config.temp_dir, f"douyin_{uuid.uuid4().hex}.mp4")
            cdn_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.douyin.com/",
            }

            size_bytes = 0
            try:
                async with aiohttp.ClientSession(headers=cdn_headers) as session:
                    async with session.head(video_url, timeout=aiohttp.ClientTimeout(total=4)) as h_resp:
                        if h_resp.status == 200:
                            size_bytes = int(h_resp.headers.get("Content-Length", 0))
            except Exception:
                pass

            size_mb = round(size_bytes / (1024 * 1024), 2) if size_bytes else None

            # If over 50MB, return direct URL for Cloud CDN streaming
            if size_bytes and size_bytes > TELEGRAM_FILE_LIMIT_BYTES:
                return DownloadResult(
                    success=True,
                    direct_url=video_url,
                    title=scan.get("title", "Douyin Video"),
                    uploader=scan.get("uploader", "Douyin Creator"),
                    filesize_bytes=size_bytes,
                    filesize_mb=size_mb,
                    is_over_50mb=True,
                    headers=cdn_headers,
                )

            # Download using accelerated stream
            ok, downloaded_path, actual_bytes, _ = await download_direct_cdn_stream(
                video_url, temp_file, headers=cdn_headers
            )
            if ok and os.path.exists(downloaded_path):
                f_size = os.path.getsize(downloaded_path)
                f_mb = round(f_size / (1024 * 1024), 2)
                return DownloadResult(
                    success=True,
                    filepath=downloaded_path,
                    title=scan.get("title", "Douyin Video"),
                    uploader=scan.get("uploader", "Douyin Creator"),
                    filesize_bytes=f_size,
                    filesize_mb=f_mb,
                    is_over_50mb=(f_size > TELEGRAM_FILE_LIMIT_BYTES),
                )

        # Fallback to yt-dlp
        custom_cookie = getattr(config, "douyin_cookie", "")
        extra_opts = {}
        if custom_cookie:
            extra_opts["http_headers"] = {"Cookie": custom_cookie}
        return await self._extract_ytdlp(clean_url, quality=quality, extra_opts=extra_opts)

    async def download_audio(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = await expand_redirect_url(url)
        file_id = uuid.uuid4().hex
        raw_audio = os.path.join(config.temp_dir, f"{file_id}_raw.audio")
        final_mp3 = os.path.join(config.temp_dir, f"{file_id}.mp3")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        scan = await self.fast_scan(clean_url)
        if scan and scan.get("audio_url"):
            audio_url = scan["audio_url"]
            cdn_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.douyin.com/",
            }
            ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                audio_url, raw_audio, max_size_mb, max_retries=2, headers=cdn_headers
            )
            if ok:
                converted = await convert_to_mp3(raw_audio, final_mp3)
                chosen_filepath = final_mp3 if converted else raw_audio
                cleanup_files(raw_audio if converted else None)

                filesize = os.path.getsize(chosen_filepath)
                filesize_mb = round(filesize / (1024 * 1024), 2)
                thumb_path = await download_thumbnail(scan.get("thumbnail_url"), output_thumb)

                return DownloadResult(
                    success=True,
                    filepath=chosen_filepath,
                    filename=f"{file_id}.mp3",
                    filesize_bytes=filesize,
                    filesize_mb=filesize_mb,
                    title=scan.get("title", "Douyin Audio"),
                    uploader=scan.get("uploader", "Douyin Creator"),
                    duration=scan.get("duration", 0),
                    thumbnail_path=thumb_path,
                    is_over_50mb=(filesize > TELEGRAM_FILE_LIMIT_BYTES),
                    is_audio=True,
                    direct_url=audio_url,
                    headers=cdn_headers,
                )

        # Fallback to yt-dlp audio
        custom_cookie = getattr(config, "douyin_cookie", "")
        extra_opts = {}
        if custom_cookie:
            extra_opts["http_headers"] = {"Cookie": custom_cookie}
        return await self._extract_ytdlp_audio(clean_url, max_size_mb=max_size_mb, extra_opts=extra_opts)

    async def download_images(self, url: str) -> Any:
        from bot.downloader import DownloadResult
        scan = await self.fast_scan(url)
        if scan and scan.get("image_urls"):
            return DownloadResult(
                success=True,
                is_image=True,
                image_urls=scan["image_urls"],
                title=scan.get("title", "Douyin Photos"),
                uploader=scan.get("uploader", "Douyin Creator"),
                direct_url=scan["image_urls"][0] if scan["image_urls"] else None,
            )
        return await super().download_images(url)

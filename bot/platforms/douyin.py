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
    get_cookie_file,
    TELEGRAM_FILE_LIMIT_BYTES,
)
from bot.cdn_api import download_direct_cdn_stream, expand_redirect_url

logger = logging.getLogger(__name__)


def get_douyin_cookie_string() -> str:
    """Combines custom DOUYIN_COOKIE from config and any douyin.com cookies from cookies.txt."""
    parts = []
    custom = getattr(config, "douyin_cookie", "").strip()
    if custom:
        parts.append(custom)
    cookie_file = get_cookie_file()
    if cookie_file and os.path.exists(cookie_file):
        try:
            with open(cookie_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip() and not line.startswith("#"):
                        fields = line.strip().split("\t")
                        if len(fields) >= 7 and "douyin.com" in fields[0]:
                            parts.append(f"{fields[5]}={fields[6]}")
        except Exception:
            pass
    return "; ".join(parts)


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
                async with session.post(url, json=post_data, headers=headers, timeout=aiohttp.ClientTimeout(total=2.5)) as resp:
                    if resp.status == 200:
                        cookie = resp.cookies.get("ttwid")
                        if cookie:
                            return cookie.value
        except Exception:
            pass
        return None

    async def _fetch_opensource_api(self, clean_url: str, aweme_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Queries configurable open-source Douyin API endpoint (e.g., Douyin_TikTok_Download_API microservice).
        Enabled when DOUYIN_API_URL is configured in environment / config.
        """
        api_base = getattr(config, "douyin_api_url", "").strip().rstrip("/")
        if not api_base:
            return None

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }

        # Try common REST endpoints of Douyin_TikTok_Download_API & related wrappers
        candidate_urls = [
            f"{api_base}/api/v1/parse?url={clean_url}",
            f"{api_base}/api/hybrid/video_data?url={clean_url}",
            f"{api_base}/api/douyin/video_data?url={clean_url}",
            f"{api_base}/?url={clean_url}",
        ]
        if aweme_id:
            candidate_urls.insert(1, f"{api_base}/api/v1/douyin/video?aweme_id={aweme_id}")

        for endpoint in candidate_urls:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(endpoint, headers=headers, timeout=aiohttp.ClientTimeout(total=3.0)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if not data:
                                continue

                            # Standard envelope unwrapping
                            item = data.get("data") or data.get("result") or data

                            title = item.get("desc") or item.get("title") or "Douyin Media"
                            author = (
                                item.get("author", {}).get("nickname")
                                if isinstance(item.get("author"), dict)
                                else item.get("author") or "Douyin Creator"
                            )

                            # 1. Image Album / Slideshow
                            images_raw = item.get("images") or item.get("image_urls") or []
                            if images_raw:
                                img_list = []
                                for img in images_raw:
                                    if isinstance(img, str):
                                        img_list.append(img)
                                    elif isinstance(img, dict):
                                        u = img.get("url_list", []) or [img.get("url")]
                                        if u and u[-1]:
                                            img_list.append(u[-1])
                                if img_list:
                                    logger.info(f"✅ Resolved Douyin photos via open-source API: {len(img_list)} images")
                                    return {
                                        "type": "images",
                                        "platform": self.display_name,
                                        "platform_id": self.platform_id,
                                        "image_urls": img_list,
                                        "title": title,
                                        "uploader": author,
                                        "audio_url": item.get("music", {}).get("play_url", {}).get("uri") if isinstance(item.get("music"), dict) else item.get("music_url"),
                                    }

                            # 2. HD Video
                            video_url = (
                                item.get("video_url")
                                or item.get("url")
                                or item.get("play_addr")
                                or item.get("nwm_video_url")
                                or item.get("nwm_video_url_HQ")
                            )
                            if not video_url and isinstance(item.get("video"), dict):
                                v_dict = item["video"]
                                p_addr = v_dict.get("play_addr", {})
                                if isinstance(p_addr, dict) and p_addr.get("url_list"):
                                    video_url = p_addr["url_list"][0].replace("playwm", "play")
                                elif v_dict.get("url_list"):
                                    video_url = v_dict["url_list"][0].replace("playwm", "play")

                            if video_url:
                                logger.info(f"✅ Resolved Douyin video via open-source API: {title[:30]}")
                                thumb = (
                                    item.get("thumbnail")
                                    or item.get("cover")
                                    or (item.get("video", {}).get("cover", {}).get("url_list", [None])[0] if isinstance(item.get("video"), dict) else None)
                                )
                                return {
                                    "type": "video",
                                    "platform": self.display_name,
                                    "platform_id": self.platform_id,
                                    "image_urls": [],
                                    "title": title,
                                    "uploader": author,
                                    "video_url": video_url,
                                    "audio_url": item.get("music", {}).get("play_url", {}).get("uri") if isinstance(item.get("music"), dict) else item.get("music_url"),
                                    "thumbnail_url": thumb,
                                }
            except Exception as e:
                logger.debug(f"Open-source API attempt failed for {endpoint}: {e}")

        return None

    async def _fetch_ssr_detail(self, clean_url: str, aweme_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Extracts embedded server-side-rendered JSON directly from Douyin / iesdouyin HTML.
        Zero signature or a_bogus required, parses direct CDN links instantly.
        """
        target_urls = [clean_url]
        if aweme_id:
            target_urls.extend([
                f"https://www.iesdouyin.com/share/video/{aweme_id}/",
                f"https://www.douyin.com/video/{aweme_id}",
            ])

        cookie_str = get_douyin_cookie_string()
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.douyin.com/",
        }
        if cookie_str:
            headers["Cookie"] = cookie_str

        for url in target_urls:
            try:
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=2.5)) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            if not html:
                                continue

                            # 1. Search for window._ROUTER_DATA
                            m_router = re.search(r'window\._ROUTER_DATA\s*=\s*(\{.+?\});?</script>', html, re.DOTALL)
                            if m_router:
                                try:
                                    router_data = json.loads(m_router.group(1))
                                    loader = router_data.get("loaderData", {})
                                    for k, v in loader.items():
                                        if isinstance(v, dict):
                                            item_res = v.get("videoInfoRes", {})
                                            item_list = item_res.get("item_list", []) if isinstance(item_res, dict) else []
                                            if item_list:
                                                parsed = self._normalize_item(item_list[0])
                                                if parsed:
                                                    logger.info(f"✅ Extracted Douyin media from _ROUTER_DATA: {parsed.get('title')}")
                                                    return parsed
                                except Exception:
                                    pass

                            # 2. Search for __UNIVERSAL_DATA_FOR_REHYDRATION__
                            m_univ = re.search(r'<script\s+id=[\'"]__UNIVERSAL_DATA_FOR_REHYDRATION__[\'"][^>]*>(.*?)</script>', html, re.DOTALL)
                            if m_univ:
                                try:
                                    univ_data = json.loads(m_univ.group(1))
                                    scope = univ_data.get("__DEFAULT_SCOPE__", {})
                                    for sk, sv in scope.items():
                                        if isinstance(sv, dict):
                                            aweme_detail = sv.get("awemeDetail") or sv.get("aweme_detail")
                                            if aweme_detail and isinstance(aweme_detail, dict):
                                                parsed = self._normalize_item(aweme_detail)
                                                if parsed:
                                                    logger.info(f"✅ Extracted Douyin media from __UNIVERSAL_DATA__: {parsed.get('title')}")
                                                    return parsed
                                except Exception:
                                    pass

                            # 3. Search for RENDER_DATA
                            m_render = re.search(r'<script\s+id=[\'"]RENDER_DATA[\'"][^>]*>(.*?)</script>', html, re.DOTALL)
                            if m_render:
                                try:
                                    import urllib.parse
                                    raw_render = urllib.parse.unquote(m_render.group(1))
                                    render_data = json.loads(raw_render)
                                    for rk, rv in render_data.items():
                                        if isinstance(rv, dict) and "video" in rv:
                                            parsed = self._normalize_item(rv)
                                            if parsed:
                                                logger.info(f"✅ Extracted Douyin media from RENDER_DATA: {parsed.get('title')}")
                                                return parsed
                                except Exception:
                                    pass
            except Exception as e:
                logger.debug(f"Douyin SSR fetch error for {url}: {e}")

        return None

    def _normalize_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalizes Douyin aweme item structure into standard platform payload."""
        if not item or not isinstance(item, dict):
            return None

        title = item.get("desc") or "Douyin Video"
        author = item.get("author", {}).get("nickname") or "Douyin Creator"

        # Check for photo slideshow / note
        images_raw = item.get("images") or []
        if images_raw:
            image_urls = []
            for img in images_raw:
                urls = img.get("url_list", []) if isinstance(img, dict) else []
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
                    "audio_url": item.get("music", {}).get("play_url", {}).get("uri") if isinstance(item.get("music"), dict) else None,
                }

        # Check for video
        video_info = item.get("video", {})
        play_addr = video_info.get("play_addr", {}) if isinstance(video_info, dict) else {}
        urls = play_addr.get("url_list", []) if isinstance(play_addr, dict) else []
        video_url = None
        if urls:
            video_url = urls[0].replace("playwm", "play")

        if video_url:
            return {
                "type": "video",
                "platform": self.display_name,
                "platform_id": self.platform_id,
                "image_urls": [],
                "title": title,
                "uploader": author,
                "video_url": video_url,
                "audio_url": item.get("music", {}).get("play_url", {}).get("uri") if isinstance(item.get("music"), dict) else None,
                "thumbnail_url": video_info.get("cover", {}).get("url_list", [None])[0] if isinstance(video_info, dict) else None,
            }

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
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=2.5)) as resp:
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
        """Queries the web detail API with guest ttwid session and combined cookies."""
        ttwid = await self._fetch_ttwid()
        cookie_header = f"ttwid={ttwid}; s_v_web_id=verify_guest;" if ttwid else "s_v_web_id=verify_guest;"
        custom_cookie = get_douyin_cookie_string()
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
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=2.5)) as resp:
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
                async with session.get(play_url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=2.5)) as resp:
                    location = resp.headers.get("Location")
                    if location:
                        return location
        except Exception:
            pass
        return None

    async def fast_scan(self, url: str) -> Optional[Dict[str, Any]]:
        """Fast scan Douyin links across all acceleration layers."""
        clean_url = await expand_redirect_url(url)
        aweme_id = self.extract_aweme_id(clean_url)

        # 1. Try Configured Open-Source API (Fastest if enabled)
        api_result = await self._fetch_opensource_api(clean_url, aweme_id)
        if api_result:
            return api_result

        # 2. Try SSR HTML Extraction (Direct CDN links, no sign)
        ssr_result = await self._fetch_ssr_detail(clean_url, aweme_id)
        if ssr_result:
            return ssr_result

        # 3. Try Web Detail API
        if aweme_id:
            detail = await self._fetch_web_detail(aweme_id)
            if detail:
                parsed = self._normalize_item(detail)
                if parsed:
                    return parsed

        # 4. Try mobile iteminfo
        if aweme_id:
            item = await self._fetch_iesdouyin_api(aweme_id)
            if item:
                parsed = self._normalize_item(item)
                if parsed:
                    return parsed

        # 5. Fallback: yt-dlp metadata dump
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
            }
            cookie_str = get_douyin_cookie_string()
            if cookie_str:
                ydl_opts["http_headers"] = {"Cookie": cookie_str}
            cookie_file = get_cookie_file()
            if cookie_file:
                ydl_opts["cookiefile"] = cookie_file
            if getattr(config, "proxy_url", None):
                ydl_opts["proxy"] = config.proxy_url

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

        # Route /note/ photo slideshow posts to download_images
        if "/note/" in clean_url:
            return await self.download_images(clean_url)

        scan = await self.fast_scan(clean_url)

        # If fast scan found direct watermark-free video CDN URL
        if scan and scan.get("video_url"):
            video_url = scan["video_url"]
            temp_file = os.path.join(config.temp_dir, f"douyin_{uuid.uuid4().hex}.mp4")
            cdn_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.douyin.com/",
            }

            # Download using accelerated stream immediately (no redundant HEAD stall)
            ok, downloaded_path_or_bytes, actual_mb, err = await download_direct_cdn_stream(
                video_url, temp_file, max_size_mb=config.max_file_size_mb, headers=cdn_headers
            )

            # If over 50MB Telegram limit, return direct URL for Cloud CDN streaming
            if err and "FILE_TOO_LARGE" in err:
                return DownloadResult(
                    success=True,
                    direct_url=video_url,
                    title=scan.get("title", "Douyin Video"),
                    uploader=scan.get("uploader", "Douyin Creator"),
                    filesize_bytes=int(actual_mb * 1024 * 1024) if actual_mb else 0,
                    filesize_mb=actual_mb,
                    is_over_50mb=True,
                    headers=cdn_headers,
                )

            if ok and os.path.exists(temp_file):
                f_size = os.path.getsize(temp_file)
                f_mb = round(f_size / (1024 * 1024), 2)
                return DownloadResult(
                    success=True,
                    filepath=temp_file,
                    title=scan.get("title", "Douyin Video"),
                    uploader=scan.get("uploader", "Douyin Creator"),
                    filesize_bytes=f_size,
                    filesize_mb=f_mb,
                    is_over_50mb=(f_size > TELEGRAM_FILE_LIMIT_BYTES),
                )

        # Fallback to yt-dlp worker in thread executor (correctly mapped to execute_ytdlp_video)
        cookie_str = get_douyin_cookie_string()
        extra_opts = {}
        if cookie_str:
            extra_opts["http_headers"] = {"Cookie": cookie_str}

        file_id = uuid.uuid4().hex
        output_base = os.path.join(config.temp_dir, f"douyin_{file_id}")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.execute_ytdlp_video, clean_url, output_base, 2000.0, extra_opts
        )

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

        # Fallback to yt-dlp audio worker in thread executor (correctly mapped to execute_ytdlp_audio)
        cookie_str = get_douyin_cookie_string()
        extra_opts = {}
        if cookie_str:
            extra_opts["http_headers"] = {"Cookie": cookie_str}

        output_base = os.path.join(config.temp_dir, f"douyin_{file_id}")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, extra_opts
        )

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

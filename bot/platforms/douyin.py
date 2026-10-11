import os
import re
import uuid
import asyncio
import logging
import time
from typing import Optional, Dict, Any, List
import aiohttp
import requests
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
from bot.platforms.douyin_abogus import ABogus
from bot.platforms.douyin_websign import sign as websign_sign, encode_pairs as websign_encode_pairs, UIFID_COOKIE_NAMES

logger = logging.getLogger(__name__)

_CACHED_TTWID: Optional[str] = None
_CACHED_TTWID_TIME: float = 0.0


def get_cached_ttwid_sync(user_agent: str) -> str:
    """Fetches and caches valid guest ttwid session cookie with 12h TTL."""
    global _CACHED_TTWID, _CACHED_TTWID_TIME
    now = time.time()
    if _CACHED_TTWID and (now - _CACHED_TTWID_TIME < 43200):
        return _CACHED_TTWID
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
    headers = {"User-Agent": user_agent, "Content-Type": "application/json"}
    try:
        r = requests.post(url, json=post_data, headers=headers, timeout=5)
        if r.status_code == 200:
            c = r.cookies.get("ttwid")
            if c:
                _CACHED_TTWID = c
                _CACHED_TTWID_TIME = now
                return _CACHED_TTWID
    except Exception as e:
        logger.debug(f"Failed to fetch ttwid: {e}")
    return ""


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
    Features multi-tiered extraction:
      1. Official Mobile Feed API (api.amemv.com): ultra-fast, direct watermark-free CDN links.
      2. Native ABogus + Websign Signed Web Detail: full high-res photo albums & notes parser.
      3. Open-source backup microservice (if DOUYIN_API_URL configured).
      4. SSR HTML fallback & yt-dlp backup.
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
        """Async wrapper for ttwid retrieval."""
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, get_cached_ttwid_sync, ua)

    async def _fetch_amemv_feed(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        """
        Queries official Douyin mobile feed endpoints (high-speed, direct CDN, zero cookies).
        Extremely reliable for Douyin videos.
        """
        endpoints = [
            f"https://api.amemv.com/aweme/v1/feed/?aweme_id={aweme_id}",
            f"https://api5-normal-c-lq.amemv.com/aweme/v1/feed/?aweme_id={aweme_id}",
        ]
        headers = {
            "User-Agent": "com.ss.android.ugc.aweme/260101 (Linux; U; Android 10; zh_CN; MI 9; Build/QKQ1.190825.002; Cronet/TTNetVersion:a6015b3c 2023-03-24 QuicVersion:4690989b 2023-03-14)",
            "Accept": "application/json",
        }
        for ep in endpoints:
            try:
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(ep, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            aweme_list = data.get("aweme_list", []) if isinstance(data, dict) else []
                            for item in aweme_list:
                                if str(item.get("aweme_id")) == str(aweme_id) or len(aweme_list) == 1:
                                    parsed = self._normalize_item(item)
                                    if parsed:
                                        logger.info(f"✅ Resolved Douyin media via amemv feed: {parsed.get('title')}")
                                        return parsed
            except Exception as e:
                logger.debug(f"amemv feed error for {ep}: {e}")
        return None

    def _execute_signed_web_detail(self, aweme_id: str, is_note: bool) -> Optional[Dict[str, Any]]:
        """
        Queries Douyin Web Detail API using pure Python native a_bogus + x-secsdk-web-signature + ttwid.
        Resolves Douyin photo notes (image slideshows) and acts as bulletproof video backup.
        """
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        signer = ABogus(ua)
        ttwid = get_cached_ttwid_sync(ua)

        session = requests.Session()
        custom_cookie = get_douyin_cookie_string()

        # Seed session cookies
        init_url = f"https://www.douyin.com/note/{aweme_id}" if is_note else f"https://www.douyin.com/video/{aweme_id}"
        try:
            session.get(init_url, headers={"User-Agent": ua}, timeout=4)
        except Exception:
            pass

        if ttwid:
            session.cookies.set("ttwid", ttwid, domain=".douyin.com")

        uifid = session.cookies.get("UIFID_TEMP")
        if not uifid:
            for name in UIFID_COOKIE_NAMES:
                val = session.cookies.get(name)
                if val:
                    uifid = val
                    break
        if not uifid:
            uifid = "8d1db381e6ede33be9bff1834f9f1e38c00552f2cfff1fad413024126227b7e846c3ec45fc0b9deebfa7af18307018bb19f4fdfa0757ec53638e3e0619ab4bc19e360a6fdea65bb48af0abdf012f103c"

        pairs = [
            ("device_platform", "webapp"),
            ("aid", "6383"),
            ("channel", "channel_pc_web"),
            ("aweme_id", str(aweme_id)),
            ("update_version_code", "170400"),
            ("pc_client_type", "1"),
            ("version_code", "290100"),
            ("version_name", "29.1.0"),
            ("cookie_enabled", "true"),
            ("screen_width", "1920"),
            ("screen_height", "1080"),
            ("browser_language", "zh-CN"),
            ("browser_platform", "Win32"),
            ("browser_name", "Chrome"),
            ("browser_version", "122.0.0.0"),
            ("browser_online", "true"),
        ]

        base_query = websign_encode_pairs(pairs)
        bogus_val = signer.get_value(base_query)
        pairs.append(("a_bogus", bogus_val))

        signed_query, sig, sig_headers = websign_sign(pairs, uifid)
        api_url = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?{signed_query}"

        req_headers = {
            "User-Agent": ua,
            "Referer": init_url,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        req_headers.update(sig_headers)
        if custom_cookie:
            for item in custom_cookie.split(";"):
                if "=" in item:
                    k, v = item.strip().split("=", 1)
                    session.cookies.set(k.strip(), v.strip(), domain=".douyin.com")

        try:
            resp = session.get(api_url, headers=req_headers, timeout=6)
            if resp.status_code == 200 and resp.text:
                data = resp.json()
                detail = data.get("aweme_detail")
                if detail:
                    parsed = self._normalize_item(detail)
                    if parsed:
                        logger.info(f"✅ Resolved Douyin media via signed web detail: {parsed.get('title')}")
                        return parsed
        except Exception as e:
            logger.debug(f"Signed web detail error for {aweme_id}: {e}")

        return None

    async def _fetch_signed_web_detail(self, aweme_id: str, is_note: bool = False) -> Optional[Dict[str, Any]]:
        """Async executor wrapper for native signed web detail query."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._execute_signed_web_detail, aweme_id, is_note)

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

                            item = data.get("data") or data.get("result") or data

                            title = item.get("desc") or item.get("title") or "Douyin Media"
                            author = (
                                item.get("author", {}).get("nickname")
                                if isinstance(item.get("author"), dict)
                                else item.get("author") or "Douyin Creator"
                            )

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

        title = item.get("desc") or "Douyin Media"
        author = item.get("author", {}).get("nickname") or "Douyin Creator"

        # 1. Check for photo slideshow / note
        images_raw = item.get("images") or []
        if images_raw:
            image_urls = []
            for img in images_raw:
                if isinstance(img, dict):
                    urls = img.get("url_list", [])
                    if urls:
                        image_urls.append(urls[-1])
                elif isinstance(img, str):
                    image_urls.append(img)
            if image_urls:
                music_url = None
                music = item.get("music")
                if isinstance(music, dict):
                    m_play = music.get("play_url", {})
                    if isinstance(m_play, dict):
                        m_list = m_play.get("url_list", [])
                        music_url = m_list[0] if m_list else m_play.get("uri")
                    elif music.get("play_url"):
                        music_url = music["play_url"]

                return {
                    "type": "images",
                    "platform": self.display_name,
                    "platform_id": self.platform_id,
                    "image_urls": image_urls,
                    "title": title,
                    "uploader": author,
                    "audio_url": music_url,
                }

        # 2. Check for video
        video_info = item.get("video", {})
        play_addr = video_info.get("play_addr", {}) if isinstance(video_info, dict) else {}
        urls = play_addr.get("url_list", []) if isinstance(play_addr, dict) else []
        video_url = None
        if urls:
            video_url = urls[0].replace("playwm", "play")

        if video_url:
            music_url = None
            music = item.get("music")
            if isinstance(music, dict):
                m_play = music.get("play_url", {})
                if isinstance(m_play, dict):
                    m_list = m_play.get("url_list", [])
                    music_url = m_list[0] if m_list else m_play.get("uri")
                elif music.get("play_url"):
                    music_url = music["play_url"]

            return {
                "type": "video",
                "platform": self.display_name,
                "platform_id": self.platform_id,
                "image_urls": [],
                "title": title,
                "uploader": author,
                "video_url": video_url,
                "audio_url": music_url,
                "thumbnail_url": video_info.get("cover", {}).get("url_list", [None])[0] if isinstance(video_info, dict) else None,
            }

        return None

    async def fast_scan(self, url: str) -> Optional[Dict[str, Any]]:
        """Fast scan Douyin links across all acceleration and backup layers."""
        clean_url = await expand_redirect_url(url)
        aweme_id = self.extract_aweme_id(clean_url)
        is_note = ("/note/" in clean_url)

        # 1. Configured Open-Source API (Fastest if enabled)
        api_result = await self._fetch_opensource_api(clean_url, aweme_id)
        if api_result:
            return api_result

        # 2. For Video: Primary Fast Path via official mobile feed (zero delay, direct CDN)
        if aweme_id and not is_note:
            amemv_result = await self._fetch_amemv_feed(aweme_id)
            if amemv_result:
                return amemv_result

        # 3. For Note & Video Backup: Signed Web Detail API (ABogus + websign + ttwid)
        if aweme_id:
            signed_result = await self._fetch_signed_web_detail(aweme_id, is_note=is_note)
            if signed_result:
                return signed_result

        # 4. Fallback for Video if is_note was True but signed detail had no images
        if aweme_id and is_note:
            amemv_result = await self._fetch_amemv_feed(aweme_id)
            if amemv_result:
                return amemv_result

        # 5. SSR HTML Extraction (Direct CDN links, no sign)
        ssr_result = await self._fetch_ssr_detail(clean_url, aweme_id)
        if ssr_result:
            return ssr_result

        # 6. Fallback: yt-dlp metadata dump
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

        # If scan found images, route to download_images
        if scan and scan.get("image_urls"):
            return await self.download_images(clean_url)

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

        # Fallback to yt-dlp worker in thread executor
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

        # Fallback to yt-dlp audio worker in thread executor
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
        clean_url = await expand_redirect_url(url)
        scan = await self.fast_scan(clean_url)
        if scan and scan.get("image_urls"):
            return DownloadResult(
                success=True,
                is_image=True,
                image_urls=scan["image_urls"],
                title=scan.get("title", "Douyin Photos"),
                uploader=scan.get("uploader", "Douyin Creator"),
                direct_url=scan["image_urls"][0] if scan["image_urls"] else None,
            )
        # If user passed a video link to image download, return video download
        if scan and scan.get("video_url"):
            return await self.download_video(clean_url)

        return await super().download_images(clean_url)

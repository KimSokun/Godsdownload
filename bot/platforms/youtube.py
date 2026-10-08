import os
import re
import time
import uuid
import asyncio
import logging
import concurrent.futures
from typing import Optional, Dict, Any, List
import aiohttp
import yt_dlp

from bot.config import config
from bot.platforms.base import (
    BasePlatform,
    get_cookie_file,
    download_thumbnail,
    convert_to_mp3,
    cleanup_files,
    TELEGRAM_FILE_LIMIT_BYTES,
)
from bot.cdn_api import download_direct_cdn_stream, fetch_cobalt_mirror, expand_redirect_url

logger = logging.getLogger(__name__)

INVIDIOUS_INSTANCES = [
    "https://invidious.f5.si",
    "https://invidious.nerdvpn.de",
    "https://inv.tux.pizza",
]

YOUTUBE_ID_REGEX = re.compile(
    r'(?:v=|\/|youtu\.be\/|embed\/|shorts\/|\/v\/|watch\?v=|\&v=)([a-zA-Z0-9_-]{11})'
)

PROXY_CACHE_FILE = "/www/wwwroot/Godsdownload/data/working_youtube_proxy.txt"
DEFAULT_PROXIES = [
    "http://116.101.54.121:8080",
]

_cached_working_proxy: Optional[str] = None
_cached_proxy_time: float = 0.0
_proxy_lock = asyncio.Lock()


def extract_youtube_id(url: str) -> Optional[str]:
    """Extracts YouTube 11-char video ID from any YouTube URL."""
    if not url:
        return None
    match = YOUTUBE_ID_REGEX.search(url)
    return match.group(1) if match else None


def clean_youtube_url(url: str) -> str:
    """Normalizes YouTube URL (shorts, youtu.be) and strips playlist/radio params."""
    yt_id = extract_youtube_id(url)
    if yt_id:
        return f"https://www.youtube.com/watch?v={yt_id}"
    return url


def _verify_proxy_candidate(p_url: str) -> Optional[str]:
    """Synchronous test of a proxy against YouTube metadata extraction using yt-dlp."""
    ydl_opts = {
        "proxy": p_url,
        "socket_timeout": 5,
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "remote_components": ["ejs:github"],
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info("https://www.youtube.com/watch?v=kJQP7kiw5Fk", download=False)
            if info and info.get("title"):
                return p_url
    except Exception:
        pass
    return None


async def get_working_proxy(force_refresh: bool = False) -> Optional[str]:
    """
    Finds and caches a working proxy for YouTube.
    1. Uses manual YOUTUBE_PROXY or PROXY_URL from .env if defined.
    2. Checks in-memory and disk cache.
    3. Tries default known-working proxies.
    4. Auto-scans multiple online free proxy lists and verifies using yt-dlp.
    """
    global _cached_working_proxy, _cached_proxy_time

    manual_proxy = getattr(config, "youtube_proxy", "") or getattr(config, "proxy_url", "")
    if manual_proxy:
        return manual_proxy

    now = time.time()
    if not force_refresh and _cached_working_proxy and (now - _cached_proxy_time < 3600):
        return _cached_working_proxy

    if not force_refresh and os.path.exists(PROXY_CACHE_FILE):
        try:
            with open(PROXY_CACHE_FILE, "r") as f:
                saved = f.read().strip()
            if saved and (now - os.path.getmtime(PROXY_CACHE_FILE) < 3600):
                _cached_working_proxy = saved
                _cached_proxy_time = now
                return saved
        except Exception:
            pass

    async with _proxy_lock:
        if not force_refresh and _cached_working_proxy and (time.time() - _cached_proxy_time < 3600):
            return _cached_working_proxy

        loop = asyncio.get_running_loop()

        for def_proxy in DEFAULT_PROXIES:
            ok = await loop.run_in_executor(None, _verify_proxy_candidate, def_proxy)
            if ok:
                _cached_working_proxy = ok
                _cached_proxy_time = time.time()
                try:
                    os.makedirs(os.path.dirname(PROXY_CACHE_FILE), exist_ok=True)
                    with open(PROXY_CACHE_FILE, "w") as f:
                        f.write(ok)
                except Exception:
                    pass
                logger.info(f"Verified working default YouTube proxy: {ok}")
                return ok

        logger.info("Scanning for fresh working YouTube proxy...")
        sources = [
            "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
            "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
            "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=yes&anonymity=elite",
        ]
        candidates = []
        try:
            async with aiohttp.ClientSession() as session:
                for src in sources:
                    try:
                        async with session.get(src, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                            if resp.status == 200:
                                text = await resp.text()
                                for line in text.splitlines():
                                    line = line.strip()
                                    if line and ":" in line and not line.startswith("#"):
                                        p = line if line.startswith("http") else f"http://{line}"
                                        if p not in candidates:
                                            candidates.append(p)
                                        if len(candidates) >= 50:
                                            break
                    except Exception:
                        pass
                    if len(candidates) >= 50:
                        break
        except Exception as e:
            logger.warning(f"Proxy sources fetch error: {e}")

        if candidates:
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(_verify_proxy_candidate, p) for p in candidates[:30]]
                for f in concurrent.futures.as_completed(futures):
                    res = f.result()
                    if res:
                        _cached_working_proxy = res
                        _cached_proxy_time = time.time()
                        try:
                            os.makedirs(os.path.dirname(PROXY_CACHE_FILE), exist_ok=True)
                            with open(PROXY_CACHE_FILE, "w") as out_f:
                                out_f.write(res)
                        except Exception:
                            pass
                        logger.info(f"Discovered working YouTube proxy: {res}")
                        return res

    return _cached_working_proxy or (DEFAULT_PROXIES[0] if DEFAULT_PROXIES else None)


def invalidate_proxy(proxy_url: str):
    """Invalidates failing proxy so a new one is selected on next request."""
    global _cached_working_proxy, _cached_proxy_time
    if _cached_working_proxy == proxy_url:
        _cached_working_proxy = None
        _cached_proxy_time = 0.0
    if os.path.exists(PROXY_CACHE_FILE):
        try:
            os.remove(PROXY_CACHE_FILE)
        except Exception:
            pass


class YouTubePlatform(BasePlatform):
    platform_id = "youtube"
    display_name = "📺 YouTube"
    patterns = [
        re.compile(r'(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)'),
    ]

    async def _query_single_invidious(self, instance: str, yt_id: str, is_audio: bool) -> Optional[Dict[str, Any]]:
        api_url = f"{instance}/api/v1/videos/{yt_id}"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers=headers, timeout=aiohttp.ClientTimeout(total=3.0)) as resp:
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

    async def _resolve_invidious(self, yt_id: str, is_audio: bool) -> Optional[Dict[str, Any]]:
        tasks = [self._query_single_invidious(inst, yt_id, is_audio) for inst in INVIDIOUS_INSTANCES]
        try:
            for coro in asyncio.as_completed(tasks, timeout=3.5):
                try:
                    res = await coro
                    if res and (res.get("video_url") or (is_audio and res.get("audio_url"))):
                        return res
                except Exception:
                    pass
        except asyncio.TimeoutError:
            pass
        return None

    def _get_youtube_ydl_opts(self, is_audio: bool, download_path_template: str, proxy: Optional[str] = None) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "remote_components": ["ejs:github"],
            "noplaylist": True,
            "concurrent_fragment_downloads": 4,
            "buffersize": 1048576,        # 1MB buffer
            "http_chunk_size": 10485760,  # 10MB chunk
        }
        if is_audio:
            opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        else:
            opts["format"] = (
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo[height<=1080]+bestaudio/"
                "best[height<=1080][ext=mp4]/"
                "bestvideo[height<=720]+bestaudio/"
                "best[height<=720]/"
                "best"
            )
            opts["merge_output_format"] = "mp4"

        if proxy:
            opts["proxy"] = proxy

        return opts

    async def download_video(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = clean_youtube_url(url)
        file_id = uuid.uuid4().hex
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()

        # Step 1: Ultra-fast Direct Gigabit Download Attempt (No Proxy Bottleneck)
        # If server connection is unblocked or valid cookies are present, this completes in 1-2 seconds!
        direct_opts = self._get_youtube_ydl_opts(is_audio=False, download_path_template=output_base, proxy=None)
        res = await loop.run_in_executor(None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb, direct_opts)
        if res and res.success:
            return res

        # Step 2: If Direct attempt is blocked by YouTube bot protection, seamlessly use smart proxy
        err_msg = str(getattr(res, "error", "")).lower()
        if not res or not res.success or "bot" in err_msg or "sign in" in err_msg or "login" in err_msg or "403" in err_msg:
            proxy = await get_working_proxy()
            if proxy:
                logger.info(f"Direct download bot-blocked ({err_msg}). Retrying with proxy: {proxy}")
                proxy_opts = self._get_youtube_ydl_opts(is_audio=False, download_path_template=output_base, proxy=proxy)
                proxy_res = await loop.run_in_executor(None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb, proxy_opts)
                if proxy_res and proxy_res.success:
                    return proxy_res
                else:
                    proxy_err = str(getattr(proxy_res, "error", "")).lower()
                    if "connection" in proxy_err or "timed out" in proxy_err:
                        invalidate_proxy(proxy)
                        new_proxy = await get_working_proxy(force_refresh=True)
                        if new_proxy and new_proxy != proxy:
                            retry_opts = self._get_youtube_ydl_opts(is_audio=False, download_path_template=output_base, proxy=new_proxy)
                            retry_res = await loop.run_in_executor(None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb, retry_opts)
                            if retry_res and retry_res.success:
                                return retry_res

        output_video = os.path.join(config.temp_dir, f"{file_id}.mp4")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        # Step 3: Cobalt CDN mirror fallback
        try:
            cobalt_info = await fetch_cobalt_mirror(clean_url, is_audio=False, timeout=3.5)
            if cobalt_info and cobalt_info.get("video_url"):
                cdn_url = cobalt_info["video_url"]
                ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                    cdn_url, output_video, max_size_mb, max_retries=2
                )
                if ok:
                    thumb_path = await download_thumbnail(cobalt_info.get("thumbnail_url"), output_thumb)
                    return DownloadResult(
                        success=True,
                        filepath=output_video,
                        filename=f"{file_id}.mp4",
                        filesize_bytes=total_bytes,
                        filesize_mb=filesize_mb,
                        title=cobalt_info.get("title", "YouTube Video"),
                        uploader=cobalt_info.get("uploader", "YouTube Channel"),
                        duration=cobalt_info.get("duration", 0),
                        thumbnail_path=thumb_path,
                        is_over_50mb=(total_bytes > TELEGRAM_FILE_LIMIT_BYTES),
                        is_audio=False,
                        direct_url=cdn_url,
                    )
                elif err and "FILE_TOO_LARGE" in err:
                    parts = err.split(":")
                    est = float(parts[1]) if len(parts) > 1 else 100.0
                    return DownloadResult(
                        success=True,
                        filesize_mb=est,
                        title=cobalt_info.get("title", "YouTube Video"),
                        uploader=cobalt_info.get("uploader", "YouTube Channel"),
                        duration=cobalt_info.get("duration", 0),
                        is_over_50mb=True,
                        is_audio=False,
                        direct_url=cdn_url,
                    )
        except Exception as e:
            logger.debug(f"Cobalt mirror query failed for YouTube {clean_url}: {e}")

        # Step 4: Invidious CDN mirror fallback
        yt_id = extract_youtube_id(clean_url)
        if yt_id:
            try:
                cdn_info = await self._resolve_invidious(yt_id, is_audio=False)
                if cdn_info and cdn_info.get("video_url"):
                    cdn_url = cdn_info["video_url"]
                    ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                        cdn_url, output_video, max_size_mb, max_retries=2
                    )
                    if ok:
                        thumb_path = await download_thumbnail(cdn_info.get("thumbnail_url"), output_thumb)
                        return DownloadResult(
                            success=True,
                            filepath=output_video,
                            filename=f"{file_id}.mp4",
                            filesize_bytes=total_bytes,
                            filesize_mb=filesize_mb,
                            title=cdn_info.get("title", "YouTube Video"),
                            uploader=cdn_info.get("uploader", "YouTube Channel"),
                            duration=cdn_info.get("duration", 0),
                            thumbnail_path=thumb_path,
                            is_over_50mb=(total_bytes > TELEGRAM_FILE_LIMIT_BYTES),
                            is_audio=False,
                            direct_url=cdn_url,
                        )
                    elif err and "FILE_TOO_LARGE" in err:
                        parts = err.split(":")
                        est = float(parts[1]) if len(parts) > 1 else 100.0
                        return DownloadResult(
                            success=True,
                            filesize_mb=est,
                            title=cdn_info.get("title", "YouTube Video"),
                            uploader=cdn_info.get("uploader", "YouTube Channel"),
                            duration=cdn_info.get("duration", 0),
                            is_over_50mb=True,
                            is_audio=False,
                            direct_url=cdn_url,
                        )
            except Exception as e:
                logger.warning(f"Invidious mirror failed: {e}")

        return res or DownloadResult(success=False, error="YouTube download failed.")

    async def download_audio(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = clean_youtube_url(url)
        file_id = uuid.uuid4().hex
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()

        # Step 1: Direct Fast Attempt
        direct_opts = self._get_youtube_ydl_opts(is_audio=True, download_path_template=output_base, proxy=None)
        res = await loop.run_in_executor(None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, direct_opts)
        if res and res.success:
            return res

        # Step 2: Proxy retry if bot-blocked
        err_msg = str(getattr(res, "error", "")).lower()
        if not res or not res.success or "bot" in err_msg or "sign in" in err_msg or "login" in err_msg or "403" in err_msg:
            proxy = await get_working_proxy()
            if proxy:
                logger.info(f"Direct audio download bot-blocked ({err_msg}). Retrying with proxy: {proxy}")
                proxy_opts = self._get_youtube_ydl_opts(is_audio=True, download_path_template=output_base, proxy=proxy)
                proxy_res = await loop.run_in_executor(None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, proxy_opts)
                if proxy_res and proxy_res.success:
                    return proxy_res
                else:
                    proxy_err = str(getattr(proxy_res, "error", "")).lower()
                    if "connection" in proxy_err or "timed out" in proxy_err:
                        invalidate_proxy(proxy)
                        new_proxy = await get_working_proxy(force_refresh=True)
                        if new_proxy and new_proxy != proxy:
                            retry_opts = self._get_youtube_ydl_opts(is_audio=True, download_path_template=output_base, proxy=new_proxy)
                            retry_res = await loop.run_in_executor(None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, retry_opts)
                            if retry_res and retry_res.success:
                                return retry_res

        raw_audio = os.path.join(config.temp_dir, f"{file_id}_raw.audio")
        final_mp3 = os.path.join(config.temp_dir, f"{file_id}.mp3")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        # Step 3: Cobalt CDN audio mirror fallback
        try:
            cobalt_info = await fetch_cobalt_mirror(clean_url, is_audio=True, timeout=3.5)
            if cobalt_info and cobalt_info.get("audio_url"):
                cdn_url = cobalt_info["audio_url"]
                ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                    cdn_url, raw_audio, max_size_mb, max_retries=2
                )
                if ok:
                    converted = await convert_to_mp3(raw_audio, final_mp3)
                    chosen_filepath = final_mp3 if converted else raw_audio
                    cleanup_files(raw_audio if converted else None)

                    filesize = os.path.getsize(chosen_filepath)
                    filesize_mb = round(filesize / (1024 * 1024), 2)
                    thumb_path = await download_thumbnail(cobalt_info.get("thumbnail_url"), output_thumb)
                    return DownloadResult(
                        success=True,
                        filepath=chosen_filepath,
                        filename=f"{file_id}.mp3",
                        filesize_bytes=filesize,
                        filesize_mb=filesize_mb,
                        title=cobalt_info.get("title", "YouTube Audio"),
                        uploader=cobalt_info.get("uploader", "YouTube Channel"),
                        duration=cobalt_info.get("duration", 0),
                        thumbnail_path=thumb_path,
                        is_over_50mb=(filesize > TELEGRAM_FILE_LIMIT_BYTES),
                        is_audio=True,
                        direct_url=cdn_url,
                    )
                elif err and "FILE_TOO_LARGE" in err:
                    parts = err.split(":")
                    est = float(parts[1]) if len(parts) > 1 else 100.0
                    return DownloadResult(
                        success=True,
                        filesize_mb=est,
                        title=cobalt_info.get("title", "YouTube Audio"),
                        uploader=cobalt_info.get("uploader", "YouTube Channel"),
                        duration=cobalt_info.get("duration", 0),
                        is_over_50mb=True,
                        is_audio=True,
                        direct_url=cdn_url,
                    )
        except Exception as e:
            logger.debug(f"Cobalt mirror audio failed for YouTube {clean_url}: {e}")

        # Step 4: Invidious CDN mirror fallback
        yt_id = extract_youtube_id(clean_url)
        if yt_id:
            try:
                cdn_info = await self._resolve_invidious(yt_id, is_audio=True)
                if cdn_info and cdn_info.get("audio_url"):
                    cdn_url = cdn_info["audio_url"]
                    ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                        cdn_url, raw_audio, max_size_mb, max_retries=2
                    )
                    if ok:
                        converted = await convert_to_mp3(raw_audio, final_mp3)
                        chosen_filepath = final_mp3 if converted else raw_audio
                        cleanup_files(raw_audio if converted else None)

                        filesize = os.path.getsize(chosen_filepath)
                        filesize_mb = round(filesize / (1024 * 1024), 2)
                        thumb_path = await download_thumbnail(cdn_info.get("thumbnail_url"), output_thumb)
                        return DownloadResult(
                            success=True,
                            filepath=chosen_filepath,
                            filename=f"{file_id}.mp3",
                            filesize_bytes=filesize,
                            filesize_mb=filesize_mb,
                            title=cdn_info.get("title", "YouTube Audio"),
                            uploader=cdn_info.get("uploader", "YouTube Channel"),
                            duration=cdn_info.get("duration", 0),
                            thumbnail_path=thumb_path,
                            is_over_50mb=(filesize > TELEGRAM_FILE_LIMIT_BYTES),
                            is_audio=True,
                            direct_url=cdn_url,
                        )
                    elif err and "FILE_TOO_LARGE" in err:
                        parts = err.split(":")
                        est = float(parts[1]) if len(parts) > 1 else 100.0
                        return DownloadResult(
                            success=True,
                            filesize_mb=est,
                            title=cdn_info.get("title", "YouTube Audio"),
                            uploader=cdn_info.get("uploader", "YouTube Channel"),
                            duration=cdn_info.get("duration", 0),
                            is_over_50mb=True,
                            is_audio=True,
                            direct_url=cdn_url,
                        )
            except Exception as e:
                logger.warning(f"Invidious mirror audio failed: {e}")

        return res or DownloadResult(success=False, error="YouTube audio download failed.")

import os
import re
import uuid
import asyncio
import logging
from typing import Optional, Dict, Any, List
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
from bot.cdn_api import fetch_cobalt_mirror, download_direct_cdn_stream

logger = logging.getLogger(__name__)

# Enhanced YouTube regex strictly capturing 11-char video ID across watch, shorts, youtu.be, embed, mobile, music
YOUTUBE_ID_REGEX = re.compile(
    r'(?:(?:v|vi|embed|shorts)\/|\/(?:v|vi|embed|shorts)\/|(?:watch\?v=)|(?:watch\?.+&v=)|(?:youtu\.be\/)|(?:\/e\/))([a-zA-Z0-9_-]{11})'
)


def extract_youtube_id(url: str) -> Optional[str]:
    """Extracts YouTube 11-char video ID from any YouTube URL (including shorts, youtu.be, embed, mobile)."""
    if not url:
        return None
    match = YOUTUBE_ID_REGEX.search(url)
    return match.group(1) if match else None


def clean_youtube_url(url: str) -> str:
    """Normalizes YouTube URL (shorts, youtu.be) and strips tracking & playlist parameters."""
    yt_id = extract_youtube_id(url)
    if yt_id:
        return f"https://www.youtube.com/watch?v={yt_id}"
    return url


def get_youtube_proxy() -> Optional[str]:
    """Returns user-configured YouTube proxy, global proxy from config, or auto-detects local WARP proxy on 127.0.0.1:40000."""
    configured = getattr(config, "youtube_proxy", "").strip() or getattr(config, "proxy_url", "").strip()
    if configured:
        return configured
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", 40000)) == 0:
                return "socks5://127.0.0.1:40000"
    except Exception:
        pass
    return None


class YouTubePlatform(BasePlatform):
    platform_id = "youtube"
    display_name = "📺 YouTube"
    patterns = [
        re.compile(r'(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)'),
    ]

    def _get_youtube_ydl_opts(self, is_audio: bool, download_path_template: str, proxy: Optional[str] = None) -> Dict[str, Any]:
        """
        Highly optimized yt-dlp options for YouTube and YouTube Shorts.
        Integrates local bgutil POT provider (127.0.0.1:4416) and multi-client extraction.
        """
        opts: Dict[str, Any] = {
            "noplaylist": True,
            "concurrent_fragment_downloads": 4,
            "buffersize": 1048576,        # 1MB buffer
            "http_chunk_size": 10485760,  # 10MB chunk
            "socket_timeout": 15,
            "extractor_args": {
                "youtubepot-bgutilhttp": {
                    "base_url": ["http://127.0.0.1:4416"],
                },
                "youtube": {
                    "player_client": ["android_vr", "android", "ios", "mweb", "web"],
                }
            },
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

        cookie_file = get_cookie_file()
        if cookie_file:
            opts["cookiefile"] = cookie_file

        if proxy:
            opts["proxy"] = proxy

        return opts

    async def download_video(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = clean_youtube_url(url)
        file_id = uuid.uuid4().hex
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()

        # Step 1: Open-Source High-Speed Direct CDN Resolver (Zero Cookies, Bypasses Bot Protection)
        try:
            cdn_info = await fetch_cobalt_mirror(clean_url, is_audio=False, timeout=12.0)
            if cdn_info and cdn_info.get("video_url"):
                cdn_url = cdn_info["video_url"]
                output_video = f"{output_base}.mp4"
                output_thumb = f"{output_base}.jpg"
                ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                    cdn_url, output_video, max_size_mb, max_retries=2
                )
                if ok:
                    thumb_path = await download_thumbnail(cdn_info.get("thumbnail_url"), output_thumb)
                    logger.info(f"✅ YouTube Direct CDN succeeded for {clean_url}: {filesize_mb} MB")
                    return DownloadResult(
                        success=True,
                        filepath=output_video,
                        filename=f"{file_id}.mp4",
                        filesize_bytes=total_bytes,
                        filesize_mb=filesize_mb,
                        title=cdn_info.get("title", "YouTube Video"),
                        uploader=cdn_info.get("uploader", "YouTube Creator"),
                        duration=cdn_info.get("duration", 0),
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
                        title=cdn_info.get("title", "YouTube Video"),
                        uploader=cdn_info.get("uploader", "YouTube Creator"),
                        filesize_mb=filesize_mb,
                        duration=cdn_info.get("duration", 0),
                        thumbnail_path=cdn_info.get("thumbnail_url"),
                        is_audio=False,
                    )
        except Exception as e:
            logger.debug(f"Direct CDN attempt for YouTube failed ({e}), falling back to yt-dlp...")

        configured_proxy = get_youtube_proxy()

        # Step 2: yt-dlp download attempt (using configured proxy/WARP if present, else direct)
        first_proxy = configured_proxy if configured_proxy else None
        first_opts = self._get_youtube_ydl_opts(is_audio=False, download_path_template=output_base, proxy=first_proxy)
        res = await loop.run_in_executor(None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb, first_opts)
        if res and res.success:
            return res

        err_msg = str(getattr(res, "error", "")).lower()

        # Step 3: yt-dlp Fallback attempt if first attempt failed
        fallback_proxy = None if first_proxy else configured_proxy
        if fallback_proxy is not None or (first_proxy and not res.success):
            logger.info(f"Primary YouTube download attempt failed ({err_msg}). Trying fallback...")
            fallback_opts = self._get_youtube_ydl_opts(is_audio=False, download_path_template=output_base, proxy=fallback_proxy)
            fallback_res = await loop.run_in_executor(None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb, fallback_opts)
            if fallback_res and fallback_res.success:
                return fallback_res
            err_msg = str(getattr(fallback_res, "error", "")).lower()

        # Step 4: Fast-fail without freezing if bot protection requires cookies/login
        if any(k in err_msg for k in ["sign in to confirm you're not a bot", "sign in", "login_required", "bot"]):
            return DownloadResult(
                success=False,
                error="YOUTUBE_BOT_PROTECTION"
            )

        return res or DownloadResult(success=False, error="YouTube download failed.")

    async def download_audio(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = clean_youtube_url(url)
        file_id = uuid.uuid4().hex
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()

        # Step 1: Open-Source High-Speed Direct CDN Resolver (Zero Cookies, Bypasses Bot Protection)
        try:
            cdn_info = await fetch_cobalt_mirror(clean_url, is_audio=True, timeout=12.0)
            if cdn_info and cdn_info.get("audio_url"):
                cdn_url = cdn_info["audio_url"]
                output_audio = f"{output_base}.mp3"
                output_thumb = f"{output_base}.jpg"
                ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                    cdn_url, output_audio, max_size_mb, max_retries=2
                )
                if ok:
                    thumb_path = await download_thumbnail(cdn_info.get("thumbnail_url"), output_thumb)
                    logger.info(f"✅ YouTube Direct CDN audio succeeded for {clean_url}: {filesize_mb} MB")
                    return DownloadResult(
                        success=True,
                        filepath=output_audio,
                        filename=f"{file_id}.mp3",
                        filesize_bytes=total_bytes,
                        filesize_mb=filesize_mb,
                        title=cdn_info.get("title", "YouTube Audio"),
                        uploader=cdn_info.get("uploader", "YouTube Creator"),
                        duration=cdn_info.get("duration", 0),
                        thumbnail_path=thumb_path,
                        is_over_50mb=(total_bytes > TELEGRAM_FILE_LIMIT_BYTES),
                        is_audio=True,
                        direct_url=cdn_url,
                    )
        except Exception as e:
            logger.debug(f"Direct CDN audio attempt for YouTube failed ({e}), falling back to yt-dlp...")

        configured_proxy = get_youtube_proxy()

        # Step 2: yt-dlp download attempt (using configured proxy/WARP if present, else direct)
        first_proxy = configured_proxy if configured_proxy else None
        first_opts = self._get_youtube_ydl_opts(is_audio=True, download_path_template=output_base, proxy=first_proxy)
        res = await loop.run_in_executor(None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, first_opts)
        if res and res.success:
            return res

        err_msg = str(getattr(res, "error", "")).lower()

        # Step 3: yt-dlp Fallback attempt if first attempt failed
        fallback_proxy = None if first_proxy else configured_proxy
        if fallback_proxy is not None or (first_proxy and not res.success):
            logger.info(f"Primary YouTube audio download attempt failed ({err_msg}). Trying fallback...")
            fallback_opts = self._get_youtube_ydl_opts(is_audio=True, download_path_template=output_base, proxy=fallback_proxy)
            fallback_res = await loop.run_in_executor(None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, fallback_opts)
            if fallback_res and fallback_res.success:
                return fallback_res
            err_msg = str(getattr(fallback_res, "error", "")).lower()

        # Step 4: Fast-fail without freezing if bot protection requires cookies/login
        if any(k in err_msg for k in ["sign in to confirm you're not a bot", "sign in", "login_required", "bot"]):
            return DownloadResult(
                success=False,
                error="YOUTUBE_BOT_PROTECTION"
            )

        return res or DownloadResult(success=False, error="YouTube audio download failed.")

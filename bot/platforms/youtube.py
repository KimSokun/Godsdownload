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
    """Returns user-configured YouTube proxy or global proxy from config."""
    return getattr(config, "youtube_proxy", "").strip() or getattr(config, "proxy_url", "").strip() or None


class YouTubePlatform(BasePlatform):
    platform_id = "youtube"
    display_name = "📺 YouTube"
    patterns = [
        re.compile(r'(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)'),
    ]

    def _get_youtube_ydl_opts(self, is_audio: bool, download_path_template: str, proxy: Optional[str] = None) -> Dict[str, Any]:
        """
        Highly optimized yt-dlp options for YouTube and YouTube Shorts.
        Uses android/ios/mweb/web player clients and skips redundant webpage/config loads.
        """
        opts: Dict[str, Any] = {
            "noplaylist": True,
            "concurrent_fragment_downloads": 4,
            "buffersize": 1048576,        # 1MB buffer
            "http_chunk_size": 10485760,  # 10MB chunk
            "socket_timeout": 15,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "ios", "mweb", "web"],
                    "player_skip": ["configs", "webpage"],
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

        if proxy:
            opts["proxy"] = proxy

        return opts

    async def download_video(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = clean_youtube_url(url)
        file_id = uuid.uuid4().hex
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()

        configured_proxy = get_youtube_proxy()

        # Step 1: Ultra-fast Direct Gigabit Download Attempt (< 2.5s)
        direct_opts = self._get_youtube_ydl_opts(is_audio=False, download_path_template=output_base, proxy=None)
        res = await loop.run_in_executor(None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb, direct_opts)
        if res and res.success:
            return res

        err_msg = str(getattr(res, "error", "")).lower()
        is_bot_blocked = any(k in err_msg for k in ["bot", "sign in", "login", "403", "forbidden"])

        # Step 2: If bot protection triggered and a proxy is explicitly configured in .env, retry via proxy
        if is_bot_blocked and configured_proxy:
            logger.info(f"Direct download bot-blocked ({err_msg}). Retrying with configured proxy: {configured_proxy}")
            proxy_opts = self._get_youtube_ydl_opts(is_audio=False, download_path_template=output_base, proxy=configured_proxy)
            proxy_res = await loop.run_in_executor(None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb, proxy_opts)
            if proxy_res and proxy_res.success:
                return proxy_res
            err_msg = str(getattr(proxy_res, "error", "")).lower()

        # Step 3: Fast-fail without freezing if bot protection requires cookies/login
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

        configured_proxy = get_youtube_proxy()

        # Step 1: Direct Fast Attempt (< 2.5s)
        direct_opts = self._get_youtube_ydl_opts(is_audio=True, download_path_template=output_base, proxy=None)
        res = await loop.run_in_executor(None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, direct_opts)
        if res and res.success:
            return res

        err_msg = str(getattr(res, "error", "")).lower()
        is_bot_blocked = any(k in err_msg for k in ["bot", "sign in", "login", "403", "forbidden"])

        # Step 2: Retry with configured proxy if present
        if is_bot_blocked and configured_proxy:
            logger.info(f"Direct audio download bot-blocked ({err_msg}). Retrying with configured proxy: {configured_proxy}")
            proxy_opts = self._get_youtube_ydl_opts(is_audio=True, download_path_template=output_base, proxy=configured_proxy)
            proxy_res = await loop.run_in_executor(None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, proxy_opts)
            if proxy_res and proxy_res.success:
                return proxy_res
            err_msg = str(getattr(proxy_res, "error", "")).lower()

        # Step 3: Fast-fail without freezing if bot protection requires cookies/login
        if any(k in err_msg for k in ["sign in to confirm you're not a bot", "sign in", "login_required", "bot"]):
            return DownloadResult(
                success=False,
                error="YOUTUBE_BOT_PROTECTION"
            )

        return res or DownloadResult(success=False, error="YouTube audio download failed.")

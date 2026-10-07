import os
import re
import uuid
import asyncio
import logging
from typing import Optional, Dict, Any, List

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


class FacebookPlatform(BasePlatform):
    platform_id = "facebook"
    display_name = "📘 Facebook"
    patterns = [
        re.compile(r'(?:https?://)?(?:www\.|m\.|web\.)?(?:facebook\.com|fb\.watch|fb\.gg|fb\.me)'),
    ]

    def _fb_custom_opts(self) -> Dict[str, Any]:
        return {
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Fetch-Mode": "navigate",
            }
        }

    async def fast_scan(self, url: str) -> Optional[Dict[str, Any]]:
        """Fast scan Facebook links for photo posts vs videos."""
        clean_url = await expand_redirect_url(url)
        try:
            cdn_info = await fetch_cobalt_mirror(clean_url, is_audio=False, timeout=3.0)
            if cdn_info:
                images = cdn_info.get("image_urls") or []
                if images:
                    return {
                        "type": "images",
                        "platform": self.display_name,
                        "platform_id": self.platform_id,
                        "image_urls": images,
                        "title": cdn_info.get("title", "Facebook Photos"),
                        "uploader": cdn_info.get("uploader", "Facebook Creator"),
                        "audio_url": None,
                    }
                if cdn_info.get("video_url"):
                    return {
                        "type": "video",
                        "platform": self.display_name,
                        "platform_id": self.platform_id,
                        "image_urls": [],
                        "title": cdn_info.get("title", "Facebook Video"),
                        "uploader": cdn_info.get("uploader", "Facebook Creator"),
                        "audio_url": cdn_info.get("audio_url"),
                    }
        except Exception:
            pass
        return None

    async def download_images(self, url: str) -> Any:
        from bot.downloader import DownloadResult
        scan = await self.fast_scan(url)
        if scan and scan.get("image_urls"):
            return DownloadResult(
                success=True,
                is_image=True,
                image_urls=scan["image_urls"],
                title=scan.get("title", "Facebook Photos"),
                uploader=scan.get("uploader", "Facebook Creator"),
                direct_url=scan["image_urls"][0] if scan["image_urls"] else None,
            )
        return await super().download_images(url)

    async def download_video(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = await expand_redirect_url(url)
        file_id = uuid.uuid4().hex
        output_video = os.path.join(config.temp_dir, f"{file_id}.mp4")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        # Step 1: Ultra-fast Cobalt CDN mirror query
        try:
            cdn_info = await fetch_cobalt_mirror(clean_url, is_audio=False, timeout=3.5)
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
                        title=cdn_info.get("title", "Facebook Video"),
                        uploader=cdn_info.get("uploader", "Facebook Creator"),
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
                        title=cdn_info.get("title", "Facebook Video"),
                        uploader=cdn_info.get("uploader", "Facebook Creator"),
                        filesize_mb=filesize_mb,
                        duration=cdn_info.get("duration", 0),
                        thumbnail_path=cdn_info.get("thumbnail_url"),
                        is_audio=False,
                    )
        except Exception as e:
            logger.debug(f"Facebook Cobalt mirror failed: {e}")

        # Step 2: yt-dlp fallback
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb, self._fb_custom_opts()
        )

    async def download_audio(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = await expand_redirect_url(url)
        file_id = uuid.uuid4().hex
        raw_audio = os.path.join(config.temp_dir, f"{file_id}_raw.audio")
        final_mp3 = os.path.join(config.temp_dir, f"{file_id}.mp3")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        # Step 1: Ultra-fast Cobalt audio mirror query
        try:
            cdn_info = await fetch_cobalt_mirror(clean_url, is_audio=True, timeout=3.5)
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
                        title=cdn_info.get("title", "Facebook Audio"),
                        uploader=cdn_info.get("uploader", "Facebook Creator"),
                        duration=cdn_info.get("duration", 0),
                        thumbnail_path=thumb_path,
                        is_over_50mb=(filesize > TELEGRAM_FILE_LIMIT_BYTES),
                        is_audio=True,
                        direct_url=cdn_url,
                    )
                elif err and "FILE_TOO_LARGE" in err:
                    return DownloadResult(
                        success=True,
                        is_over_50mb=True,
                        direct_url=cdn_url,
                        title=cdn_info.get("title", "Facebook Audio"),
                        uploader=cdn_info.get("uploader", "Facebook Creator"),
                        filesize_mb=filesize_mb,
                        duration=cdn_info.get("duration", 0),
                        thumbnail_path=cdn_info.get("thumbnail_url"),
                        is_audio=True,
                    )
        except Exception as e:
            logger.debug(f"Facebook Cobalt audio failed: {e}")

        # Step 2: yt-dlp fallback
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, self._fb_custom_opts()
        )

import os
import uuid
import asyncio
import logging
from typing import Optional, Dict, Any

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


class GenericPlatform(BasePlatform):
    platform_id = "generic"
    display_name = "🌐 Universal Web (1000+ Sites)"
    patterns = []

    def can_handle(self, url: str) -> bool:
        # Generic platform is the catch-all fallback
        return True

    async def fast_scan(self, url: str) -> Optional[Dict[str, Any]]:
        clean_url = await expand_redirect_url(url)
        lower_url = clean_url.split("?")[0].lower()
        if any(lower_url.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"]):
            filename_title = os.path.splitext(os.path.basename(clean_url.split("?")[0]))[0]
            return {
                "type": "images",
                "platform": self.display_name,
                "platform_id": self.platform_id,
                "image_urls": [clean_url],
                "title": filename_title or "Direct Web Image",
                "uploader": "Web",
                "audio_url": None,
            }
        try:
            cdn_info = await fetch_cobalt_mirror(clean_url, is_audio=False, timeout=2.0)
            if cdn_info and cdn_info.get("image_urls"):
                return {
                    "type": "images",
                    "platform": self.display_name,
                    "platform_id": self.platform_id,
                    "image_urls": cdn_info["image_urls"],
                    "title": cdn_info.get("title", "Web Images"),
                    "uploader": cdn_info.get("uploader", "Web"),
                    "audio_url": None,
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
                title=scan.get("title", "Web Image"),
                uploader=scan.get("uploader", "Web"),
                direct_url=scan["image_urls"][0],
            )
        return await super().download_images(url)

    async def download_video(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = await expand_redirect_url(url)
        file_id = uuid.uuid4().hex
        output_video = os.path.join(config.temp_dir, f"{file_id}.mp4")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        # Step 1: Direct media file stream detection (.mp4, .mov, .webm, etc.)
        lower_url = clean_url.split("?")[0].lower()
        if any(lower_url.endswith(ext) for ext in [".mp4", ".mov", ".webm", ".mkv", ".m4v", ".ts", ".flv"]):
            ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                clean_url, output_video, max_size_mb, max_retries=2
            )
            if ok:
                filename_title = os.path.splitext(os.path.basename(clean_url.split("?")[0]))[0]
                return DownloadResult(
                    success=True,
                    filepath=output_video,
                    filename=f"{file_id}.mp4",
                    filesize_bytes=total_bytes,
                    filesize_mb=filesize_mb,
                    title=filename_title or "Direct Web Video",
                    uploader="Web",
                    duration=0,
                    thumbnail_path=None,
                    is_over_50mb=(total_bytes > TELEGRAM_FILE_LIMIT_BYTES),
                    is_audio=False,
                    direct_url=clean_url,
                )

        # Step 2: Optional Cobalt mirror probe
        try:
            cdn_info = await fetch_cobalt_mirror(clean_url, is_audio=False, timeout=2.5)
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
                        title=cdn_info.get("title", "Universal Video"),
                        uploader=cdn_info.get("uploader", "Web"),
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
                        title=cdn_info.get("title", "Universal Video"),
                        uploader=cdn_info.get("uploader", "Web"),
                        filesize_mb=filesize_mb,
                        duration=cdn_info.get("duration", 0),
                        thumbnail_path=cdn_info.get("thumbnail_url"),
                        is_audio=False,
                    )
        except Exception:
            pass

        # Step 2: yt-dlp fallback
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb)

    async def download_audio(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = await expand_redirect_url(url)
        file_id = uuid.uuid4().hex
        raw_audio = os.path.join(config.temp_dir, f"{file_id}_raw.audio")
        final_mp3 = os.path.join(config.temp_dir, f"{file_id}.mp3")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        # Step 1: Optional Cobalt audio mirror probe
        try:
            cdn_info = await fetch_cobalt_mirror(clean_url, is_audio=True, timeout=2.5)
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
                        title=cdn_info.get("title", "Universal Audio"),
                        uploader=cdn_info.get("uploader", "Web"),
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
                        title=cdn_info.get("title", "Universal Audio"),
                        uploader=cdn_info.get("uploader", "Web"),
                        filesize_mb=filesize_mb,
                        duration=cdn_info.get("duration", 0),
                        thumbnail_path=cdn_info.get("thumbnail_url"),
                        is_audio=True,
                    )
        except Exception:
            pass

        # Step 2: yt-dlp fallback
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb)

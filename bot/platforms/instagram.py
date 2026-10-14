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
    ensure_ios_compatible_video_sync,
    probe_video_metadata_sync,
    TELEGRAM_FILE_LIMIT_BYTES,
)
from bot.cdn_api import download_direct_cdn_stream, expand_redirect_url, fetch_cobalt_mirror, fetch_instagram_story_api

logger = logging.getLogger(__name__)


class InstagramPlatform(BasePlatform):
    platform_id = "instagram"
    display_name = "📸 Instagram"
    patterns = [
        re.compile(r'(?:https?://)?(?:www\.)?(?:instagram\.com|instagr\.am)'),
    ]

    def _ig_custom_opts(self, is_story: bool = False) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
                "Accept-Language": "en-US,en;q=0.9",
            }
        }
        if is_story:
            opts["extractor_args"] = {"instagram:story": {"api": ["web"]}}
        if getattr(config, "instagram_cookie", None):
            opts["http_headers"]["Cookie"] = config.instagram_cookie
        return opts

    async def fast_scan(self, url: str) -> Optional[Dict[str, Any]]:
        """Fast scan Instagram link (Reel vs single photo vs carousel vs Story)."""
        clean_url = await expand_redirect_url(url)
        is_story = bool(re.search(r'instagram\.com/stories/([^/?#]+)', clean_url, re.I))

        # Check story API first if it is an Instagram story link
        if is_story:
            try:
                story_info = await fetch_instagram_story_api(clean_url, timeout=3.5)
                if story_info:
                    images = story_info.get("image_urls") or []
                    if images:
                        return {
                            "type": "images",
                            "platform": self.display_name,
                            "platform_id": self.platform_id,
                            "image_urls": images,
                            "title": story_info.get("title", "Instagram Story"),
                            "uploader": story_info.get("uploader", "Instagram Creator"),
                            "audio_url": None,
                        }
                    if story_info.get("video_url"):
                        return {
                            "type": "video",
                            "platform": self.display_name,
                            "platform_id": self.platform_id,
                            "image_urls": [],
                            "title": story_info.get("title", "Instagram Story"),
                            "uploader": story_info.get("uploader", "Instagram Creator"),
                            "audio_url": None,
                        }
            except Exception:
                pass

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
                        "title": cdn_info.get("title", "Instagram Photos"),
                        "uploader": cdn_info.get("uploader", "Instagram Creator"),
                        "audio_url": cdn_info.get("audio_url"),
                    }
                if cdn_info.get("video_url"):
                    return {
                        "type": "video",
                        "platform": self.display_name,
                        "platform_id": self.platform_id,
                        "image_urls": [],
                        "title": cdn_info.get("title", "Instagram Video"),
                        "uploader": cdn_info.get("uploader", "Instagram Creator"),
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
                title=scan.get("title", "Instagram Photos"),
                uploader=scan.get("uploader", "Instagram Creator"),
                direct_url=scan["image_urls"][0] if scan["image_urls"] else None,
            )
        return await super().download_images(url)

    async def download_video(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = await expand_redirect_url(url)
        file_id = uuid.uuid4().hex
        output_video = os.path.join(config.temp_dir, f"{file_id}.mp4")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")
        is_story = bool(re.search(r'instagram\.com/stories/([^/?#]+)', clean_url, re.I))

        # Step 1: Check dedicated Story API or Cobalt CDN mirror resolution
        try:
            cdn_info = None
            if is_story:
                cdn_info = await fetch_instagram_story_api(clean_url, timeout=4.0)
            if not cdn_info:
                cdn_info = await fetch_cobalt_mirror(clean_url, is_audio=False, timeout=3.5)

            if cdn_info:
                # If story or post resolved to images, return them directly
                if cdn_info.get("image_urls") and not cdn_info.get("video_url"):
                    return DownloadResult(
                        success=True,
                        is_image=True,
                        image_urls=cdn_info["image_urls"],
                        title=cdn_info.get("title", "Instagram Story" if is_story else "Instagram Photos"),
                        uploader=cdn_info.get("uploader", "Instagram Creator"),
                        direct_url=cdn_info["image_urls"][0],
                    )

                if cdn_info.get("video_url"):
                    cdn_url = cdn_info["video_url"]
                    ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                        cdn_url, output_video, max_size_mb, max_retries=2
                    )
                    if ok:
                        # Ensure iOS Photos & faststart compatibility
                        output_video = ensure_ios_compatible_video_sync(output_video)
                        total_bytes = os.path.getsize(output_video)
                        filesize_mb = round(total_bytes / (1024 * 1024), 2)

                        # Extract exact original video frame width, height, duration & native thumbnail
                        width, height, duration, vid_thumb = probe_video_metadata_sync(output_video, generate_thumb=True)
                        thumb_path = vid_thumb
                        if not thumb_path and cdn_info.get("thumbnail_url"):
                            thumb_path = await download_thumbnail(cdn_info.get("thumbnail_url"), output_thumb)

                        return DownloadResult(
                            success=True,
                            filepath=output_video,
                            filename=f"{file_id}.mp4",
                            filesize_bytes=total_bytes,
                            filesize_mb=filesize_mb,
                            title=cdn_info.get("title", "Instagram Story" if is_story else "Instagram Reel"),
                            uploader=cdn_info.get("uploader", "Instagram Creator"),
                            duration=duration or cdn_info.get("duration", 0),
                            thumbnail_path=thumb_path,
                            is_over_50mb=(total_bytes > TELEGRAM_FILE_LIMIT_BYTES),
                            is_audio=False,
                            direct_url=cdn_url,
                            width=width or None,
                            height=height or None,
                        )
                    elif err and "FILE_TOO_LARGE" in err:
                        return DownloadResult(
                            success=True,
                            is_over_50mb=True,
                            direct_url=cdn_url,
                            title=cdn_info.get("title", "Instagram Story" if is_story else "Instagram Reel"),
                            uploader=cdn_info.get("uploader", "Instagram Creator"),
                            filesize_mb=filesize_mb,
                            duration=cdn_info.get("duration", 0),
                            thumbnail_path=cdn_info.get("thumbnail_url"),
                            is_audio=False,
                        )
        except Exception as e:
            logger.debug(f"Instagram CDN / Story query failed: {e}")

        # Step 2: yt-dlp fallback (with instagram:story arguments if story)
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(
            None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb, self._ig_custom_opts(is_story=is_story)
        )
        if not res.success and is_story:
            logger.info("Instagram Story download could not be completed via anonymous access.")
            res.error = "Instagram Story is private, expired (24h limit), or requires login cookies/API key."
        return res

    async def download_audio(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = await expand_redirect_url(url)
        file_id = uuid.uuid4().hex
        raw_audio = os.path.join(config.temp_dir, f"{file_id}_raw.audio")
        final_mp3 = os.path.join(config.temp_dir, f"{file_id}.mp3")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        # Step 1: Ultra-fast Cobalt CDN audio mirror resolution
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
                        title=cdn_info.get("title", "Instagram Audio"),
                        uploader=cdn_info.get("uploader", "Instagram Creator"),
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
                        title=cdn_info.get("title", "Instagram Audio"),
                        uploader=cdn_info.get("uploader", "Instagram Creator"),
                        filesize_mb=filesize_mb,
                        duration=cdn_info.get("duration", 0),
                        thumbnail_path=cdn_info.get("thumbnail_url"),
                        is_audio=True,
                    )
        except Exception as e:
            logger.debug(f"Instagram Cobalt audio query failed: {e}")

        # Step 2: yt-dlp fallback
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, self._ig_custom_opts()
        )

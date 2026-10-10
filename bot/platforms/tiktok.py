import os
import re
import uuid
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


class TikTokPlatform(BasePlatform):
    platform_id = "tiktok"
    display_name = "🎵 TikTok"
    patterns = [
        re.compile(r'(?:https?://)?(?:[a-zA-Z0-9_\.]+\.)?tiktok\.com'),
    ]

    async def _fetch_tikwm(self, url: str) -> Optional[Dict[str, Any]]:
        """TikWM HD No-Watermark CDN Mirror."""
        api_url = "https://www.tikwm.com/api/"
        data = {"url": url, "count": 12, "cursor": 0, "web": 1, "hd": 1}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=3.5)) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        if res.get("code") == 0 and "data" in res:
                            item = res["data"]
                            video_cdn = item.get("hdplay") or item.get("play")
                            music_cdn = item.get("music")
                            title = item.get("title") or "TikTok Video"
                            author = item.get("author", {}).get("nickname") or "TikTok Creator"
                            duration = item.get("duration") or 0
                            thumbnail = item.get("cover")

                            images = item.get("images") or []
                            return {
                                "success": True,
                                "source": "TikWM HD Mirror",
                                "title": title,
                                "uploader": author,
                                "duration": duration,
                                "video_url": video_cdn,
                                "audio_url": music_cdn,
                                "thumbnail_url": thumbnail,
                                "image_urls": images,
                            }
        except Exception:
            pass
        return None

    async def fast_scan(self, url: str) -> Optional[Dict[str, Any]]:
        """Fast scan TikTok/Douyin links to detect 10-image slideshows vs videos."""
        clean_url = await expand_redirect_url(url)
        try:
            cdn = await self._fetch_tikwm(clean_url)
            if cdn:
                images = cdn.get("image_urls") or []
                if images:
                    return {
                        "type": "images",
                        "platform": self.display_name,
                        "platform_id": self.platform_id,
                        "image_urls": images,
                        "title": cdn.get("title", "TikTok Photos"),
                        "uploader": cdn.get("uploader", "TikTok Creator"),
                        "audio_url": cdn.get("audio_url"),
                    }
                if cdn.get("video_url"):
                    return {
                        "type": "video",
                        "platform": self.display_name,
                        "platform_id": self.platform_id,
                        "image_urls": [],
                        "title": cdn.get("title", "TikTok Video"),
                        "uploader": cdn.get("uploader", "TikTok Creator"),
                        "audio_url": cdn.get("audio_url"),
                    }
        except Exception:
            pass

        try:
            cobalt = await fetch_cobalt_mirror(clean_url, is_audio=False, timeout=2.5)
            if cobalt:
                imgs = cobalt.get("image_urls") or []
                if imgs:
                    return {
                        "type": "images",
                        "platform": self.display_name,
                        "platform_id": self.platform_id,
                        "image_urls": imgs,
                        "title": cobalt.get("title", "TikTok Photos"),
                        "uploader": cobalt.get("uploader", "TikTok Creator"),
                        "audio_url": cobalt.get("audio_url"),
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
                title=scan.get("title", "TikTok Photos"),
                uploader=scan.get("uploader", "TikTok Creator"),
                direct_url=scan["image_urls"][0] if scan["image_urls"] else None,
            )
        return await super().download_images(url)

    async def _fetch_tikmate(self, url: str) -> Optional[Dict[str, Any]]:
        """TikMate HD Mirror."""
        api_url = "https://api.tikmate.app/api/lookup"
        data = {"url": url}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://tikmate.app/"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=3.5)) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        video_id = res.get("id")
                        token = res.get("token")
                        if video_id and token:
                            video_url = f"https://tikmate.app/download/{token}/{video_id}.mp4?hd=1"
                            title = res.get("author_name", "TikTok Video")
                            author = res.get("author_id", "Creator")
                            thumbnail = res.get("author_avatar")

                            return {
                                "success": True,
                                "source": "TikMate Mirror",
                                "title": f"TikTok by {title}",
                                "uploader": author,
                                "duration": 0,
                                "video_url": video_url,
                                "audio_url": None,
                                "thumbnail_url": thumbnail,
                            }
        except Exception:
            pass
        return None

    async def download_video(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = await expand_redirect_url(url)
        file_id = uuid.uuid4().hex
        output_video = os.path.join(config.temp_dir, f"{file_id}.mp4")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        # Step 1: Try TikWM then TikMate
        for mirror_fn in [self._fetch_tikwm, self._fetch_tikmate]:
            try:
                cdn_info = await mirror_fn(clean_url)
                if cdn_info and cdn_info.get("video_url"):
                    cdn_url = cdn_info["video_url"]
                    mirror_headers = {"Referer": "https://tikmate.app/"} if "tikmate" in cdn_url else {"Referer": "https://www.tikwm.com/"}
                    ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                        cdn_url, output_video, max_size_mb, max_retries=2, headers=mirror_headers
                    )
                    if ok:
                        thumb_path = await download_thumbnail(cdn_info.get("thumbnail_url"), output_thumb)
                        return DownloadResult(
                            success=True,
                            filepath=output_video,
                            filename=f"{file_id}.mp4",
                            filesize_bytes=total_bytes,
                            filesize_mb=filesize_mb,
                            title=cdn_info.get("title", "TikTok Video"),
                            uploader=cdn_info.get("uploader", "TikTok Creator"),
                            duration=cdn_info.get("duration", 0),
                            thumbnail_path=thumb_path,
                            is_over_50mb=(total_bytes > TELEGRAM_FILE_LIMIT_BYTES),
                            is_audio=False,
                            direct_url=cdn_url,
                            headers=mirror_headers,
                        )
                    elif err and "FILE_TOO_LARGE" in err:
                        parts = err.split(":")
                        est = float(parts[1]) if len(parts) > 1 else 100.0
                        return DownloadResult(
                            success=True,
                            filesize_mb=est,
                            title=cdn_info.get("title", "TikTok Video"),
                            uploader=cdn_info.get("uploader", "TikTok Creator"),
                            duration=cdn_info.get("duration", 0),
                            is_over_50mb=True,
                            is_audio=False,
                            direct_url=cdn_url,
                            headers=mirror_headers,
                        )
            except Exception as e:
                logger.warning(f"TikTok mirror query failed: {e}")

        # Step 2: Try Cobalt CDN mirror
        try:
            cobalt_info = await fetch_cobalt_mirror(clean_url, is_audio=False, timeout=3.0)
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
                        title=cobalt_info.get("title", "TikTok Video"),
                        uploader=cobalt_info.get("uploader", "TikTok Creator"),
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
                        title=cobalt_info.get("title", "TikTok Video"),
                        uploader=cobalt_info.get("uploader", "TikTok Creator"),
                        duration=cobalt_info.get("duration", 0),
                        is_over_50mb=True,
                        is_audio=False,
                        direct_url=cdn_url,
                    )
        except Exception as e:
            logger.debug(f"TikTok Cobalt query failed: {e}")

        # Step 3: yt-dlp fallback
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

        # Step 1: Try TikWM audio
        try:
            cdn_info = await self._fetch_tikwm(clean_url)
            if cdn_info and cdn_info.get("audio_url"):
                cdn_url = cdn_info["audio_url"]
                ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                    cdn_url, raw_audio, max_size_mb, max_retries=2, headers={"Referer": "https://www.tikwm.com/"}
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
                        title=cdn_info.get("title", "TikTok Audio"),
                        uploader=cdn_info.get("uploader", "TikTok Creator"),
                        duration=cdn_info.get("duration", 0),
                        thumbnail_path=thumb_path,
                        is_over_50mb=(filesize > TELEGRAM_FILE_LIMIT_BYTES),
                        is_audio=True,
                        direct_url=cdn_url,
                        headers={"Referer": "https://www.tikwm.com/"},
                    )
                elif err and "FILE_TOO_LARGE" in err:
                    parts = err.split(":")
                    est = float(parts[1]) if len(parts) > 1 else 100.0
                    return DownloadResult(
                        success=True,
                        filesize_mb=est,
                        title=cdn_info.get("title", "TikTok Audio"),
                        uploader=cdn_info.get("uploader", "TikTok Creator"),
                        duration=cdn_info.get("duration", 0),
                        is_over_50mb=True,
                        is_audio=True,
                        direct_url=cdn_url,
                        headers={"Referer": "https://www.tikwm.com/"},
                    )
        except Exception as e:
            logger.warning(f"TikTok audio mirror failed: {e}")

        # Step 2: Try Cobalt audio mirror
        try:
            cobalt_info = await fetch_cobalt_mirror(clean_url, is_audio=True, timeout=3.0)
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
                        title=cobalt_info.get("title", "TikTok Audio"),
                        uploader=cobalt_info.get("uploader", "TikTok Creator"),
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
                        title=cobalt_info.get("title", "TikTok Audio"),
                        uploader=cobalt_info.get("uploader", "TikTok Creator"),
                        duration=cobalt_info.get("duration", 0),
                        is_over_50mb=True,
                        is_audio=True,
                        direct_url=cdn_url,
                    )
        except Exception as e:
            logger.debug(f"TikTok Cobalt audio failed: {e}")

        # Step 3: yt-dlp fallback
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb)

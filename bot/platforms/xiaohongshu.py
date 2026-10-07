import os
import re
import uuid
import asyncio
import logging
from typing import Optional, Dict, Any, List

from bot.config import config
from bot.platforms.base import BasePlatform
from bot.cdn_api import expand_redirect_url

logger = logging.getLogger(__name__)


class XiaohongshuPlatform(BasePlatform):
    platform_id = "xiaohongshu"
    display_name = "📕 Xiaohongshu (小红书)"
    patterns = [
        re.compile(r'(?:https?://)?(?:[a-zA-Z0-9_\.]+\.)?(?:xiaohongshu\.com|xhslink\.com)'),
    ]

    def _xhs_custom_opts(self) -> Dict[str, Any]:
        return {
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.38",
                "Referer": "https://www.xiaohongshu.com/",
            }
        }

    async def fast_scan(self, url: str) -> Optional[Dict[str, Any]]:
        """Fast scan Xiaohongshu notes for image carousels vs video notes."""
        clean_url = await expand_redirect_url(url)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, self.execute_ytdlp_images, clean_url)
        if res and res.success and res.image_urls:
            return {
                "type": "images",
                "platform": self.display_name,
                "platform_id": self.platform_id,
                "image_urls": res.image_urls,
                "title": res.title or "Xiaohongshu Note",
                "uploader": res.uploader or "RED Creator",
                "audio_url": None,
            }
        return None

    async def download_images(self, url: str) -> Any:
        from bot.downloader import DownloadResult
        scan = await self.fast_scan(url)
        if scan and scan.get("image_urls"):
            return DownloadResult(
                success=True,
                is_image=True,
                image_urls=scan["image_urls"],
                title=scan.get("title", "Xiaohongshu Note"),
                uploader=scan.get("uploader", "RED Creator"),
                direct_url=scan["image_urls"][0] if scan["image_urls"] else None,
            )
        return await super().download_images(url)

    async def download_video(self, url: str, max_size_mb: float = 2000.0) -> Any:
        clean_url = await expand_redirect_url(url)
        file_id = uuid.uuid4().hex
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(
            None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb, self._xhs_custom_opts()
        )
        if not res or not res.success:
            err = getattr(res, "error", "") or ""
            if "initial state" in err or "页面不见了" in err or "not found" in err.lower():
                from bot.downloader import DownloadResult
                return DownloadResult(
                    success=False,
                    error="NOTE_NOT_FOUND: This note or video has been deleted or is private on Xiaohongshu."
                )
        return res

    async def download_audio(self, url: str, max_size_mb: float = 2000.0) -> Any:
        clean_url = await expand_redirect_url(url)
        file_id = uuid.uuid4().hex
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(
            None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, self._xhs_custom_opts()
        )
        if not res or not res.success:
            err = getattr(res, "error", "") or ""
            if "initial state" in err or "页面不见了" in err or "not found" in err.lower():
                from bot.downloader import DownloadResult
                return DownloadResult(
                    success=False,
                    error="NOTE_NOT_FOUND: This note or audio has been deleted or is private on Xiaohongshu."
                )
        return res

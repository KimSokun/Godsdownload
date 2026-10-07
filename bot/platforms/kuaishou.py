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


class KuaishouPlatform(BasePlatform):
    platform_id = "kuaishou"
    display_name = "👻 Kuaishou (快手) / Kwai"
    patterns = [
        re.compile(r'(?:https?://)?(?:[a-zA-Z0-9_\.]+\.)?(?:kuaishou\.com|kwai\.com|gifshow\.com|chenzhongtech\.com)'),
    ]

    def _kuaishou_custom_opts(self) -> Dict[str, Any]:
        return {
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
        }

    async def download_video(self, url: str, max_size_mb: float = 2000.0) -> Any:
        clean_url = await expand_redirect_url(url)
        file_id = uuid.uuid4().hex
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb, self._kuaishou_custom_opts()
        )

    async def download_audio(self, url: str, max_size_mb: float = 2000.0) -> Any:
        clean_url = await expand_redirect_url(url)
        file_id = uuid.uuid4().hex
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, self._kuaishou_custom_opts()
        )

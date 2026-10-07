import os
import re
import uuid
import asyncio
import logging
from typing import Optional, Dict, Any, List
import aiohttp

from bot.config import config
from bot.platforms.base import BasePlatform, cleanup_files, convert_to_mp3
from bot.cdn_api import expand_redirect_url, download_direct_cdn_stream

logger = logging.getLogger(__name__)

BILIBILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
}


def clean_bilibili_url(url: str) -> str:
    """Normalizes Bilibili URL and strips tracking query parameters (e.g. spm_id_from, vd_source, share_source)."""
    if not url:
        return url
    match = re.search(r'(BV[a-zA-Z0-9]+|av\d+)', url)
    if match:
        return f"https://www.bilibili.com/video/{match.group(1)}"
    return url.split("?")[0]


class BilibiliPlatform(BasePlatform):
    platform_id = "bilibili"
    display_name = "📺 Bilibili"
    patterns = [
        re.compile(r'(?:https?://)?(?:www\.|m\.)?(?:bilibili\.com|b23\.tv)'),
    ]

    def _bilibili_custom_opts(self) -> Dict[str, Any]:
        return {
            "concurrent_fragment_downloads": 4,
            "http_headers": {
                "User-Agent": BILIBILI_HEADERS["User-Agent"],
                "Referer": BILIBILI_HEADERS["Referer"],
            },
        }

    async def _fetch_html5_stream(self, bvid: str) -> Optional[Dict[str, Any]]:
        """Queries Bilibili official HTML5 playurl API for direct progressive MP4 stream."""
        try:
            async with aiohttp.ClientSession(headers=BILIBILI_HEADERS) as session:
                # 1. Fetch pagelist to get primary cid and title
                page_url = f"https://api.bilibili.com/x/player/pagelist?bvid={bvid}"
                async with session.get(page_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return None
                    pdata = await resp.json()
                    if pdata.get("code") != 0 or not pdata.get("data"):
                        return None
                    cid = pdata["data"][0].get("cid")
                    title = pdata["data"][0].get("part") or "Bilibili Video"
                    duration = int(pdata["data"][0].get("duration") or 0)

                if not cid:
                    return None

                # 2. Fetch playurl with platform=html5 & fnval=0 for pre-muxed MP4 stream
                play_url = f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn=80&fnval=0&fnver=0&fourk=1&platform=html5"
                async with session.get(play_url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                    if resp.status != 200:
                        return None
                    pld = await resp.json()
                    durls = pld.get("data", {}).get("durl", [])
                    if not durls:
                        return None
                    primary = durls[0]
                    cdn_url = primary.get("url")
                    size_bytes = int(primary.get("size") or 0)
                    size_mb = round(size_bytes / (1024 * 1024), 2)

                    return {
                        "cdn_url": cdn_url,
                        "title": title,
                        "duration": duration,
                        "size_bytes": size_bytes,
                        "size_mb": size_mb,
                    }
        except Exception as e:
            logger.debug(f"Bilibili HTML5 API lookup failed: {e}")
            return None

    async def download_video(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        expanded = await expand_redirect_url(url)
        clean_url = clean_bilibili_url(expanded)
        file_id = uuid.uuid4().hex
        output_base = os.path.join(config.temp_dir, file_id)
        video_dest = f"{output_base}.mp4"

        # 1. Fast path: Bilibili HTML5 Direct CDN Stream
        bv_match = re.search(r'(BV[a-zA-Z0-9]+)', clean_url)
        if bv_match:
            bvid = bv_match.group(1)
            meta = await self._fetch_html5_stream(bvid)
            if meta and meta.get("cdn_url"):
                cdn_url = meta["cdn_url"]
                size_mb = meta["size_mb"]
                size_bytes = meta["size_bytes"]
                title = meta["title"]
                duration = meta["duration"]

                # If size exceeds Telegram 50MB limit: return direct stream immediately!
                if size_mb >= config.telegram_direct_limit_mb:
                    logger.info(f"Bilibili video {bvid} is {size_mb} MB (>= 50MB). Returning instant direct CDN URL.")
                    return DownloadResult(
                        success=True,
                        filepath=None,
                        filename=f"{bvid}.mp4",
                        filesize_bytes=size_bytes,
                        filesize_mb=size_mb,
                        title=title,
                        uploader="Bilibili",
                        duration=duration,
                        thumbnail_path=None,
                        is_over_50mb=True,
                        is_audio=False,
                        direct_url=cdn_url,
                        headers={"Referer": "https://www.bilibili.com/"},
                    )

                # Size < 50MB: Download progressive MP4 stream directly to disk
                ok, total_bytes, actual_mb, err = await download_direct_cdn_stream(cdn_url, video_dest, headers=BILIBILI_HEADERS)
                if ok and os.path.exists(video_dest):
                    actual_size = os.path.getsize(video_dest)
                    actual_mb = round(actual_size / (1024 * 1024), 2)
                    is_over_50 = actual_size > (50 * 1024 * 1024)
                    return DownloadResult(
                        success=True,
                        filepath=video_dest if not is_over_50 else None,
                        filename=os.path.basename(video_dest),
                        filesize_bytes=actual_size,
                        filesize_mb=actual_mb,
                        title=title,
                        uploader="Bilibili",
                        duration=duration,
                        thumbnail_path=None,
                        is_over_50mb=is_over_50,
                        is_audio=False,
                        direct_url=cdn_url,
                        headers={"Referer": "https://www.bilibili.com/"},
                    )

        # 2. Fallback: yt-dlp with backoff retry and browser headers
        loop = asyncio.get_running_loop()
        backoffs = [2.0, 3.5, 5.0]
        res = None
        for attempt in range(len(backoffs) + 1):
            res = await loop.run_in_executor(
                None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb, self._bilibili_custom_opts()
            )
            if res and res.success:
                return res

            err = getattr(res, "error", "") or ""
            if ("412" in err or "Precondition" in err) and attempt < len(backoffs):
                delay = backoffs[attempt]
                logger.info(f"Bilibili 412 rate limit on {clean_url} (attempt {attempt + 1}/{len(backoffs) + 1}). Retrying in {delay}s...")
                await asyncio.sleep(delay)
            else:
                break
        return res

    async def download_audio(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        expanded = await expand_redirect_url(url)
        clean_url = clean_bilibili_url(expanded)
        file_id = uuid.uuid4().hex
        output_base = os.path.join(config.temp_dir, file_id)
        video_temp = f"{output_base}_temp.mp4"
        audio_dest = f"{output_base}.mp3"

        # 1. Fast path: HTML5 stream -> MP3 conversion
        bv_match = re.search(r'(BV[a-zA-Z0-9]+)', clean_url)
        if bv_match:
            bvid = bv_match.group(1)
            meta = await self._fetch_html5_stream(bvid)
            if meta and meta.get("cdn_url"):
                cdn_url = meta["cdn_url"]
                size_mb = meta["size_mb"]
                title = meta["title"]
                duration = meta["duration"]

                # If progressive MP4 is < 60MB, download and convert audio track to MP3
                if size_mb <= 60.0:
                    ok, total_bytes, actual_mb, err = await download_direct_cdn_stream(cdn_url, video_temp, headers=BILIBILI_HEADERS)
                    if ok and os.path.exists(video_temp):
                        converted = await convert_to_mp3(video_temp, audio_dest)
                        cleanup_files(video_temp)
                        if converted and os.path.exists(audio_dest):
                            audio_size = os.path.getsize(audio_dest)
                            audio_mb = round(audio_size / (1024 * 1024), 2)
                            return DownloadResult(
                                success=True,
                                filepath=audio_dest,
                                filename=os.path.basename(audio_dest),
                                filesize_bytes=audio_size,
                                filesize_mb=audio_mb,
                                title=title,
                                uploader="Bilibili",
                                duration=duration,
                                thumbnail_path=None,
                                is_over_50mb=(audio_size > 50 * 1024 * 1024),
                                is_audio=True,
                                direct_url=cdn_url,
                                headers={"Referer": "https://www.bilibili.com/"},
                            )

        # 2. Fallback: yt-dlp audio
        loop = asyncio.get_running_loop()
        backoffs = [2.0, 3.5, 5.0]
        res = None
        for attempt in range(len(backoffs) + 1):
            res = await loop.run_in_executor(
                None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, self._bilibili_custom_opts()
            )
            if res and res.success:
                return res

            err = getattr(res, "error", "") or ""
            if ("412" in err or "Precondition" in err) and attempt < len(backoffs):
                delay = backoffs[attempt]
                logger.info(f"Bilibili 412 rate limit on {clean_url} (attempt {attempt + 1}/{len(backoffs) + 1}). Retrying in {delay}s...")
                await asyncio.sleep(delay)
            else:
                break
        return res

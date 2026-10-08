import os
import re
import uuid
import html
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

SHORTCODE_RE = re.compile(r'(?:/t/|/post/)([A-Za-z0-9_-]+)')
USERNAME_RE = re.compile(r'threads\.(?:net|com)/@([A-Za-z0-9_.-]+)')


class ThreadsPlatform(BasePlatform):
    platform_id = "threads"
    display_name = "🧵 Threads"
    patterns = [
        re.compile(r'(?:https?://)?(?:www\.)?(?:threads\.net|threads\.com)'),
    ]

    def _threads_custom_opts(self) -> Dict[str, Any]:
        return {
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
        }

    @staticmethod
    def extract_shortcode(url: str) -> Optional[str]:
        m = SHORTCODE_RE.search(url)
        return m.group(1) if m else None

    @staticmethod
    def extract_username(url: str) -> Optional[str]:
        m = USERNAME_RE.search(url)
        return m.group(1) if m else None

    async def resolve_threads_media(self, url: str) -> Dict[str, Any]:
        """
        Multi-tier resilient media extractor for Threads (photos, carousels, videos):
        Tier 1: Threads native embed endpoint (/t/{shortcode}/embed)
        Tier 2: Facebook External Hit Crawler metadata (/t/{shortcode})
        Tier 3: Cobalt CDN Mirror
        """
        clean_url = await expand_redirect_url(url)
        shortcode = self.extract_shortcode(clean_url)
        username = self.extract_username(clean_url) or "Threads Creator"

        result: Dict[str, Any] = {
            "shortcode": shortcode,
            "username": username,
            "title": "Threads Post",
            "uploader": username,
            "video_url": None,
            "image_urls": [],
            "thumbnail_url": None,
            "media_type": None,  # "video" or "images"
        }

        if not shortcode:
            return result

        # Tier 1: Threads Native Embed Endpoint
        try:
            embed_url = f"https://www.threads.net/t/{shortcode}/embed"
            headers_embed = {
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://www.threads.net/",
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(embed_url, headers=headers_embed, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                    if resp.status == 200:
                        raw_text = await resp.text()
                        clean_text = raw_text.replace(r'\/', '/')

                        # 1. Author identity
                        author_match = re.search(r'class="AuthorIdentity"[^>]*><a [^>]*><span>([^<]+)</span>', clean_text)
                        if author_match:
                            result["uploader"] = author_match.group(1).strip()

                        # 2. Post Caption / Title
                        caption_match = re.search(r'class="BodyTextContainer"[^>]*><span>(.*?)</span>', clean_text, re.DOTALL)
                        if caption_match:
                            caption_text = re.sub(r'<[^>]+>', '', caption_match.group(1)).strip()
                            caption_text = html.unescape(caption_text)
                            if caption_text:
                                result["title"] = caption_text[:120]

                        # 3. Direct Video (.mp4)
                        video_matches = re.findall(r'<source[^>]+src="([^"]+)"', clean_text)
                        if not video_matches:
                            video_matches = re.findall(r'https://[^"\'\s<>]+\.mp4[^"\'\s<>]*', clean_text)

                        for v in video_matches:
                            v_clean = html.unescape(v)
                            if "static.cdninstagram.com" not in v_clean:
                                result["video_url"] = v_clean
                                result["media_type"] = "video"
                                break

                        # 4. Images (.jpg)
                        raw_imgs = re.findall(
                            r'<div class="[^"]*(?:SoloMediaContainer|SingleInnerMediaContainer|CarouselInnerMediaContainer)[^"]*"[^>]*>.*?<img[^>]+src="([^"]+)"',
                            clean_text,
                            re.DOTALL,
                        )
                        if not raw_imgs:
                            raw_jpgs = re.findall(r'https://[^"\'\s<>]+\.jpg[^"\'\s<>]*', clean_text)
                            raw_imgs = [
                                j for j in raw_jpgs
                                if not any(x in j for x in ['s100x100', 's150x150', 'static.cdninstagram', 't51.2885-19'])
                            ]

                        final_imgs: List[str] = []
                        for img in raw_imgs:
                            clean_img = html.unescape(img)
                            if clean_img not in final_imgs and 't51.2885-19' not in clean_img:
                                final_imgs.append(clean_img)

                        if final_imgs:
                            result["image_urls"] = final_imgs
                            result["thumbnail_url"] = final_imgs[0]
                            if not result["media_type"]:
                                result["media_type"] = "images"

                        if result["video_url"] or result["image_urls"]:
                            logger.info(f"⚡ [Threads] Tier 1 resolved: type={result['media_type']}, images={len(result['image_urls'])}, video={'yes' if result['video_url'] else 'no'}")
                            return result
        except Exception as e:
            logger.debug(f"Threads Tier 1 embed resolution failed: {e}")

        # Tier 2: Facebook External Hit Crawler
        try:
            main_url = f"https://www.threads.net/t/{shortcode}"
            headers_bot = {
                "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
                "Accept": "text/html,*/*",
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(main_url, headers=headers_bot, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                    if resp.status == 200:
                        raw_html = await resp.text()
                        og_v = re.findall(r'<meta property="og:video(?::secure_url)?" content="([^"]+)"', raw_html)
                        og_i = re.findall(r'<meta property="og:image" content="([^"]+)"', raw_html)
                        og_d = re.findall(r'<meta property="og:description" content="([^"]+)"', raw_html)

                        if og_d:
                            desc = html.unescape(og_d[0]).strip()
                            if desc:
                                result["title"] = desc[:120]

                        if og_v:
                            result["video_url"] = html.unescape(og_v[0])
                            result["media_type"] = "video"

                        if og_i:
                            imgs = [html.unescape(i) for i in og_i]
                            result["image_urls"] = imgs
                            result["thumbnail_url"] = imgs[0]
                            if not result["media_type"]:
                                result["media_type"] = "images"

                        if result["video_url"] or result["image_urls"]:
                            logger.info(f"⚡ [Threads] Tier 2 crawler resolved: type={result['media_type']}, images={len(result['image_urls'])}")
                            return result
        except Exception as e:
            logger.debug(f"Threads Tier 2 crawler resolution failed: {e}")

        # Tier 3: Cobalt CDN Mirror Fallback
        try:
            cdn_info = await fetch_cobalt_mirror(clean_url, is_audio=False, timeout=3.5)
            if cdn_info:
                if cdn_info.get("video_url"):
                    result["video_url"] = cdn_info["video_url"]
                    result["media_type"] = "video"
                    result["title"] = cdn_info.get("title", result["title"])
                    result["uploader"] = cdn_info.get("uploader", result["uploader"])
                    result["thumbnail_url"] = cdn_info.get("thumbnail_url")
                    return result
                elif cdn_info.get("image_urls"):
                    result["image_urls"] = cdn_info["image_urls"]
                    result["media_type"] = "images"
                    result["title"] = cdn_info.get("title", result["title"])
                    result["uploader"] = cdn_info.get("uploader", result["uploader"])
                    result["thumbnail_url"] = cdn_info["image_urls"][0]
                    return result
        except Exception as e:
            logger.debug(f"Threads Tier 3 Cobalt mirror failed: {e}")

        return result

    async def fast_scan(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Fast scan Threads URL: distinguishes between photo/carousel posts and video posts.
        """
        resolved = await self.resolve_threads_media(url)
        if resolved and resolved.get("media_type") == "images" and resolved.get("image_urls"):
            return {
                "type": "images",
                "platform": self.display_name,
                "platform_id": self.platform_id,
                "image_urls": resolved["image_urls"],
                "title": resolved.get("title", "Threads Photos"),
                "uploader": resolved.get("uploader", "Threads Creator"),
            }
        elif resolved and resolved.get("video_url"):
            return {
                "type": "video",
                "platform": self.display_name,
                "platform_id": self.platform_id,
                "image_urls": [],
                "video_url": resolved["video_url"],
                "title": resolved.get("title", "Threads Video"),
                "uploader": resolved.get("uploader", "Threads Creator"),
                "thumbnail_url": resolved.get("thumbnail_url"),
            }
        return None

    async def download_images(self, url: str) -> Any:
        """
        Downloads or extracts HD images / carousel photos from Threads.
        """
        from bot.downloader import DownloadResult

        resolved = await self.resolve_threads_media(url)
        if resolved and resolved.get("image_urls"):
            img_list = resolved["image_urls"]
            return DownloadResult(
                success=True,
                is_image=True,
                image_urls=img_list,
                title=resolved.get("title", "Threads Photos"),
                uploader=resolved.get("uploader", "Threads Creator"),
                direct_url=img_list[0] if img_list else None,
            )

        # Fallback to base class
        return await super().download_images(url)

    async def download_video(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = await expand_redirect_url(url)
        file_id = uuid.uuid4().hex
        output_video = os.path.join(config.temp_dir, f"{file_id}.mp4")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        # Step 1: Direct Threads Resolution (Embed / Crawler / Cobalt)
        resolved = await self.resolve_threads_media(clean_url)
        if resolved and resolved.get("video_url"):
            direct_video_url = resolved["video_url"]
            ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                direct_video_url,
                output_video,
                max_size_mb=max_size_mb,
                max_retries=2,
                headers={"Referer": "https://www.threads.net/"},
            )
            if ok:
                thumb_path = await download_thumbnail(resolved.get("thumbnail_url"), output_thumb)
                return DownloadResult(
                    success=True,
                    filepath=output_video,
                    filename=f"{file_id}.mp4",
                    filesize_bytes=total_bytes,
                    filesize_mb=filesize_mb,
                    title=resolved.get("title", "Threads Video"),
                    uploader=resolved.get("uploader", "Threads Creator"),
                    duration=0,
                    thumbnail_path=thumb_path,
                    is_over_50mb=(total_bytes > TELEGRAM_FILE_LIMIT_BYTES),
                    is_audio=False,
                    direct_url=direct_video_url,
                )
            elif err and "FILE_TOO_LARGE" in err:
                return DownloadResult(
                    success=True,
                    is_over_50mb=True,
                    direct_url=direct_video_url,
                    title=resolved.get("title", "Threads Video"),
                    uploader=resolved.get("uploader", "Threads Creator"),
                    filesize_mb=filesize_mb,
                    duration=0,
                    thumbnail_path=resolved.get("thumbnail_url"),
                    is_audio=False,
                )

        # Step 2: yt-dlp fallback
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.execute_ytdlp_video, clean_url, output_base, max_size_mb, self._threads_custom_opts()
        )

    async def download_audio(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = await expand_redirect_url(url)
        file_id = uuid.uuid4().hex
        raw_video = os.path.join(config.temp_dir, f"{file_id}_raw.mp4")
        final_mp3 = os.path.join(config.temp_dir, f"{file_id}.mp3")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        # Step 1: Direct Threads Resolution (Download video stream & convert to MP3)
        resolved = await self.resolve_threads_media(clean_url)
        if resolved and resolved.get("video_url"):
            direct_video_url = resolved["video_url"]
            ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                direct_video_url,
                raw_video,
                max_size_mb=max_size_mb,
                max_retries=2,
                headers={"Referer": "https://www.threads.net/"},
            )
            if ok:
                converted = await convert_to_mp3(raw_video, final_mp3)
                chosen_filepath = final_mp3 if converted else raw_video
                cleanup_files(raw_video if converted else None)

                filesize = os.path.getsize(chosen_filepath)
                filesize_mb = round(filesize / (1024 * 1024), 2)
                thumb_path = await download_thumbnail(resolved.get("thumbnail_url"), output_thumb)

                return DownloadResult(
                    success=True,
                    filepath=chosen_filepath,
                    filename=f"{file_id}.mp3",
                    filesize_bytes=filesize,
                    filesize_mb=filesize_mb,
                    title=resolved.get("title", "Threads Audio"),
                    uploader=resolved.get("uploader", "Threads Creator"),
                    duration=0,
                    thumbnail_path=thumb_path,
                    is_over_50mb=(filesize > TELEGRAM_FILE_LIMIT_BYTES),
                    is_audio=True,
                    direct_url=direct_video_url,
                )
            elif err and "FILE_TOO_LARGE" in err:
                return DownloadResult(
                    success=True,
                    is_over_50mb=True,
                    direct_url=direct_video_url,
                    title=resolved.get("title", "Threads Audio"),
                    uploader=resolved.get("uploader", "Threads Creator"),
                    filesize_mb=filesize_mb,
                    duration=0,
                    thumbnail_path=resolved.get("thumbnail_url"),
                    is_audio=True,
                )

        # Step 2: yt-dlp fallback
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.execute_ytdlp_audio, clean_url, output_base, max_size_mb, self._threads_custom_opts()
        )

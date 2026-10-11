import os
import re
import html
import uuid
import asyncio
import logging
from typing import Optional, Dict, Any, List, Tuple
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


class FacebookPlatform(BasePlatform):
    platform_id = "facebook"
    display_name = "📘 Facebook"
    patterns = [
        re.compile(r'(?:https?://)?(?:[a-zA-Z0-9-]+\.)?(?:facebook\.com|fb\.com|fb\.watch|fb\.gg|fb\.me)'),
    ]

    def _fb_custom_opts(self) -> Dict[str, Any]:
        return {
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9,km;q=0.8",
                "Sec-Fetch-Mode": "navigate",
            }
        }

    @staticmethod
    def normalize_fb_url(url: str) -> str:
        """
        Normalizes Facebook video and reel URLs to canonical web endpoints.
        Specifically converts /reel/<id> to https://www.facebook.com/video.php?v=<id>,
        which bypasses Facebook's mobile redirect barrier and parses 100% reliably in yt-dlp.
        """
        if not url:
            return url

        # Reel pattern: facebook.com/reel/<id>
        reel_match = re.search(r'facebook\.com/reel/(\d+)', url)
        if reel_match:
            return f"https://www.facebook.com/video.php?v={reel_match.group(1)}"

        # Watch pattern with v query param: facebook.com/watch/?v=<id>
        watch_match = re.search(r'facebook\.com/watch/?\?(?:[^#]+&)?v=(\d+)', url)
        if watch_match:
            return f"https://www.facebook.com/video.php?v={watch_match.group(1)}"

        # Direct videos path: facebook.com/<user>/videos/<id>
        videos_match = re.search(r'facebook\.com/[^/?#]+/videos/(\d+)', url)
        if videos_match:
            return f"https://www.facebook.com/video.php?v={videos_match.group(1)}"

        # Gaming / fb.gg video: fb.gg/v/<id>
        fbgg_match = re.search(r'fb\.gg/v/(\d+)', url)
        if fbgg_match:
            return f"https://www.facebook.com/video.php?v={fbgg_match.group(1)}"

        return url

    async def _extract_facebook_media(self, url: str) -> Dict[str, Any]:
        """
        Extracts high-resolution video streams, photos, stories, and metadata
        directly from the Facebook page HTML.
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,km;q=0.8",
            "Sec-Fetch-Mode": "navigate",
        }

        page_html = ""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=8.0)) as resp:
                    page_html = await resp.text(errors="ignore")
        except Exception as e:
            logger.debug(f"Direct Facebook HTML fetch error: {e}")

        if not page_html:
            return {}

        clean = page_html.replace('\\/', '/')

        # Title & Uploader extraction
        title_m = re.findall(r'<meta\s+(?:property|name)="og:title"\s+content="([^"]+)"', page_html, re.I)
        if not title_m:
            title_m = re.findall(r'<title>([^<]+)</title>', page_html, re.I)
        title = html.unescape(title_m[0]).strip() if title_m else "Facebook Media"

        uploader_m = re.findall(r'<meta\s+(?:property|name)="og:site_name"\s+content="([^"]+)"', page_html, re.I)
        uploader = html.unescape(uploader_m[0]).strip() if uploader_m else "Facebook Creator"

        # Direct Video Stream extraction (HD & SD)
        hd_streams = re.findall(r'browser_native_hd_url["\']:\s*["\'](https?://[^"\']+)["\']', clean)
        hd_streams += re.findall(r'playable_url_quality_hd["\']:\s*["\'](https?://[^"\']+)["\']', clean)
        sd_streams = re.findall(r'browser_native_sd_url["\']:\s*["\'](https?://[^"\']+)["\']', clean)
        sd_streams += re.findall(r'playable_url["\']:\s*["\'](https?://[^"\']+)["\']', clean)
        generic_vids = re.findall(r'https://video[^\s"\'<>]+\.mp4[^\s"\'<>]*', clean)

        video_url = None
        if hd_streams:
            video_url = html.unescape(hd_streams[0])
        elif sd_streams:
            video_url = html.unescape(sd_streams[0])
        elif generic_vids:
            video_url = html.unescape(generic_vids[0])

        # High-res Photos & Stories extraction
        og_images = re.findall(r'<meta\s+(?:property|name)="og:image"\s+content="([^"]+)"', page_html, re.I)
        json_uris = re.findall(r'"(?:uri|image_uri)":\s*"(https://scontent[^"]+)"', clean)
        raw_scontent = re.findall(r'https://scontent[^\s"\'<>]+\.(?:jpg|jpeg|png)(?:\?[^\s"\'<>]*)?', clean)

        candidates = og_images + json_uris + raw_scontent
        photos: List[str] = []
        seen = set()
        for c in candidates:
            u = html.unescape(c).strip()
            # Ignore micro icons, emojis, badges, avatar thumbnails
            if any(skip in u.lower() for skip in ['/p50x50/', '/p100x100/', '/s50x50/', 'emoji', 'badge', 'icon']):
                continue
            core_id = re.search(r'/([^/?#]+\.(?:jpg|jpeg|png))', u)
            key = core_id.group(1) if core_id else u.split('?')[0]
            if key not in seen:
                seen.add(key)
                photos.append(u)

        thumbnail_url = photos[0] if photos else (og_images[0] if og_images else None)

        return {
            "title": title,
            "uploader": uploader,
            "video_url": video_url,
            "photos": photos,
            "thumbnail_url": thumbnail_url,
        }

    async def fast_scan(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Fast scan Facebook links to accurately distinguish between
        photo posts, multi-photo albums, stories, reels, and videos.
        """
        clean_url = await expand_redirect_url(url)

        # 1. Check Cobalt mirror first for instant cached response
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

        # 2. Inspect URL structure
        is_photo_url = any(p in clean_url for p in ["/photo.php", "/photo/", "/share/p/"])
        is_video_url = any(p in clean_url for p in ["/reel/", "/videos/", "/watch", "fb.watch", "/share/r/", "/share/v/", "fb.gg/v/"])
        is_story_or_post = any(p in clean_url for p in ["story.php", "/stories/", "/posts/"])

        # Photo posts or albums
        if is_photo_url or is_story_or_post:
            media = await self._extract_facebook_media(clean_url)
            if media.get("photos"):
                # If there's no direct video stream, or if it was explicitly a photo post
                if not media.get("video_url") or is_photo_url:
                    return {
                        "type": "images",
                        "platform": self.display_name,
                        "platform_id": self.platform_id,
                        "image_urls": media["photos"],
                        "title": media.get("title", "Facebook Photos"),
                        "uploader": media.get("uploader", "Facebook Creator"),
                        "audio_url": None,
                    }
            if media.get("video_url"):
                return {
                    "type": "video",
                    "platform": self.display_name,
                    "platform_id": self.platform_id,
                    "image_urls": [],
                    "title": media.get("title", "Facebook Video"),
                    "uploader": media.get("uploader", "Facebook Creator"),
                    "audio_url": None,
                }

        # Reels & Videos
        if is_video_url:
            return {
                "type": "video",
                "platform": self.display_name,
                "platform_id": self.platform_id,
                "image_urls": [],
                "title": "Facebook Video",
                "uploader": "Facebook Creator",
                "audio_url": None,
            }

        return None

    async def download_images(self, url: str) -> Any:
        from bot.downloader import DownloadResult

        clean_url = await expand_redirect_url(url)
        media = await self._extract_facebook_media(clean_url)
        photos = media.get("photos") or []

        if photos:
            return DownloadResult(
                success=True,
                is_image=True,
                image_urls=photos,
                title=media.get("title", "Facebook Photos"),
                uploader=media.get("uploader", "Facebook Creator"),
                direct_url=photos[0],
            )

        # Fallback to super implementation
        return await super().download_images(url)

    async def download_video(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = await expand_redirect_url(url)
        norm_url = self.normalize_fb_url(clean_url)
        file_id = uuid.uuid4().hex
        output_video = os.path.join(config.temp_dir, f"{file_id}.mp4")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        # Step 1: Ultra-fast Direct CDN extraction (HTML & Cobalt)
        try:
            media = await self._extract_facebook_media(clean_url)
            direct_vurl = media.get("video_url")

            # Fallback to Cobalt mirror if direct HTML didn't expose MP4
            if not direct_vurl:
                cdn_info = await fetch_cobalt_mirror(clean_url, is_audio=False, timeout=3.5)
                if cdn_info and cdn_info.get("video_url"):
                    direct_vurl = cdn_info["video_url"]
                    if not media.get("title") or media.get("title") == "Facebook Media":
                        media["title"] = cdn_info.get("title", "Facebook Video")
                    if not media.get("uploader") or media.get("uploader") == "Facebook Creator":
                        media["uploader"] = cdn_info.get("uploader", "Facebook Creator")

            if direct_vurl:
                ok, total_bytes, filesize_mb, err = await download_direct_cdn_stream(
                    direct_vurl, output_video, max_size_mb, max_retries=2
                )
                thumb_url = media.get("thumbnail_url")
                thumb_path = await download_thumbnail(thumb_url, output_thumb) if thumb_url else None

                if ok:
                    return DownloadResult(
                        success=True,
                        filepath=output_video,
                        filename=f"{file_id}.mp4",
                        filesize_bytes=total_bytes,
                        filesize_mb=filesize_mb,
                        title=media.get("title", "Facebook Video"),
                        uploader=media.get("uploader", "Facebook Creator"),
                        duration=0,
                        thumbnail_path=thumb_path,
                        is_over_50mb=(total_bytes > TELEGRAM_FILE_LIMIT_BYTES),
                        is_audio=False,
                        direct_url=direct_vurl,
                    )
                elif err and "FILE_TOO_LARGE" in err:
                    return DownloadResult(
                        success=True,
                        is_over_50mb=True,
                        direct_url=direct_vurl,
                        title=media.get("title", "Facebook Video"),
                        uploader=media.get("uploader", "Facebook Creator"),
                        filesize_mb=filesize_mb,
                        duration=0,
                        thumbnail_path=thumb_path or thumb_url,
                        is_audio=False,
                    )
        except Exception as e:
            logger.debug(f"Direct Facebook video extraction failed: {e}")

        # Step 2: High-reliability yt-dlp extraction on normalized URL
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(
            None, self.execute_ytdlp_video, norm_url, output_base, max_size_mb, self._fb_custom_opts()
        )

        # Step 3: If yt-dlp fails because the URL only contained photos, auto-delegate to download_images
        if not res.success:
            try:
                media = await self._extract_facebook_media(clean_url)
                if media.get("photos"):
                    logger.info("Facebook video extraction returned no video; falling back to extracted photos.")
                    return DownloadResult(
                        success=True,
                        is_image=True,
                        image_urls=media["photos"],
                        title=media.get("title", "Facebook Photos"),
                        uploader=media.get("uploader", "Facebook Creator"),
                        direct_url=media["photos"][0],
                    )
            except Exception:
                pass

        return res

    async def download_audio(self, url: str, max_size_mb: float = 2000.0) -> Any:
        from bot.downloader import DownloadResult

        clean_url = await expand_redirect_url(url)
        norm_url = self.normalize_fb_url(clean_url)
        file_id = uuid.uuid4().hex
        raw_audio = os.path.join(config.temp_dir, f"{file_id}_raw.audio")
        final_mp3 = os.path.join(config.temp_dir, f"{file_id}.mp3")
        output_thumb = os.path.join(config.temp_dir, f"{file_id}.jpg")

        # Step 1: Cobalt audio mirror query
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

        # Step 2: yt-dlp fallback on normalized URL
        output_base = os.path.join(config.temp_dir, file_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.execute_ytdlp_audio, norm_url, output_base, max_size_mb, self._fb_custom_opts()
        )

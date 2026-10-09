import os
import re
import uuid
import shutil
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Tuple, List
from PIL import Image
import yt_dlp
import aiohttp
import aiofiles
from bot.config import config
from bot.cdn_api import DirectCDN, download_direct_cdn_stream
from bot.platforms.router import detect_platform, get_platform_name, get_all_platforms
from bot.platforms.base import ensure_ios_compatible_video_sync

logger = logging.getLogger(__name__)

# Telegram Bot API file upload size limit (50 MB in bytes)
TELEGRAM_FILE_LIMIT_BYTES = 50 * 1024 * 1024  # 50 MB
URL_REGEX = re.compile(
    r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
)


def get_ffmpeg_path() -> Optional[str]:
    """Dynamically finds FFmpeg binary from system PATH, aaPanel App Store paths, or imageio-ffmpeg package."""
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg and os.path.exists(sys_ffmpeg):
        return sys_ffmpeg

    # Common Linux and aaPanel "ffmpeg manager" installation paths
    import glob
    aapanel_candidates = [
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/www/server/ffmpeg/bin/ffmpeg",
        "/www/server/ffmpeg/ffmpeg",
        "/usr/local/ffmpeg/bin/ffmpeg",
        "/opt/ffmpeg/bin/ffmpeg",
    ]
    for p in aapanel_candidates + glob.glob("/www/server/ffmpeg*/bin/ffmpeg") + glob.glob("/www/server/ffmpeg*/ffmpeg"):
        if os.path.exists(p) and (os.access(p, os.X_OK) or not hasattr(os, "access")):
            return p

    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        if os.path.exists(ffmpeg_exe):
            return ffmpeg_exe
    except Exception as e:
        logger.warning(f"Could not load imageio-ffmpeg: {e}")

    return None


def get_cookie_file() -> Optional[str]:
    """Finds cookies.txt file if present and contains real tab-separated cookie entries."""
    candidates = []
    if config.cookies_file and os.path.isabs(config.cookies_file) and os.path.exists(config.cookies_file):
        candidates.append(config.cookies_file)
    
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(root_dir, config.cookies_file or "cookies.txt")
    candidates.append(candidate)

    for c in candidates:
        if os.path.exists(c) and os.path.getsize(c) > 100:
            try:
                with open(c, "r", encoding="utf-8", errors="ignore") as f:
                    cookie_lines = [l for l in f if "\t" in l and not l.startswith("#")]
                    if len(cookie_lines) >= 3:
                        return c
            except Exception:
                pass
    return None


def sanitize_thumbnail(thumb_path: Optional[str]) -> Optional[str]:
    """
    Converts any thumbnail (including WEBP) to standard JPEG.
    Telegram Bot API strictly requires JPEG/PNG for audio/video thumbnails.
    """
    if not thumb_path or not os.path.exists(thumb_path):
        return None
    
    ext = os.path.splitext(thumb_path)[1].lower()
    if ext in [".jpg", ".jpeg", ".png"]:
        return thumb_path

    try:
        jpg_path = os.path.splitext(thumb_path)[0] + "_thumb.jpg"
        with Image.open(thumb_path) as img:
            img.convert("RGB").save(jpg_path, "JPEG", quality=85)
        return jpg_path
    except Exception as e:
        logger.warning(f"Thumbnail JPEG conversion failed: {e}")
        return None


async def convert_to_mp3(input_path: str, output_path: str) -> bool:
    """Converts any audio file to standard MP3 (192kbps) via FFmpeg."""
    ffmpeg_exe = get_ffmpeg_path()
    if not ffmpeg_exe:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg_exe, "-y", "-i", input_path, "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k", output_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        return proc.returncode == 0 and os.path.exists(output_path)
    except Exception as e:
        logger.warning(f"FFmpeg MP3 conversion error: {e}")
        return False


@dataclass
class DownloadResult:
    success: bool
    filepath: Optional[str] = None
    filename: Optional[str] = None
    filesize_bytes: int = 0
    filesize_mb: float = 0.0
    title: str = "Media"
    uploader: str = "Unknown"
    duration: int = 0
    thumbnail_path: Optional[str] = None
    is_over_50mb: bool = False
    is_audio: bool = False
    error: Optional[str] = None
    web_token: Optional[str] = None
    direct_url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    is_image: bool = False
    image_urls: List[str] = field(default_factory=list)
    image_paths: List[str] = field(default_factory=list)


def extract_first_url(text: str) -> Optional[str]:
    """Extracts the first valid URL from a message text."""
    if not text or not isinstance(text, str):
        return None
    match = URL_REGEX.search(text.strip())
    return match.group(0) if match else None


def cleanup_files(*filepaths: Optional[str]) -> None:
    """Instantly and safely removes temporary files from server disk."""
    for path in filepaths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
                logger.info(f"Cleaned up temporary file: {path}")
            except Exception as e:
                logger.warning(f"Could not remove temp file {path}: {e}")


async def _download_thumbnail(thumb_url: str, output_path: str) -> Optional[str]:
    """Downloads thumbnail image from CDN and ensures it is a valid JPEG."""
    if not thumb_url:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(thumb_url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status == 200:
                    async with aiofiles.open(output_path, "wb") as f:
                        await f.write(await resp.read())
                    return sanitize_thumbnail(output_path)
    except Exception:
        pass
    return None


def _build_video_opts(download_path_template: str, use_cookies: bool = True) -> Dict[str, Any]:
    """Builds standard yt-dlp extraction and download options for video."""
    ffmpeg_exe = get_ffmpeg_path()
    cookie_file = get_cookie_file() if use_cookies else None

    if cookie_file:
        extractor_args = {
            "youtube": {
                "player_client": ["web", "mweb", "android", "ios"],
            }
        }
    else:
        extractor_args = {
            "youtube": {
                "player_skip": ["webpage", "configs"],
                "player_client": ["ios", "android", "tv_embedded"],
            }
        }

    format_spec = (
        "bestvideo[ext=mp4][vcodec^=avc1][filesize<48M]+bestaudio[ext=m4a][acodec^=mp4a][filesize<8M]/"
        "bestvideo[vcodec^=avc1][filesize<48M]+bestaudio[acodec^=mp4a][filesize<8M]/"
        "best[ext=mp4][vcodec^=avc1][filesize<50M]/"
        "bestvideo[ext=mp4][filesize<48M]+bestaudio[ext=m4a][filesize<8M]/"
        "best[ext=mp4][filesize<50M]/"
        "bestvideo[filesize<48M]+bestaudio[filesize<8M]/"
        "best[filesize<50M]/"
        "bestvideo[height<=1080][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
        "bestvideo[height<=1080]+bestaudio/"
        "best[height<=1080]/"
        "bestvideo+bestaudio/best"
    ) if ffmpeg_exe else "best[vcodec^=avc1][acodec^=mp4a]/best[ext=mp4]/best"

    opts: Dict[str, Any] = {
        "outtmpl": download_path_template,
        "format": format_spec,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "writethumbnail": True,
        "updatetime": False,
        "extractor_args": extractor_args,
    }

    if getattr(config, "proxy_url", None):
        opts["proxy"] = config.proxy_url

    if cookie_file:
        opts["cookiefile"] = cookie_file

    if ffmpeg_exe:
        opts["ffmpeg_location"] = ffmpeg_exe
        opts["postprocessors"] = [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }
        ]
        opts["postprocessor_args"] = {
            "ffmpeg": ["-movflags", "+faststart", "-metadata", "creation_time=now"]
        }

    return opts


def _build_audio_opts(download_path_template: str, use_cookies: bool = True) -> Dict[str, Any]:
    """Builds yt-dlp extraction and download options for MP3 audio."""
    ffmpeg_exe = get_ffmpeg_path()
    cookie_file = get_cookie_file() if use_cookies else None

    if cookie_file:
        extractor_args = {
            "youtube": {
                "player_client": ["web", "mweb", "android", "ios"],
            }
        }
    else:
        extractor_args = {
            "youtube": {
                "player_skip": ["webpage", "configs"],
                "player_client": ["ios", "android", "tv_embedded"],
            }
        }

    opts: Dict[str, Any] = {
        "outtmpl": download_path_template,
        "format": "bestaudio/bestvideo*+bestaudio/best/b",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "writethumbnail": True,
        "extractor_args": extractor_args,
    }

    if getattr(config, "proxy_url", None):
        opts["proxy"] = config.proxy_url

    if cookie_file:
        opts["cookiefile"] = cookie_file

    if ffmpeg_exe:
        opts["ffmpeg_location"] = ffmpeg_exe
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]

    return opts


def _download_video_worker(url: str, output_base: str, max_size_mb: float) -> DownloadResult:
    """Synchronous fallback worker to extract and download video media using yt-dlp."""
    download_template = f"{output_base}.%(ext)s"
    cookie_available = get_cookie_file() is not None
    attempts = [True, False] if cookie_available else [False]

    for use_cookies in attempts:
        ydl_opts = _build_video_opts(download_template, use_cookies=use_cookies)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    if use_cookies and cookie_available:
                        continue
                    return DownloadResult(success=False, error="Failed to fetch video information.")

                title = info.get("title", "Video")
                uploader = info.get("uploader") or info.get("channel") or "Unknown"
                duration = int(info.get("duration") or 0)
                
                raw_size = info.get("filesize") or info.get("filesize_approx")
                if raw_size:
                    est_mb = round(raw_size / (1024 * 1024), 2)
                    if est_mb > max_size_mb:
                        return DownloadResult(
                            success=False,
                            error=f"FILE_TOO_LARGE:{est_mb}:{max_size_mb}"
                        )

                ydl.download([url])

                downloaded_video = None
                for ext in ["mp4", "mkv", "webm", "mov", "ts"]:
                    candidate = f"{output_base}.{ext}"
                    if os.path.exists(candidate):
                        downloaded_video = candidate
                        break

                if downloaded_video:
                    downloaded_video = ensure_ios_compatible_video_sync(downloaded_video)

                if not downloaded_video:
                    if use_cookies and cookie_available:
                        continue
                    return DownloadResult(success=False, error="Video file could not be generated.")

                filesize = os.path.getsize(downloaded_video)
                filesize_mb = round(filesize / (1024 * 1024), 2)

                thumbnail_file = None
                for thumb_ext in ["jpg", "jpeg", "webp", "png"]:
                    candidate_thumb = f"{output_base}.{thumb_ext}"
                    if os.path.exists(candidate_thumb):
                        thumbnail_file = sanitize_thumbnail(candidate_thumb)
                        break

                if filesize_mb > max_size_mb:
                    cleanup_files(downloaded_video, thumbnail_file)
                    return DownloadResult(
                        success=False,
                        error=f"FILE_TOO_LARGE:{filesize_mb}:{max_size_mb}"
                    )

                is_over_50mb = filesize > TELEGRAM_FILE_LIMIT_BYTES
                filename = os.path.basename(downloaded_video)

                return DownloadResult(
                    success=True,
                    filepath=downloaded_video,
                    filename=filename,
                    filesize_bytes=filesize,
                    filesize_mb=filesize_mb,
                    title=title,
                    uploader=uploader,
                    duration=duration,
                    thumbnail_path=thumbnail_file,
                    is_over_50mb=is_over_50mb,
                    is_audio=False,
                )

        except Exception as e:
            if use_cookies and cookie_available:
                logger.warning(f"Video download with cookies failed ({e}), retrying without cookies...")
                continue
            logger.error(f"yt-dlp video download failed for URL {url}: {e}", exc_info=True)
            return DownloadResult(success=False, error=str(e))

    return DownloadResult(success=False, error="Download failed.")


def _download_audio_worker(url: str, output_base: str, max_size_mb: float) -> DownloadResult:
    """Synchronous fallback worker to extract and convert audio to MP3 using yt-dlp."""
    download_template = f"{output_base}.%(ext)s"
    cookie_available = get_cookie_file() is not None
    attempts = [True, False] if cookie_available else [False]

    for use_cookies in attempts:
        ydl_opts = _build_audio_opts(download_template, use_cookies=use_cookies)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    if use_cookies and cookie_available:
                        continue
                    return DownloadResult(success=False, error="Failed to fetch audio information.")

                title = info.get("title", "Audio")
                uploader = info.get("uploader") or info.get("channel") or "Unknown Artist"
                duration = int(info.get("duration") or 0)

                ydl.download([url])

                downloaded_audio = None
                for ext in ["mp3", "m4a", "aac", "ogg", "opus", "webm"]:
                    candidate = f"{output_base}.{ext}"
                    if os.path.exists(candidate):
                        downloaded_audio = candidate
                        break

                if not downloaded_audio:
                    if use_cookies and get_cookie_file():
                        continue
                    return DownloadResult(success=False, error="Audio file could not be generated.")

                filesize = os.path.getsize(downloaded_audio)
                filesize_mb = round(filesize / (1024 * 1024), 2)

                thumbnail_file = None
                for thumb_ext in ["jpg", "jpeg", "webp", "png"]:
                    candidate_thumb = f"{output_base}.{thumb_ext}"
                    if os.path.exists(candidate_thumb):
                        thumbnail_file = sanitize_thumbnail(candidate_thumb)
                        break

                if filesize_mb > max_size_mb:
                    cleanup_files(downloaded_audio, thumbnail_file)
                    return DownloadResult(
                        success=False,
                        error=f"FILE_TOO_LARGE:{filesize_mb}:{max_size_mb}"
                    )

                is_over_50mb = filesize > TELEGRAM_FILE_LIMIT_BYTES
                filename = os.path.basename(downloaded_audio)

                return DownloadResult(
                    success=True,
                    filepath=downloaded_audio,
                    filename=filename,
                    filesize_bytes=filesize,
                    filesize_mb=filesize_mb,
                    title=title,
                    uploader=uploader,
                    duration=duration,
                    thumbnail_path=thumbnail_file,
                    is_over_50mb=is_over_50mb,
                    is_audio=True,
                )

        except Exception as e:
            if use_cookies and get_cookie_file():
                logger.warning(f"Audio download with cookies failed ({e}), retrying without cookies...")
                continue
            logger.error(f"yt-dlp audio download failed for URL {url}: {e}", exc_info=True)
            return DownloadResult(success=False, error=str(e))

    return DownloadResult(success=False, error="Audio download failed.")


async def download_video(url: str, max_size_mb: float = 2000.0) -> DownloadResult:
    """
    Auto-detects URL platform and dispatches to the matching platform module
    (YouTube, TikTok, Facebook, Bilibili, WeTV, Instagram, Threads, Twitter, etc.).
    """
    platform = detect_platform(url)
    logger.info(f"⚡ [Platform Router] Dispatched video download to {platform.display_name} for URL: {url}")
    return await platform.download_video(url, max_size_mb)


async def download_audio(url: str, max_size_mb: float = 2000.0) -> DownloadResult:
    """
    Auto-detects URL platform and dispatches to the matching platform module
    (YouTube, TikTok, Facebook, Bilibili, WeTV, Instagram, Threads, Twitter, etc.).
    """
    platform = detect_platform(url)
    logger.info(f"⚡ [Platform Router] Dispatched audio download to {platform.display_name} for URL: {url}")
    return await platform.download_audio(url, max_size_mb)


async def scan_url(url: str) -> Dict[str, Any]:
    """
    Ultra-fast scanner to detect if URL contains images, photo carousel, or video
    before performing heavy downloads.
    """
    platform = detect_platform(url)
    try:
        scan_res = await platform.fast_scan(url)
        if scan_res:
            return scan_res
    except Exception as e:
        logger.debug(f"Fast scan failed on {platform.display_name}: {e}")

    return {
        "type": "video",
        "platform": platform.display_name,
        "platform_id": platform.platform_id,
        "image_urls": [],
    }


async def download_images(url: str) -> DownloadResult:
    """
    Auto-detects URL platform and dispatches image extraction (single or carousel).
    """
    platform = detect_platform(url)
    logger.info(f"⚡ [Platform Router] Dispatched image download to {platform.display_name} for URL: {url}")
    res = await platform.download_images(url)
    if res:
        return res
    return DownloadResult(success=False, error="No images found for this URL.", is_image=True)



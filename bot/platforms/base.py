import os
import re
import time
import uuid
import asyncio
import logging
import subprocess
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Pattern
import aiohttp
import aiofiles
import yt_dlp
from PIL import Image

from bot.config import config

logger = logging.getLogger(__name__)

def get_telegram_limit_bytes() -> int:
    """Returns Telegram direct in-app upload limit (strictly 50MB for in-chat delivery)."""
    return int(getattr(config, "telegram_direct_limit_mb", 50.0) * 1024 * 1024)

# Strictly 50 MB in bytes for direct in-chat delivery
TELEGRAM_FILE_LIMIT_BYTES = 50 * 1024 * 1024


def get_ffmpeg_path() -> Optional[str]:
    """Dynamically finds FFmpeg binary from system PATH, aaPanel App Store paths, or imageio-ffmpeg package."""
    import shutil
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
    """Finds cookies.txt file if present and contains valid tab-separated cookie entries."""
    candidates = []
    if config.cookies_file and os.path.isabs(config.cookies_file) and os.path.exists(config.cookies_file):
        candidates.append(config.cookies_file)
    
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


def ensure_ios_compatible_video_sync(filepath: str) -> str:
    """
    Ensures that an MP4 video file is 100% compatible with Apple iOS Photos (Camera Roll).
    - Checks video codec (must be H.264/HEVC), pixel format (must be 8-bit yuv420p), and audio codec (must be AAC).
    - If already compatible: lightning-fast remux (< 0.2s) with '-movflags +faststart' and '-metadata creation_time=now'.
    - If incompatible (VP9, AV1, Opus, 10-bit HDR, fragmented MP4): transcodes to H.264 (yuv420p) + AAC (44.1kHz).
    - Updates file system mtime so iOS Photos/Files sorts it under 'Today'.
    """
    if not filepath or not os.path.exists(filepath):
        return filepath

    ffmpeg_exe = get_ffmpeg_path()
    if not ffmpeg_exe:
        return filepath

    try:
        proc = subprocess.run(
            [ffmpeg_exe, "-hide_banner", "-i", filepath],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=15,
        )
        stderr_text = proc.stderr or ""
    except Exception as e:
        logger.warning(f"Failed to probe video with ffmpeg: {e}")
        return filepath

    vcodec = None
    pix_fmt = None
    acodec = None
    has_video = False
    has_audio = False

    for line in stderr_text.splitlines():
        line_str = line.strip()
        if "Stream #" in line_str:
            if "Video:" in line_str:
                has_video = True
                m = re.search(r'Video:\s*([a-zA-Z0-9_-]+)', line_str)
                if m:
                    vcodec = m.group(1).lower()
                for pf in ["yuv420p10le", "yuv420p10be", "yuv422p", "yuv444p", "yuv420p"]:
                    if pf in line_str:
                        pix_fmt = pf
                        break
            elif "Audio:" in line_str:
                has_audio = True
                m = re.search(r'Audio:\s*([a-zA-Z0-9_-]+)', line_str)
                if m:
                    acodec = m.group(1).lower()

    if not has_video:
        return filepath

    # iOS Camera Roll requires H.264 (avc1/h264) with 8-bit yuv420p. Audio must be AAC (mp4a/aac) or none.
    is_v_compat = (vcodec in ["h264", "avc1"]) and (pix_fmt in ["yuv420p", None])
    is_a_compat = (not has_audio) or (acodec in ["aac", "mp4a"])

    base_dir = os.path.dirname(filepath)
    temp_out = os.path.join(base_dir, f"ios_fixed_{uuid.uuid4().hex}.mp4")

    try:
        if is_v_compat and is_a_compat:
            # Fast remux: Stream copy + faststart + creation_time=now (< 0.2s)
            cmd = [
                ffmpeg_exe, "-y", "-i", filepath,
                "-map", "0:v:0",
            ]
            if has_audio:
                cmd += ["-map", "0:a:0"]
            cmd += [
                "-c", "copy",
                "-movflags", "+faststart",
                "-metadata", "creation_time=now",
                temp_out
            ]
        else:
            # Transcode to guaranteed Apple iOS Photos compatibility
            logger.info(f"Transcoding video for iOS Photos compatibility (vcodec={vcodec}, pix_fmt={pix_fmt}, acodec={acodec})")
            cmd = [
                ffmpeg_exe, "-y", "-i", filepath,
                "-map", "0:v:0",
            ]
            if has_audio:
                cmd += ["-map", "0:a:0"]
            cmd += [
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-preset", "veryfast",
                "-crf", "22",
            ]
            if has_audio:
                cmd += [
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-ar", "44100",
                ]
            cmd += [
                "-movflags", "+faststart",
                "-metadata", "creation_time=now",
                temp_out
            ]

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
        )

        if proc.returncode == 0 and os.path.exists(temp_out) and os.path.getsize(temp_out) > 0:
            target_path = filepath if filepath.lower().endswith(".mp4") else f"{os.path.splitext(filepath)[0]}.mp4"
            if target_path != filepath and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
            os.replace(temp_out, target_path)
            now = time.time()
            try:
                os.utime(target_path, (now, now))
            except Exception:
                pass
            return target_path
    except Exception as e:
        logger.warning(f"ensure_ios_compatible_video_sync error: {e}")
    finally:
        if os.path.exists(temp_out):
            try:
                os.remove(temp_out)
            except Exception:
                pass

    return filepath


async def ensure_ios_compatible_video(filepath: str) -> str:
    """Async wrapper for ensure_ios_compatible_video_sync."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, ensure_ios_compatible_video_sync, filepath)


def cleanup_files(*filepaths: Optional[str]) -> None:
    """Instantly and safely removes temporary files from server disk."""
    for path in filepaths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
                logger.info(f"Cleaned up temporary file: {path}")
            except Exception as e:
                logger.warning(f"Could not remove temp file {path}: {e}")


async def download_thumbnail(thumb_url: str, output_path: str) -> Optional[str]:
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


def extract_best_direct_url(info: Optional[Dict[str, Any]], is_audio: bool = False) -> Optional[str]:
    """
    Extracts the highest-quality direct streaming/download URL from yt-dlp info dict.
    Prioritizes progressive MP4 formats (both video and audio) for videos,
    or direct audio streams for audio downloads.
    """
    if not info:
        return None

    # 1. Direct url at root of info
    if info.get("url") and str(info["url"]).startswith("http"):
        return str(info["url"])

    # 2. Check requested_formats (when yt-dlp selected streams)
    req = info.get("requested_formats")
    if req and isinstance(req, list):
        for f in req:
            if not is_audio and f.get("vcodec") != "none" and f.get("url") and str(f["url"]).startswith("http"):
                return str(f["url"])
            if is_audio and f.get("acodec") != "none" and f.get("url") and str(f["url"]).startswith("http"):
                return str(f["url"])

    formats = info.get("formats", [])
    if not formats:
        return None

    if not is_audio:
        # Prioritize progressive (both video and audio) so file has sound when downloaded
        prog = [
            f for f in formats
            if f.get("vcodec") != "none"
            and f.get("acodec") != "none"
            and f.get("url")
            and str(f["url"]).startswith("http")
        ]
        if prog:
            prog.sort(key=lambda x: (x.get("height") or 0, x.get("tbr") or 0, x.get("filesize") or 0), reverse=True)
            return str(prog[0]["url"])

        # Fallback to best video stream
        vids = [
            f for f in formats
            if f.get("vcodec") != "none"
            and f.get("url")
            and str(f["url"]).startswith("http")
        ]
        if vids:
            vids.sort(key=lambda x: (x.get("height") or 0, x.get("tbr") or 0, x.get("filesize") or 0), reverse=True)
            return str(vids[0]["url"])
    else:
        auds = [
            f for f in formats
            if f.get("acodec") != "none"
            and f.get("url")
            and str(f["url"]).startswith("http")
        ]
        if auds:
            auds.sort(key=lambda x: (x.get("abr") or 0, x.get("tbr") or 0, x.get("filesize") or 0), reverse=True)
            return str(auds[0]["url"])

    for f in reversed(formats):
        if f.get("url") and str(f["url"]).startswith("http"):
            return str(f["url"])

    return None


class BasePlatform(ABC):
    """
    Abstract Base Platform.
    Every platform (YouTube, TikTok, Facebook, WeTV, etc.) inherits from this class.
    """
    platform_id: str = "generic"
    display_name = "🌐 Universal Web"
    patterns: List[Pattern] = []

    def can_handle(self, url: str) -> bool:
        """Determines if this platform module can handle the given URL."""
        if not url:
            return False
        clean_url = url.strip()
        clean_lower = clean_url.lower()
        for pattern in self.patterns:
            if pattern.search(clean_url) or pattern.search(clean_lower):
                return True
        return False

    @abstractmethod
    async def download_video(self, url: str, max_size_mb: float = 2000.0) -> Any:
        """Download video media. Must return DownloadResult."""
        pass

    @abstractmethod
    async def download_audio(self, url: str, max_size_mb: float = 2000.0) -> Any:
        """Download audio media. Must return DownloadResult."""
        pass

    async def fast_scan(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Default fast scan probing. Checks direct image extension or returns None.
        Subclasses can override with platform-specific lightning-fast probes.
        """
        clean = url.split("?")[0].lower()
        if any(clean.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]):
            return {
                "type": "images",
                "platform": self.display_name,
                "platform_id": self.platform_id,
                "image_urls": [url],
                "title": os.path.basename(clean),
                "uploader": "Direct CDN",
                "audio_url": None,
            }
        return None

    async def download_images(self, url: str) -> Any:
        """
        Downloads or extracts all images (single or carousel) for this platform.
        Returns DownloadResult with is_image=True and image_urls filled.
        """
        from bot.downloader import DownloadResult

        # Quick scan first
        scan = await self.fast_scan(url)
        if scan and scan.get("image_urls"):
            return DownloadResult(
                success=True,
                is_image=True,
                image_urls=scan["image_urls"],
                title=scan.get("title", "Images"),
                uploader=scan.get("uploader", "Creator"),
                direct_url=scan["image_urls"][0] if scan["image_urls"] else None,
            )

        # Fallback to yt-dlp extracting flat info or entries
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.execute_ytdlp_images, url)

    def execute_ytdlp_images(self, url: str) -> Any:
        """yt-dlp worker for extracting multi-image entries or thumbnails."""
        from bot.downloader import DownloadResult
        ydl_opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": False,
        }
        cookie_file = get_cookie_file()
        if cookie_file:
            ydl_opts["cookiefile"] = cookie_file
        if getattr(config, "proxy_url", None):
            ydl_opts["proxy"] = config.proxy_url

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return DownloadResult(success=False, error="Could not fetch image info.", is_image=True)

                image_urls = []
                # Check entries (e.g. playlist of photos / carousel)
                entries = info.get("entries")
                if entries:
                    for entry in entries:
                        if not entry:
                            continue
                        u = entry.get("url")
                        if u and str(u).startswith("http"):
                            image_urls.append(str(u))
                        elif entry.get("thumbnails"):
                            best_t = entry["thumbnails"][-1].get("url")
                            if best_t:
                                image_urls.append(str(best_t))

                # Check root thumbnails or formats
                if not image_urls:
                    if info.get("thumbnails"):
                        best_t = info["thumbnails"][-1].get("url")
                        if best_t:
                            image_urls.append(str(best_t))

                if image_urls:
                    return DownloadResult(
                        success=True,
                        is_image=True,
                        image_urls=image_urls,
                        title=info.get("title", "Images"),
                        uploader=info.get("uploader", "Creator"),
                        direct_url=image_urls[0],
                    )
        except Exception as e:
            logger.debug(f"yt-dlp image extraction error: {e}")

        return DownloadResult(success=False, error="No images found.", is_image=True)


    def build_video_ydl_opts(self, download_path_template: str, use_cookies: bool = True) -> Dict[str, Any]:
        """Default yt-dlp video options with high-speed streaming & instant Telegram faststart."""
        ffmpeg_exe = get_ffmpeg_path()
        cookie_file = get_cookie_file() if use_cookies else None

        if ffmpeg_exe:
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
            )
        else:
            # Fallback to single progressive format if FFmpeg is not installed on the system
            format_spec = "best[vcodec^=avc1][acodec^=mp4a]/best[ext=mp4]/best"

        opts: Dict[str, Any] = {
            "outtmpl": download_path_template,
            "format": format_spec,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "writethumbnail": True,
            "updatetime": False,
            "concurrent_fragment_downloads": 4,
            "buffersize": 1048576,        # 1MB buffer
            "http_chunk_size": 10485760,  # 10MB chunk
            "retries": 10,
            "fragment_retries": 10,
            "file_access_retries": 3,
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
            # FastStart (+faststart) & creation_time=now enables instant streaming and Apple Photos save compatibility
            opts["postprocessor_args"] = {
                "ffmpeg": ["-movflags", "+faststart", "-metadata", "creation_time=now"]
            }

        return opts

    def build_audio_ydl_opts(self, download_path_template: str, use_cookies: bool = True) -> Dict[str, Any]:
        """Default yt-dlp audio options with metadata embedding & concurrent fragment download."""
        ffmpeg_exe = get_ffmpeg_path()
        cookie_file = get_cookie_file() if use_cookies else None

        opts: Dict[str, Any] = {
            "outtmpl": download_path_template,
            "format": "bestaudio/bestvideo*+bestaudio/best/b",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "writethumbnail": True,
            "concurrent_fragment_downloads": 4,
            "buffersize": 1048576,
            "retries": 10,
            "fragment_retries": 10,
            "file_access_retries": 3,
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
                },
                {
                    "key": "FFmpegMetadata",
                    "add_metadata": True,
                }
            ]

        return opts

    def execute_ytdlp_video(self, url: str, output_base: str, max_size_mb: float, custom_opts: Optional[Dict[str, Any]] = None) -> Any:
        """Synchronous yt-dlp video worker with automatic direct CDN stream extraction."""
        from bot.downloader import DownloadResult

        download_template = f"{output_base}.%(ext)s"
        cookie_available = get_cookie_file() is not None
        attempts = [True, False] if cookie_available else [False]

        for use_cookies in attempts:
            ydl_opts = self.build_video_ydl_opts(download_template, use_cookies=use_cookies)
            if custom_opts:
                ydl_opts.update(custom_opts)

            direct_url = None
            title = "Video"
            uploader = "Unknown"
            duration = 0
            est_mb = None
            raw_size = None

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
                    direct_url = extract_best_direct_url(info, is_audio=False)

                    raw_size = info.get("filesize") or info.get("filesize_approx")
                    if not raw_size and info.get("requested_formats"):
                        raw_size = sum((f.get("filesize") or f.get("filesize_approx") or 0) for f in info["requested_formats"])

                    if raw_size:
                        est_mb = round(raw_size / (1024 * 1024), 2)
                        if est_mb > max_size_mb and not direct_url:
                            return DownloadResult(
                                success=False,
                                error=f"FILE_TOO_LARGE:{est_mb}:{max_size_mb}"
                            )

                    # If file is over Telegram limit (>= 50MB) and we have a direct stream URL:
                    # Return immediately to avoid server disk consumption, rate-limiting, and FFmpeg merge bottlenecks
                    if direct_url and (est_mb is not None and est_mb >= config.telegram_direct_limit_mb):
                        return DownloadResult(
                            success=True,
                            filepath=None,
                            filename=f"{os.path.basename(output_base)}.mp4",
                            filesize_bytes=raw_size or int(est_mb * 1024 * 1024),
                            filesize_mb=est_mb,
                            title=title,
                            uploader=uploader,
                            duration=duration,
                            thumbnail_path=None,
                            is_over_50mb=True,
                            is_audio=False,
                            direct_url=direct_url,
                        )

                    try:
                        ydl.download([url])
                    except Exception as dl_err:
                        if direct_url:
                            logger.warning(f"yt-dlp download failed ({dl_err}), falling back to direct stream URL")
                            return DownloadResult(
                                success=True,
                                filepath=None,
                                filename=f"{os.path.basename(output_base)}.mp4",
                                filesize_bytes=raw_size or 0,
                                filesize_mb=est_mb or 50.0,
                                title=title,
                                uploader=uploader,
                                duration=duration,
                                thumbnail_path=None,
                                is_over_50mb=True,
                                is_audio=False,
                                direct_url=direct_url,
                            )
                        raise

                    downloaded_video = None
                    for ext in ["mp4", "mkv", "webm", "mov", "ts"]:
                        candidate = f"{output_base}.{ext}"
                        if os.path.exists(candidate):
                            downloaded_video = candidate
                            break

                    if downloaded_video:
                        downloaded_video = ensure_ios_compatible_video_sync(downloaded_video)

                    if not downloaded_video:
                        if direct_url:
                            return DownloadResult(
                                success=True,
                                filepath=None,
                                filename=f"{os.path.basename(output_base)}.mp4",
                                filesize_bytes=raw_size or 0,
                                filesize_mb=est_mb or 50.0,
                                title=title,
                                uploader=uploader,
                                duration=duration,
                                thumbnail_path=None,
                                is_over_50mb=True,
                                is_audio=False,
                                direct_url=direct_url,
                            )
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

                    is_over_50mb = filesize > get_telegram_limit_bytes()
                    if is_over_50mb and not direct_url:
                        direct_url = extract_best_direct_url(info, is_audio=False)

                    if filesize_mb > max_size_mb and not direct_url:
                        cleanup_files(downloaded_video, thumbnail_file)
                        return DownloadResult(
                            success=False,
                            error=f"FILE_TOO_LARGE:{filesize_mb}:{max_size_mb}"
                        )

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
                        direct_url=direct_url,
                    )
            except Exception as e:
                if direct_url:
                    logger.warning(f"yt-dlp video error ({e}), gracefully recovering with direct_url")
                    return DownloadResult(
                        success=True,
                        filepath=None,
                        filename=f"{os.path.basename(output_base)}.mp4",
                        filesize_bytes=raw_size or 0,
                        filesize_mb=est_mb or 50.0,
                        title=title,
                        uploader=uploader,
                        duration=duration,
                        thumbnail_path=None,
                        is_over_50mb=True,
                        is_audio=False,
                        direct_url=direct_url,
                    )
                if use_cookies and cookie_available:
                    logger.warning(f"Video download with cookies failed ({e}), retrying without cookies...")
                    continue
                logger.error(f"yt-dlp video download failed for {url}: {e}")
                return DownloadResult(success=False, error=str(e))

        return DownloadResult(success=False, error="Download failed.")

    def execute_ytdlp_audio(self, url: str, output_base: str, max_size_mb: float, custom_opts: Optional[Dict[str, Any]] = None) -> Any:
        """Synchronous yt-dlp audio worker with automatic direct CDN stream extraction."""
        from bot.downloader import DownloadResult

        download_template = f"{output_base}.%(ext)s"
        cookie_available = get_cookie_file() is not None
        attempts = [True, False] if cookie_available else [False]

        for use_cookies in attempts:
            ydl_opts = self.build_audio_ydl_opts(download_template, use_cookies=use_cookies)
            if custom_opts:
                ydl_opts.update(custom_opts)

            direct_url = None
            title = "Audio"
            uploader = "Unknown Artist"
            duration = 0
            est_mb = None
            raw_size = None

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
                    direct_url = extract_best_direct_url(info, is_audio=True)

                    raw_size = info.get("filesize") or info.get("filesize_approx")
                    if not raw_size and info.get("requested_formats"):
                        raw_size = sum((f.get("filesize") or f.get("filesize_approx") or 0) for f in info["requested_formats"])

                    if raw_size:
                        est_mb = round(raw_size / (1024 * 1024), 2)
                        if est_mb > max_size_mb and not direct_url:
                            return DownloadResult(
                                success=False,
                                error=f"FILE_TOO_LARGE:{est_mb}:{max_size_mb}"
                            )

                    if direct_url and (est_mb is not None and est_mb >= config.telegram_direct_limit_mb):
                        return DownloadResult(
                            success=True,
                            filepath=None,
                            filename=f"{os.path.basename(output_base)}.mp3",
                            filesize_bytes=raw_size or int(est_mb * 1024 * 1024),
                            filesize_mb=est_mb,
                            title=title,
                            uploader=uploader,
                            duration=duration,
                            thumbnail_path=None,
                            is_over_50mb=True,
                            is_audio=True,
                            direct_url=direct_url,
                        )

                    try:
                        ydl.download([url])
                    except Exception as dl_err:
                        if direct_url:
                            logger.warning(f"yt-dlp audio download failed ({dl_err}), falling back to direct stream URL")
                            return DownloadResult(
                                success=True,
                                filepath=None,
                                filename=f"{os.path.basename(output_base)}.mp3",
                                filesize_bytes=raw_size or 0,
                                filesize_mb=est_mb or 50.0,
                                title=title,
                                uploader=uploader,
                                duration=duration,
                                thumbnail_path=None,
                                is_over_50mb=True,
                                is_audio=True,
                                direct_url=direct_url,
                            )
                        raise

                    downloaded_audio = None
                    for ext in ["mp3", "m4a", "aac", "ogg", "opus", "webm"]:
                        candidate = f"{output_base}.{ext}"
                        if os.path.exists(candidate):
                            downloaded_audio = candidate
                            break

                    if not downloaded_audio:
                        if direct_url:
                            return DownloadResult(
                                success=True,
                                filepath=None,
                                filename=f"{os.path.basename(output_base)}.mp3",
                                filesize_bytes=raw_size or 0,
                                filesize_mb=est_mb or 50.0,
                                title=title,
                                uploader=uploader,
                                duration=duration,
                                thumbnail_path=None,
                                is_over_50mb=True,
                                is_audio=True,
                                direct_url=direct_url,
                            )
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

                    is_over_50mb = filesize > get_telegram_limit_bytes()
                    if is_over_50mb and not direct_url:
                        direct_url = extract_best_direct_url(info, is_audio=True)

                    if filesize_mb > max_size_mb and not direct_url:
                        cleanup_files(downloaded_audio, thumbnail_file)
                        return DownloadResult(
                            success=False,
                            error=f"FILE_TOO_LARGE:{filesize_mb}:{max_size_mb}"
                        )

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
                        direct_url=direct_url,
                    )
            except Exception as e:
                if direct_url:
                    logger.warning(f"yt-dlp audio error ({e}), gracefully recovering with direct_url")
                    return DownloadResult(
                        success=True,
                        filepath=None,
                        filename=f"{os.path.basename(output_base)}.mp3",
                        filesize_bytes=raw_size or 0,
                        filesize_mb=est_mb or 50.0,
                        title=title,
                        uploader=uploader,
                        duration=duration,
                        thumbnail_path=None,
                        is_over_50mb=True,
                        is_audio=True,
                        direct_url=direct_url,
                    )
                if use_cookies and get_cookie_file():
                    logger.warning(f"Audio download with cookies failed ({e}), retrying without cookies...")
                    continue
                logger.error(f"yt-dlp audio download failed for {url}: {e}")
                return DownloadResult(success=False, error=str(e))

        return DownloadResult(success=False, error="Audio download failed.")


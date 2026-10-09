import os
import time
import html
import secrets
import logging
import asyncio
from typing import Optional, Dict, Any
from aiohttp import web
import aiohttp
from bot.config import config

logger = logging.getLogger(__name__)

# In-memory storage for active media stream tokens
# token -> { filepath, direct_url, headers, title, uploader, filesize_mb, is_audio, created_at }
_ACTIVE_STREAMS: Dict[str, Dict[str, Any]] = {}
_cached_ip = None

# Direct CDN stream URLs expire in 2 hours (7200 seconds)
STREAM_EXPIRY_SECONDS = 2 * 60 * 60


async def get_server_host() -> str:
    """Gets the server IP or custom domain for browser URLs."""
    global _cached_ip
    if getattr(config, "server_domain_or_ip", None):
        return config.server_domain_or_ip
    if _cached_ip:
        return _cached_ip
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.ipify.org", timeout=aiohttp.ClientTimeout(total=2.5)) as resp:
                if resp.status == 200:
                    _cached_ip = (await resp.text()).strip()
                    return _cached_ip
    except Exception:
        pass
    _cached_ip = "127.0.0.1"
    return _cached_ip


def register_stream(
    filepath: Optional[str] = None,
    direct_url: Optional[str] = None,
    title: str = "Video",
    uploader: str = "Creator",
    filesize_mb: float = 0.0,
    headers: Optional[Dict[str, str]] = None,
    is_audio: bool = False,
) -> str:
    """Registers a media stream and returns a secure token (strictly expires in 2 hours)."""
    now = time.time()
    expired = [t for t, data in _ACTIVE_STREAMS.items() if now - data.get("created_at", 0) > STREAM_EXPIRY_SECONDS]
    for t in expired:
        _ACTIVE_STREAMS.pop(t, None)

    token = secrets.token_urlsafe(16)
    _ACTIVE_STREAMS[token] = {
        "filepath": filepath,
        "direct_url": direct_url,
        "title": title or "Media",
        "uploader": uploader or "Creator",
        "filesize_mb": filesize_mb,
        "headers": headers or {},
        "is_audio": is_audio,
        "created_at": now,
    }
    return token


async def get_player_url(token: str) -> str:
    """Constructs the full public browser URL for the video player."""
    host = await get_server_host()
    port = getattr(config, "web_server_port", 8080)
    if port in (80, 443):
        return f"http://{host}/watch?v={token}"
    return f"http://{host}:{port}/watch?v={token}"


async def get_stream_url(token: str) -> str:
    """Constructs the direct CDN stream URL (opens directly in browser HTML5 player)."""
    host = await get_server_host()
    port = getattr(config, "web_server_port", 8080)
    if port in (80, 443):
        return f"http://{host}/stream?v={token}"
    return f"http://{host}:{port}/stream?v={token}"


async def get_download_url(token: str) -> str:
    """Constructs the direct attachment download URL (triggers browser file download)."""
    host = await get_server_host()
    port = getattr(config, "web_server_port", 8080)
    if port in (80, 443):
        return f"http://{host}/download?v={token}"
    return f"http://{host}:{port}/download?v={token}"


async def handle_watch(request: web.Request) -> web.Response:
    """Serves the modern, responsive HTML5 Video Player web page."""
    token = request.query.get("v", "").strip()
    data = _ACTIVE_STREAMS.get(token)
    if not data or (time.time() - data.get("created_at", 0) > STREAM_EXPIRY_SECONDS):
        if token in _ACTIVE_STREAMS:
            _ACTIVE_STREAMS.pop(token, None)
        expired_html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stream Expired - Godsdownload</title>
    <style>
        body { background: #0b0f19; color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; text-align: center; }
        .card { background: #1e293b; padding: 36px 28px; border-radius: 16px; border: 1px solid #334155; max-width: 440px; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5); }
        h2 { font-size: 1.5rem; margin-bottom: 12px; color: #f87171; }
        p { color: #94a3b8; font-size: 0.95rem; line-height: 1.6; margin-bottom: 24px; }
        .badge { display: inline-block; background: #334155; color: #38bdf8; padding: 8px 16px; border-radius: 8px; font-weight: 600; font-size: 0.9rem; }
    </style>
</head>
<body>
    <div class="card">
        <h2>⚠️ Video Stream Expired</h2>
        <p>This video link has expired or was cleaned up. Please send the link to <b>Godsdownload Bot</b> on Telegram again to generate a fresh high-speed stream.</p>
        <div class="badge">⚡ Godsdownload Bot</div>
    </div>
</body>
</html>"""
        return web.Response(text=expired_html, content_type="text/html", status=404)

    safe_title = html.escape(data.get("title", "Media"))
    safe_uploader = html.escape(data.get("uploader", "Creator"))
    size_mb = data.get("filesize_mb", 0.0)
    is_audio = data.get("is_audio", False)
    ext = "mp3" if is_audio else "mp4"
    type_label = "MP3 Audio" if is_audio else "MP4 Video"
    direct_url_val = data.get("direct_url") or ""
    direct_source = f'<source src="{html.escape(direct_url_val)}" type="video/mp4">' if direct_url_val and not is_audio else ""
    media_tag = (
        f'<audio id="player" controls playsinline preload="metadata" style="width: 100%; border-radius: 8px;">'
        f'<source id="media-src" src="/stream?v={token}" type="audio/mpeg">'
        f'Your browser does not support HTML5 audio.</audio>'
        if is_audio else
        f'<video id="player" controls playsinline preload="auto" style="width: 100%; max-height: 520px; outline: none; background: #000; border-radius: 12px;">'
        f'<source id="media-src" src="/stream?v={token}" type="video/mp4">'
        f'{direct_source}'
        f'Your browser does not support HTML5 video.</video>'
    )

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_title} - Godsdownload Safe Player</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: #0b0f19;
            color: #f1f5f9;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
            min-height: 100vh;
            padding: 16px;
        }}
        .container {{
            width: 100%;
            max-width: 860px;
            margin: auto;
        }}
        .header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 16px;
            padding: 8px 0;
            border-bottom: 1px solid #1e293b;
        }}
        .brand {{
            font-size: 1.25rem;
            font-weight: 700;
            color: #38bdf8;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .badge-safe {{
            background: rgba(34, 197, 94, 0.15);
            color: #4ade80;
            border: 1px solid #22c55e;
            padding: 5px 14px;
            border-radius: 9999px;
            font-size: 0.8rem;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}
        .player-card {{
            background: #1e293b;
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
            border: 1px solid #334155;
        }}
        .video-wrapper {{
            background: #000;
            width: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .info-card {{
            padding: 20px 24px;
        }}
        .title {{
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 10px;
            line-height: 1.4;
            color: #f8fafc;
        }}
        .meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
            color: #94a3b8;
            font-size: 0.875rem;
            margin-bottom: 22px;
        }}
        .meta-item {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .actions {{
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }}
        .btn {{
            flex: 1;
            min-width: 220px;
            padding: 14px 24px;
            border-radius: 12px;
            font-size: 1rem;
            font-weight: 600;
            text-align: center;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            border: none;
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        .btn-download {{
            background: linear-gradient(135deg, #0284c7, #2563eb);
            color: #ffffff;
            box-shadow: 0 4px 14px rgba(37, 99, 235, 0.4);
        }}
        .btn-download:hover {{
            background: linear-gradient(135deg, #0369a1, #1d4ed8);
            transform: translateY(-1px);
        }}
        .footer {{
            margin-top: 24px;
            text-align: center;
            color: #64748b;
            font-size: 0.825rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="brand">⚡ Godsdownload Safe Player</div>
            <div class="badge-safe">🔒 Verified Safe Media</div>
        </div>

        <div class="player-card">
            <div class="video-wrapper">
                {media_tag}
            </div>
            <div class="info-card">
                <h1 class="title">{safe_title}</h1>
                <div class="meta">
                    <div class="meta-item">👤 {safe_uploader}</div>
                    <div class="meta-item">📦 {size_mb} MB</div>
                    <div class="meta-item">⚡ High Quality {ext.upper()}</div>
                </div>
                <div class="actions">
                    <a href="/download?v={token}" class="btn btn-download" download>
                        📥 Download {type_label} ({size_mb} MB)
                    </a>
                </div>
            </div>
        </div>

        <div class="footer">
            Streamed safely via Godsdownload Bot • Direct high-speed CDN buffer
        </div>
    </div>

    <script>
        window.addEventListener('DOMContentLoaded', () => {{
            const p = document.getElementById('player');
            const mediaSrc = document.getElementById('media-src');
            const fallbackCdn = "{html.escape(direct_url_val)}";
            if (p) {{
                if (mediaSrc && fallbackCdn) {{
                    p.addEventListener('error', function() {{
                        if (mediaSrc.src !== fallbackCdn) {{
                            console.log('Primary stream proxy failed, switching to backup CDN stream...');
                            mediaSrc.src = fallbackCdn;
                            p.load();
                            p.play().catch(() => {{}});
                        }}
                    }}, true);
                }}
                p.play().catch(() => {{
                    // Autoplay prevented; ready for user tap
                }});
            }}
        }});
    </script>
</body>
</html>"""
    return web.Response(text=html_content, content_type="text/html")


async def _stream_media(request: web.Request, token: str, is_attachment: bool = False) -> web.StreamResponse:
    """Streams the media file from disk or proxies the remote CDN stream with HTTP 206 Range support."""
    data = _ACTIVE_STREAMS.get(token)
    if not data or (time.time() - data.get("created_at", 0) > STREAM_EXPIRY_SECONDS):
        if token in _ACTIVE_STREAMS:
            _ACTIVE_STREAMS.pop(token, None)
        return web.Response(text="Media stream expired or not found (Valid for 2 hours)", status=404)

    title = data.get("title", "media")
    is_audio = data.get("is_audio", False)
    ext = "mp3" if is_audio else "mp4"
    safe_name = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_")).strip() or "video"
    disposition = f'attachment; filename="{safe_name}.{ext}"' if is_attachment else None

    # Case 1: Local file exists on server disk
    filepath = data.get("filepath")
    if filepath and os.path.exists(filepath):
        headers = {}
        if disposition:
            headers["Content-Disposition"] = disposition
        return web.FileResponse(filepath, headers=headers)

    # Case 2: Proxy direct CDN stream with anti-hotlinking headers (Referer & User-Agent)
    direct_url = data.get("direct_url")
    if not direct_url:
        return web.Response(text="Media source unavailable", status=404)

    from urllib.parse import urlparse
    parsed = urlparse(direct_url)
    origin = f"{parsed.scheme}://{parsed.netloc}/" if parsed.netloc else "https://www.google.com/"

    custom_headers = data.get("headers", {})
    clean_direct = direct_url.lower()

    # Smart Anti-Hotlinking Referer Resolution (Bilibili, TikTok, Facebook, Instagram)
    if any(k in clean_direct for k in ["bilibili", "bilivideo", "akamaized.net"]):
        referer = "https://www.bilibili.com/"
    elif any(k in clean_direct for k in ["tiktok", "byteoversea"]):
        referer = "https://www.tiktok.com/"
    elif any(k in clean_direct for k in ["fbcdn", "facebook"]):
        referer = "https://www.facebook.com/"
    elif any(k in clean_direct for k in ["instagram", "cdninstagram"]):
        referer = "https://www.instagram.com/"
    else:
        referer = custom_headers.get("Referer") or origin

    ua = custom_headers.get("User-Agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    req_headers = {
        "User-Agent": ua,
        "Referer": referer,
        "Accept": "*/*",
    }
    if "Range" in request.headers:
        req_headers["Range"] = request.headers["Range"]

    session = aiohttp.ClientSession()
    try:
        resp = await session.get(direct_url, headers=req_headers, timeout=aiohttp.ClientTimeout(total=7200, sock_read=60), allow_redirects=True)
        # If CDN rejected with 400 or 403, retry with clean headers or alternative referer
        if resp.status not in (200, 206):
            await resp.release()
            retry_headers = {
                "User-Agent": req_headers["User-Agent"],
                "Accept": "*/*",
            }
            if any(k in clean_direct for k in ["bilibili", "bilivideo"]):
                retry_headers["Referer"] = "https://www.bilibili.com/"
            if "Range" in request.headers:
                retry_headers["Range"] = request.headers["Range"]
            resp = await session.get(direct_url, headers=retry_headers, timeout=aiohttp.ClientTimeout(total=7200, sock_read=60), allow_redirects=True)

        stream_resp = web.StreamResponse(status=resp.status)
        content_type = "audio/mpeg" if is_audio else "video/mp4"
        stream_resp.headers["Content-Type"] = resp.headers.get("Content-Type", content_type)
        stream_resp.headers["Accept-Ranges"] = "bytes"
        if disposition:
            stream_resp.headers["Content-Disposition"] = disposition
        if "Content-Range" in resp.headers:
            stream_resp.headers["Content-Range"] = resp.headers["Content-Range"]
        if "Content-Length" in resp.headers:
            stream_resp.headers["Content-Length"] = resp.headers["Content-Length"]

        await stream_resp.prepare(request)
        async for chunk in resp.content.iter_chunked(64 * 1024):
            await stream_resp.write(chunk)
        return stream_resp
    except (ConnectionResetError, aiohttp.ClientPayloadError):
        pass
    except Exception as e:
        logger.warning(f"Error while proxying stream for {token}: {e}")
    finally:
        await session.close()
    return web.Response(status=200)


async def handle_stream(request: web.Request) -> web.StreamResponse:
    """Streams the video/audio with HTTP 206 Range support."""
    token = request.query.get("v", "").strip()
    return await _stream_media(request, token, is_attachment=False)


async def handle_download(request: web.Request) -> web.StreamResponse:
    """Direct file download endpoint with Content-Disposition attachment."""
    token = request.query.get("v", "").strip()
    return await _stream_media(request, token, is_attachment=True)


async def _periodic_cleanup_task():
    """Background task to clean up expired stream tokens (2 hours) and temporary download files."""
    while True:
        await asyncio.sleep(300)  # Runs every 5 minutes
        try:
            now = time.time()
            expired = [t for t, data in _ACTIVE_STREAMS.items() if now - data.get("created_at", 0) > STREAM_EXPIRY_SECONDS]
            for t in expired:
                exp_data = _ACTIVE_STREAMS.pop(t, None)
                if exp_data and exp_data.get("filepath") and os.path.exists(exp_data["filepath"]):
                    try:
                        os.remove(exp_data["filepath"])
                    except Exception:
                        pass

            temp_dir = config.temp_dir
            if os.path.exists(temp_dir):
                for fname in os.listdir(temp_dir):
                    fpath = os.path.join(temp_dir, fname)
                    if os.path.isfile(fpath):
                        try:
                            if now - os.path.getmtime(fpath) > STREAM_EXPIRY_SECONDS:  # 2 hours
                                os.remove(fpath)
                                logger.debug(f"Removed old temp file: {fname}")
                        except Exception:
                            pass
        except Exception as e:
            logger.warning(f"Error in periodic cleanup task: {e}")


def create_web_player_app() -> web.Application:
    """Creates the aiohttp Web Application for the video player."""
    app = web.Application()
    app.router.add_get("/watch", handle_watch)
    app.router.add_get("/stream", handle_stream)
    app.router.add_get("/download", handle_download)
    return app


async def start_web_player(host: str = "0.0.0.0", port: int = 8080):
    """Starts the asynchronous web player background server and cleanup task."""
    app = create_web_player_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
        logger.info(f"🎬 Godsdownload Web Player is running on http://{host}:{port}/watch")
        asyncio.create_task(_periodic_cleanup_task())
    except Exception as e:
        logger.warning(f"Could not start Web Player server on port {port}: {e}")

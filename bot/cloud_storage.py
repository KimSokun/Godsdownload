import os
import logging
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)

LITTERBOX_API_URL = "https://litterbox.catbox.moe/resources/internals/api.php"
GOFILE_SERVERS_URL = "https://api.gofile.io/servers"


async def upload_to_litterbox(filepath: str, duration: str = "24h", timeout_seconds: int = 300) -> Optional[str]:
    """
    Uploads a file to Catbox Litterbox temporary hosting (up to 1 GB).
    Returns direct MP4 download link (e.g. https://litter.catbox.moe/xxxxxx.mp4).
    """
    if not os.path.exists(filepath):
        logger.error(f"File not found for Litterbox upload: {filepath}")
        return None

    filename = os.path.basename(filepath)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }

    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        data = aiohttp.FormData()
        data.add_field("reqtype", "fileupload")
        data.add_field("time", duration)

        with open(filepath, "rb") as f:
            data.add_field("fileToUpload", f, filename=filename)
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.post(LITTERBOX_API_URL, data=data) as resp:
                    if resp.status == 200:
                        link = (await resp.text()).strip()
                        if link.startswith("https://"):
                            logger.info(f"✅ Catbox Litterbox upload succeeded: {link}")
                            return link
                        logger.warning(f"Litterbox response is not a valid URL: {link}")
                    else:
                        logger.warning(f"Litterbox HTTP error {resp.status}: {await resp.text()}")
    except Exception as e:
        logger.warning(f"Litterbox upload failed: {e}")

    return None


async def upload_to_gofile(filepath: str, timeout_seconds: int = 300) -> Optional[str]:
    """
    Uploads a file to Gofile (supports unlimited file size).
    Returns downloadPage link (e.g. https://gofile.io/d/xxxxxx).
    """
    if not os.path.exists(filepath):
        logger.error(f"File not found for Gofile upload: {filepath}")
        return None

    filename = os.path.basename(filepath)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            # 1. Get best server
            async with session.get(GOFILE_SERVERS_URL) as s_resp:
                if s_resp.status != 200:
                    return None
                s_data = await s_resp.json()
                servers = s_data.get("data", {}).get("servers", [])
                if not servers:
                    return None
                server_name = servers[0].get("name")
                if not server_name:
                    return None

            upload_url = f"https://{server_name}.gofile.io/contents/uploadfile"

            # 2. Upload file
            data = aiohttp.FormData()
            with open(filepath, "rb") as f:
                data.add_field("file", f, filename=filename)
                async with session.post(upload_url, data=data) as up_resp:
                    if up_resp.status == 200:
                        up_json = await up_resp.json()
                        dl_page = up_json.get("data", {}).get("downloadPage")
                        if dl_page and dl_page.startswith("https://"):
                            logger.info(f"✅ Gofile upload succeeded: {dl_page}")
                            return dl_page
                        logger.warning(f"Gofile response missing downloadPage: {up_json}")
                    else:
                        logger.warning(f"Gofile HTTP error {up_resp.status}")
    except Exception as e:
        logger.warning(f"Gofile upload failed: {e}")

    return None


async def upload_large_file(filepath: str) -> Optional[str]:
    """
    Smart multi-cloud uploader for media files > 50 MB:
    1. Primary: Catbox Litterbox API (up to 1 GB, direct MP4 link, valid 24h).
    2. Fallback: Gofile API (unlimited file size).
    """
    if not filepath or not os.path.exists(filepath):
        return None

    file_size_bytes = os.path.getsize(filepath)
    file_size_mb = file_size_bytes / (1024 * 1024)

    # 1. For files under 1000 MB: Litterbox is primary (provides direct MP4 file link)
    if file_size_mb <= 1000.0:
        logger.info(f"Uploading large file ({file_size_mb:.1f} MB) to Catbox Litterbox...")
        link = await upload_to_litterbox(filepath, duration="24h")
        if link:
            return link
        logger.warning("Catbox Litterbox failed, falling back to Gofile...")

    # 2. For files > 1000 MB or if Litterbox failed: Gofile
    logger.info(f"Uploading large file ({file_size_mb:.1f} MB) to Gofile...")
    link = await upload_to_gofile(filepath)
    if link:
        return link

    logger.error(f"All cloud uploads failed for {filepath}")
    return None

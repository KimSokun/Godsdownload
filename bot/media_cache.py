import os
import time
import json
import hashlib
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_FILE = os.path.join(CACHE_DIR, "media_cache.json")
# Default cache retention: 30 days
CACHE_TTL_SECONDS = 30 * 24 * 3600


class MediaCache:
    """
    High-speed Telegram file_id Cache.
    Maps canonical URL + media_type -> Telegram file_id and metadata.
    Enables instant (<0.2s) duplicate delivery with zero server bandwidth & zero CPU load.
    """

    def __init__(self, cache_file: str = CACHE_FILE):
        self.cache_file = cache_file
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _hash_key(self, url: str, media_type: str) -> str:
        """Generates a stable normalized SHA256 key from URL and media_type."""
        clean_url = url.strip()
        key_raw = f"{media_type.lower()}:{clean_url}"
        return hashlib.sha256(key_raw.encode("utf-8")).hexdigest()

    def _load(self):
        """Loads cached entries from disk."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
                logger.info(f"Loaded {len(self._cache)} cached media file_ids.")
            except Exception as e:
                logger.warning(f"Could not load media cache: {e}. Starting fresh.")
                self._cache = {}
        else:
            self._cache = {}

    def _save(self):
        """Persists cache to disk."""
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Could not save media cache: {e}")

    def get(self, url: str, media_type: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves cached media metadata and Telegram file_id if available and not expired.
        """
        if not url:
            return None
        key = self._hash_key(url, media_type)
        entry = self._cache.get(key)
        if not entry:
            return None

        # Check expiration
        now = time.time()
        created_at = entry.get("timestamp", 0)
        if (now - created_at) > CACHE_TTL_SECONDS:
            self._cache.pop(key, None)
            self._save()
            return None

        return entry

    def set(
        self,
        url: str,
        media_type: str,
        file_id: str,
        title: str = "Media",
        uploader: str = "Creator",
        duration: int = 0,
        filesize_mb: float = 0.0,
    ) -> None:
        """
        Caches a successfully delivered Telegram file_id.
        """
        if not url or not file_id:
            return
        key = self._hash_key(url, media_type)
        self._cache[key] = {
            "file_id": file_id,
            "url": url,
            "media_type": media_type,
            "title": title or "Media",
            "uploader": uploader or "Creator",
            "duration": duration or 0,
            "filesize_mb": filesize_mb or 0.0,
            "timestamp": time.time(),
        }
        self._save()
        logger.info(f"Cached media file_id for {media_type} ({title[:30]}) -> {file_id[:16]}...")

    def clean_expired(self) -> int:
        """Removes expired cache items."""
        now = time.time()
        expired_keys = [
            k for k, v in self._cache.items()
            if (now - v.get("timestamp", 0)) > CACHE_TTL_SECONDS
        ]
        for k in expired_keys:
            self._cache.pop(k, None)
        if expired_keys:
            self._save()
            logger.info(f"Cleaned {len(expired_keys)} expired cache entries.")
        return len(expired_keys)

    def get_stats(self) -> Dict[str, Any]:
        """Returns statistics about the cache."""
        return {
            "total_cached": len(self._cache),
            "cache_file": self.cache_file,
            "ttl_days": CACHE_TTL_SECONDS // (24 * 3600),
        }


# Global singleton instance
media_cache = MediaCache()

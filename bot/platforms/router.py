import logging
from typing import List, Optional

from bot.platforms.base import BasePlatform
from bot.platforms.youtube import YouTubePlatform
from bot.platforms.tiktok import TikTokPlatform
from bot.platforms.douyin import DouyinPlatform
from bot.platforms.facebook import FacebookPlatform
from bot.platforms.bilibili import BilibiliPlatform
from bot.platforms.instagram import InstagramPlatform
from bot.platforms.threads import ThreadsPlatform
from bot.platforms.twitter import TwitterPlatform
from bot.platforms.reddit import RedditPlatform
from bot.platforms.pinterest import PinterestPlatform
from bot.platforms.generic import GenericPlatform

logger = logging.getLogger(__name__)

# Registered platform handlers in precedence order (Only working platforms that can download video & images)
_PLATFORMS: List[BasePlatform] = [
    YouTubePlatform(),
    TikTokPlatform(),
    DouyinPlatform(),
    FacebookPlatform(),
    BilibiliPlatform(),
    InstagramPlatform(),
    ThreadsPlatform(),
    TwitterPlatform(),
    RedditPlatform(),
    PinterestPlatform(),
]

_GENERIC_FALLBACK: BasePlatform = GenericPlatform()


def detect_platform(url: str) -> BasePlatform:
    """
    Auto-detects the matching platform handler for a given URL.
    Returns the matching platform instance or GenericPlatform fallback.
    """
    if not url:
        return _GENERIC_FALLBACK

    clean_url = url.strip()
    for platform in _PLATFORMS:
        if platform.can_handle(clean_url):
            logger.debug(f"Detected platform {platform.platform_id} for URL: {url}")
            return platform

    logger.debug(f"Using generic fallback platform for URL: {url}")
    return _GENERIC_FALLBACK


def get_platform_name(url: str) -> str:
    """Returns the display name of the platform detected for this URL."""
    return detect_platform(url).display_name


def get_all_platforms() -> List[BasePlatform]:
    """Returns all registered platform instances including the generic fallback."""
    return list(_PLATFORMS) + [_GENERIC_FALLBACK]

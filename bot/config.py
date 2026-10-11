import os
import logging
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("Godsdownload")


def _parse_admin_configs():
    raw_entries = []
    for var in ["ADMIN_USERNAMES", "ADMIN_USERNAME", "ADMIN_IDS", "ADMINS"]:
        val = os.getenv(var, "").strip()
        if val:
            raw_entries.extend([x.strip() for x in val.split(",") if x.strip()])
    
    admin_ids = []
    admin_usernames = []
    for entry in raw_entries:
        clean = entry.lstrip("@").strip().lower()
        if clean.isdigit():
            admin_ids.append(int(clean))
        elif clean:
            admin_usernames.append(clean)
    return admin_ids, admin_usernames


@dataclass
class Config:
    bot_token: str = os.getenv("BOT_TOKEN", "").strip()
    admin_ids: List[int] = field(default_factory=lambda: _parse_admin_configs()[0])
    admin_usernames: List[str] = field(default_factory=lambda: _parse_admin_configs()[1])
    usdt_tron_address: str = os.getenv("USDT_TRON_ADDRESS", "YOUR_USDT_TRON_ADDRESS").strip()
    vip_price_usd: float = float(os.getenv("VIP_PRICE_USD", "2.0"))
    free_download_limit: int = int(os.getenv("FREE_DOWNLOAD_LIMIT", "10"))
    
    # Telegram Local Bot API Server settings (Allows up to 2000 MB / 1.5 GB direct uploads)
    telegram_api_url: str = os.getenv("TELEGRAM_API_URL", "").strip()
    telegram_file_url: str = os.getenv("TELEGRAM_FILE_URL", "").strip()

    # Max file size limit (Up to 2000 MB supported via Web Player)
    max_file_size_mb: float = float(os.getenv("MAX_FILE_SIZE_MB", "2000.0"))
    telegram_direct_limit_mb: float = float(os.getenv("TELEGRAM_DIRECT_LIMIT_MB", "50.0"))
    cookies_file: str = os.getenv("COOKIES_FILE", "cookies.txt").strip()
    douyin_cookie: str = os.getenv("DOUYIN_COOKIE", "").strip()
    douyin_api_url: str = os.getenv("DOUYIN_API_URL", "").strip()
    cobalt_api_url: str = os.getenv("COBALT_API_URL", "https://api.cobalt.tools").strip()
    proxy_url: str = os.getenv("PROXY_URL", "").strip()
    youtube_proxy: str = os.getenv("YOUTUBE_PROXY", "").strip()

    # Built-in Web Video Player settings (For safe in-browser streaming & downloads)
    web_server_port: int = int(os.getenv("WEB_SERVER_PORT", "8080"))
    server_domain_or_ip: str = os.getenv("SERVER_DOMAIN_OR_IP", "").strip()

    # Auto-updater
    auto_update_interval_hours: int = int(os.getenv("AUTO_UPDATE_INTERVAL_HOURS", "6"))
    
    # Owner configuration (exclusive control for /setvip)
    owner_username: str = os.getenv("OWNER_USERNAME", "skun20000").strip().lstrip("@").lower()
    owner_id: Optional[int] = int(os.getenv("OWNER_ID")) if os.getenv("OWNER_ID", "").strip().isdigit() else None

    # Directory paths (temporary storage only - cleaned immediately after download/send)
    temp_dir: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp_downloads")

    def is_owner(self, user_id: int, username: str = None) -> bool:
        """
        Checks if a user is the Bot Owner (@skun20000 or matching OWNER_ID / OWNER_USERNAME).
        Owner has exclusive permissions, including the /setvip command.
        """
        if username:
            clean_u = username.lstrip("@").strip().lower()
            if clean_u and clean_u == self.owner_username:
                if self.owner_id is None:
                    self.owner_id = user_id
                if user_id not in self.admin_ids:
                    self.admin_ids.append(user_id)
                return True
            if clean_u:
                return False

        if self.owner_id and user_id == self.owner_id:
            return True

        # Check saved username in user tracker if available
        try:
            from bot.tracker import tracker
            uid_str = str(user_id)
            if uid_str in tracker.users:
                saved_u = tracker.users[uid_str].get("username", "").lstrip("@").strip().lower()
                if saved_u and saved_u == self.owner_username:
                    if self.owner_id is None:
                        self.owner_id = user_id
                    if user_id not in self.admin_ids:
                        self.admin_ids.append(user_id)
                    return True
        except Exception:
            pass

        return False

    def is_admin(self, user_id: int, username: str = None) -> bool:
        """
        Checks if a user is an administrator by numeric user_id or Telegram username.
        Supports usernames with or without '@', case-insensitively.
        Owner is always an administrator.
        """
        if self.is_owner(user_id, username):
            return True

        if user_id in self.admin_ids:
            return True

        if username:
            clean_u = username.lstrip("@").strip().lower()
            if clean_u and clean_u in self.admin_usernames:
                if user_id not in self.admin_ids:
                    self.admin_ids.append(user_id)
                return True

        # Check saved username in user tracker if available
        try:
            from bot.tracker import tracker
            uid_str = str(user_id)
            if uid_str in tracker.users:
                saved_u = tracker.users[uid_str].get("username", "").lstrip("@").strip().lower()
                if saved_u and saved_u in self.admin_usernames:
                    if user_id not in self.admin_ids:
                        self.admin_ids.append(user_id)
                    return True
        except Exception:
            pass

        return False

    @property
    def primary_admin_username(self) -> str:
        """Returns the primary admin username for support links (default: owner skun20000)."""
        custom = os.getenv("ADMIN_CONTACT_USERNAME", "").strip().lstrip("@")
        if custom:
            return custom
        if self.owner_username:
            return self.owner_username
        if self.admin_usernames:
            return self.admin_usernames[0]
        return "skun20000"

    @property
    def admin_contact_url(self) -> str:
        """Constructs direct Telegram chat link to admin without exposing raw username."""
        return f"https://t.me/{self.primary_admin_username}"

    def get_admin_chat_ids(self) -> List[int]:
        """Returns all resolved numeric chat IDs of administrators and owner for notifications."""
        ids = set(self.admin_ids)
        if self.owner_id:
            ids.add(self.owner_id)
        try:
            from bot.tracker import tracker
            all_usernames = set(self.admin_usernames)
            if self.owner_username:
                all_usernames.add(self.owner_username)
            for uid_str, udata in tracker.users.items():
                u_name = udata.get("username", "").lstrip("@").strip().lower()
                if u_name and u_name in all_usernames:
                    if uid_str.isdigit():
                        ids.add(int(uid_str))
        except Exception:
            pass
        return list(ids)


config = Config()

# Ensure temp directory exists
os.makedirs(config.temp_dir, exist_ok=True)

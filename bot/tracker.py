import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, List, Optional
from bot.config import config

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DATA_FILE = os.path.join(DATA_DIR, "users.json")
MEMBER_JSON_FILE = os.path.join(DATA_DIR, "member.json")
MEMBER_JSONL_FILE = os.path.join(DATA_DIR, "member.jsonl")
ROOT_MEMBER_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "member.json")
ROOT_MEMBER_JSONL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "member.jsonl")
ROOT_MEMBER_TXT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "member.txt")
ROOT_MEMBER_PNG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "member.png")
MEMBER_PNG_FILE = os.path.join(DATA_DIR, "member.png")
REPORTS_FILE = os.path.join(DATA_DIR, "reports.json")
ROOT_REPORTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports.json")


def get_today_str() -> str:
    """Returns today's date string in YYYY-MM-DD (UTC)."""
    return datetime.utcnow().strftime("%Y-%m-%d")


class UserTracker:
    """
    Lightweight User & Daily Limit Tracker.
    No database required! Keeps data in-memory and auto-syncs to users.json,
    member.json, member.jsonl, and member.txt.
    Free tier allows 10 downloads per day (resets daily at 00:00 UTC).
    """

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.users: Dict[str, Dict[str, Any]] = {}
        self.reports: List[Dict[str, Any]] = []
        self._load()
        self._load_reports()
        try:
            self.export_member_data()
        except Exception as e:
            logger.warning(f"Could not initialize member export files: {e}")

    def _load(self):
        """Loads user state from JSON file if exists."""
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    self.users = json.load(f)
                logger.info(f"Loaded {len(self.users)} users into memory.")
            except Exception as e:
                logger.error(f"Error reading {DATA_FILE}: {e}. Starting fresh in memory.")
                self.users = {}
        else:
            self.users = {}

    def _save(self):
        """Saves current state to JSON file and automatically syncs member.json and member.jsonl."""
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self.users, f, indent=2, ensure_ascii=False)
            self.export_member_data()
        except Exception as e:
            logger.error(f"Failed to persist user state: {e}")

    def export_member_data(self) -> Dict[str, Any]:
        """
        Exports all members broken down into Normal Users and Subscription (VIP) Users.
        Writes to data/member.json, data/member.jsonl, member.json, member.jsonl, and member.txt.
        """
        now = datetime.utcnow()
        normal_users = []
        subscription_users = []

        for uid_str, u in self.users.items():
            user_id = u.get("user_id", int(uid_str) if str(uid_str).isdigit() else uid_str)
            raw_username = u.get("username", "") or ""
            username = f"@{raw_username.lstrip('@')}" if raw_username else ""
            first_name = u.get("first_name", "") or "User"
            total_dl = u.get("total_downloads", u.get("download_count", 0))
            daily_dl = u.get("daily_downloads", 0)
            joined_at = u.get("created_at", "")
            last_active = u.get("last_active", "")
            is_vip = u.get("is_vip", False)
            vip_until_str = u.get("vip_until")

            if is_vip:
                days_left = 30
                if vip_until_str:
                    try:
                        vip_dt = datetime.fromisoformat(vip_until_str)
                        if vip_dt > now:
                            days_left = max(1, (vip_dt - now).days)
                        else:
                            days_left = 0
                    except Exception:
                        pass

                subscription_users.append({
                    "id": user_id,
                    "username": username,
                    "first_name": first_name,
                    "status": "VIP",
                    "vip_until": vip_until_str,
                    "days_left": days_left,
                    "total_downloads": total_dl,
                    "daily_downloads": daily_dl,
                    "joined_at": joined_at,
                    "last_active": last_active,
                })
            else:
                normal_users.append({
                    "id": user_id,
                    "username": username,
                    "first_name": first_name,
                    "status": "FREE",
                    "daily_limit": config.free_download_limit,
                    "total_downloads": total_dl,
                    "daily_downloads": daily_dl,
                    "joined_at": joined_at,
                    "last_active": last_active,
                })

        # Sort subscription users by days_left descending, normal users by total_downloads descending
        subscription_users.sort(key=lambda x: x.get("days_left", 0), reverse=True)
        normal_users.sort(key=lambda x: x.get("total_downloads", 0), reverse=True)

        total_members = len(self.users)
        total_downloads = sum(u.get("total_downloads", u.get("download_count", 0)) for u in self.users.values())

        report_data = {
            "generated_at": now.isoformat() + "Z",
            "summary": {
                "total_members": total_members,
                "total_normal_users": len(normal_users),
                "total_subscription_users": len(subscription_users),
                "total_downloads": total_downloads,
            },
            "subscription_users": subscription_users,
            "normal_users": normal_users,
        }

        # 1. Write member.json
        json_content = json.dumps(report_data, indent=2, ensure_ascii=False)
        for p in [MEMBER_JSON_FILE, ROOT_MEMBER_JSON]:
            try:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(json_content)
            except Exception as e:
                logger.warning(f"Could not write {p}: {e}")

        # 2. Write member.jsonl (JSON Lines format)
        jsonl_lines = [
            json.dumps({"type": "summary", **report_data["summary"], "generated_at": report_data["generated_at"]}, ensure_ascii=False)
        ]
        for sub in subscription_users:
            jsonl_lines.append(json.dumps({"type": "subscription_user", **sub}, ensure_ascii=False))
        for norm in normal_users:
            jsonl_lines.append(json.dumps({"type": "normal_user", **norm}, ensure_ascii=False))

        jsonl_content = "\n".join(jsonl_lines) + "\n"
        for p in [MEMBER_JSONL_FILE, ROOT_MEMBER_JSONL]:
            try:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(jsonl_content)
            except Exception as e:
                logger.warning(f"Could not write {p}: {e}")

        # 3. Write member.txt
        txt_lines = [
            "# ==================================================================",
            "# Godsdownload Bot - Registered Members & VIP List",
            f"# Generated: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"# Total Members: {total_members} | VIP Subscribers: {len(subscription_users)} | Free Members: {len(normal_users)}",
            "# ==================================================================",
            "",
            "[👑 VIP SUBSCRIPTION USERS]",
            "# ID           | USERNAME           | NAME             | DAYS LEFT | VIP UNTIL  | DOWNLOADS",
            "# ---------------------------------------------------------------------------------------",
        ]
        if not subscription_users:
            txt_lines.append("# No active VIP subscribers.")
        else:
            for s in subscription_users:
                exp_date = (s.get("vip_until") or "").split("T")[0]
                txt_lines.append(f"{str(s['id']):<12} | {s['username']:<18} | {s['first_name']:<16} | {str(s['days_left']) + ' days':<9} | {exp_date:<10} | {s['total_downloads']}")

        txt_lines.extend([
            "",
            "[🆓 NORMAL USERS]",
            "# ID           | USERNAME           | NAME             | TODAY / LIMIT | TOTAL DOWNLOADS | JOINED",
            "# ----------------------------------------------------------------------------------------------",
        ])
        if not normal_users:
            txt_lines.append("# No normal users yet.")
        else:
            for n in normal_users:
                joined_date = (n.get("joined_at") or "").split("T")[0]
                limit_str = f"{n['daily_downloads']}/{n['daily_limit']}"
                txt_lines.append(f"{str(n['id']):<12} | {n['username']:<18} | {n['first_name']:<16} | {limit_str:<13} | {str(n['total_downloads']):<15} | {joined_date}")

        txt_content = "\n".join(txt_lines) + "\n"
        try:
            with open(ROOT_MEMBER_TXT, "w", encoding="utf-8") as f:
                f.write(txt_content)
        except Exception as e:
            logger.warning(f"Could not write {ROOT_MEMBER_TXT}: {e}")

        # 4. Generate member.png dashboard image
        try:
            self.export_member_png(report_data)
        except Exception as e:
            logger.warning(f"Could not generate member PNG: {e}")

        return report_data

    def export_member_png(self, report_data: Optional[Dict[str, Any]] = None) -> str:
        """
        Renders a high-resolution dark-themed visual analytics dashboard of members in PNG format.
        Saves to member.png and data/member.png.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            logger.warning("Pillow not installed; skipping member PNG generation.")
            return ROOT_MEMBER_PNG

        if report_data is None:
            report_data = self.export_member_data()

        summary = report_data.get("summary", {})
        sub_users = report_data.get("subscription_users", [])
        norm_users = report_data.get("normal_users", [])

        def _font(size: int = 15, bold: bool = False):
            font_names = ["DejaVuSans-Bold.ttf", "arialbd.ttf"] if bold else ["DejaVuSans.ttf", "arial.ttf"]
            for fn in font_names:
                try:
                    return ImageFont.truetype(fn, size=size)
                except Exception:
                    pass
            try:
                return ImageFont.load_default(size=size)
            except Exception:
                return ImageFont.load_default()

        # Dynamic height calculation
        num_sub = min(len(sub_users), 12)
        num_norm = min(len(norm_users), 8)
        header_h = 90
        cards_h = 110
        sub_table_h = 60 + max(num_sub, 1) * 32
        norm_table_h = 60 + max(num_norm, 1) * 32
        footer_h = 50
        total_height = header_h + cards_h + sub_table_h + norm_table_h + footer_h + 60
        width = 1050

        img = Image.new("RGB", (width, total_height), color=(15, 20, 28))
        draw = ImageDraw.Draw(img)

        # 1. Header Banner
        draw.rectangle([(0, 0), (width, header_h)], fill=(22, 30, 42))
        draw.text((40, 22), "👑 GODSDOWNLOAD - MEMBER DIRECTORY & ANALYTICS", font=_font(22, True), fill=(255, 255, 255))
        now_str = report_data.get("generated_at", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))
        draw.text((40, 56), f"Generated: {now_str} • Exclusive Bot Owner Confidential Report", font=_font(13), fill=(148, 163, 184))

        # 2. Four Stat Cards
        card_w = 220
        card_h = 82
        card_y = header_h + 20
        cards = [
            ("TOTAL MEMBERS", str(summary.get("total_members", 0)), (56, 189, 248)),
            ("VIP SUBSCRIBERS", str(summary.get("total_subscription_users", 0)), (251, 191, 36)),
            ("NORMAL USERS", str(summary.get("total_normal_users", 0)), (52, 211, 153)),
            ("TOTAL DOWNLOADS", str(summary.get("total_downloads", 0)), (129, 140, 248)),
        ]
        for i, (title, val, col) in enumerate(cards):
            x = 40 + i * (card_w + 30)
            draw.rounded_rectangle([(x, card_y), (x + card_w, card_y + card_h)], radius=10, fill=(26, 34, 48), outline=(45, 55, 72), width=1)
            draw.text((x + 16, card_y + 14), title, font=_font(11, True), fill=(148, 163, 184))
            draw.text((x + 16, card_y + 36), val, font=_font(25, True), fill=col)

        # 3. VIP Subscription Users Section
        cur_y = card_y + card_h + 25
        draw.text((40, cur_y), "💎 VIP SUBSCRIPTION MEMBERS", font=_font(16, True), fill=(251, 191, 36))
        cur_y += 30

        # VIP Table Header
        draw.rounded_rectangle([(40, cur_y), (width - 40, cur_y + 32)], radius=6, fill=(30, 41, 59))
        draw.text((55, cur_y + 8), "USER ID", font=_font(12, True), fill=(148, 163, 184))
        draw.text((190, cur_y + 8), "USERNAME", font=_font(12, True), fill=(148, 163, 184))
        draw.text((370, cur_y + 8), "NAME", font=_font(12, True), fill=(148, 163, 184))
        draw.text((540, cur_y + 8), "DAYS LEFT", font=_font(12, True), fill=(148, 163, 184))
        draw.text((680, cur_y + 8), "VIP UNTIL", font=_font(12, True), fill=(148, 163, 184))
        draw.text((840, cur_y + 8), "DOWNLOADS", font=_font(12, True), fill=(148, 163, 184))
        cur_y += 34

        if not sub_users:
            draw.text((55, cur_y + 8), "No active VIP subscribers registered yet.", font=_font(13), fill=(100, 116, 139))
            cur_y += 32
        else:
            for idx, s in enumerate(sub_users[:12]):
                row_bg = (22, 30, 42) if idx % 2 == 0 else (18, 25, 36)
                draw.rectangle([(40, cur_y), (width - 40, cur_y + 30)], fill=row_bg)
                exp = (s.get("vip_until") or "").split("T")[0]
                draw.text((55, cur_y + 7), str(s.get("id")), font=_font(12), fill=(226, 232, 240))
                draw.text((190, cur_y + 7), str(s.get("username", ""))[:18], font=_font(12, True), fill=(56, 189, 248))
                draw.text((370, cur_y + 7), str(s.get("first_name", ""))[:16], font=_font(12), fill=(203, 213, 225))
                draw.text((540, cur_y + 7), f"{s.get('days_left', 0)} days", font=_font(12, True), fill=(251, 191, 36))
                draw.text((680, cur_y + 7), exp, font=_font(12), fill=(148, 163, 184))
                draw.text((840, cur_y + 7), str(s.get("total_downloads", 0)), font=_font(12, True), fill=(52, 211, 153))
                cur_y += 32
            if len(sub_users) > 12:
                draw.text((55, cur_y + 6), f"+ {len(sub_users) - 12} more VIP subscribers (view full list in member.json)", font=_font(11), fill=(100, 116, 139))
                cur_y += 24

        # 4. Normal Users Section
        cur_y += 20
        draw.text((40, cur_y), "🆓 NORMAL MEMBERS (FREE TIER)", font=_font(16, True), fill=(52, 211, 153))
        cur_y += 30

        # Normal Table Header
        draw.rounded_rectangle([(40, cur_y), (width - 40, cur_y + 32)], radius=6, fill=(30, 41, 59))
        draw.text((55, cur_y + 8), "USER ID", font=_font(12, True), fill=(148, 163, 184))
        draw.text((190, cur_y + 8), "USERNAME", font=_font(12, True), fill=(148, 163, 184))
        draw.text((370, cur_y + 8), "NAME", font=_font(12, True), fill=(148, 163, 184))
        draw.text((540, cur_y + 8), "TODAY / LIMIT", font=_font(12, True), fill=(148, 163, 184))
        draw.text((680, cur_y + 8), "DOWNLOADS", font=_font(12, True), fill=(148, 163, 184))
        draw.text((840, cur_y + 8), "JOINED", font=_font(12, True), fill=(148, 163, 184))
        cur_y += 34

        if not norm_users:
            draw.text((55, cur_y + 8), "No normal users registered yet.", font=_font(13), fill=(100, 116, 139))
            cur_y += 32
        else:
            for idx, n in enumerate(norm_users[:8]):
                row_bg = (22, 30, 42) if idx % 2 == 0 else (18, 25, 36)
                draw.rectangle([(40, cur_y), (width - 40, cur_y + 30)], fill=row_bg)
                joined = (n.get("joined_at") or "").split("T")[0]
                limit_str = f"{n.get('daily_downloads', 0)} / {n.get('daily_limit', 10)}"
                draw.text((55, cur_y + 7), str(n.get("id")), font=_font(12), fill=(226, 232, 240))
                draw.text((190, cur_y + 7), str(n.get("username", ""))[:18], font=_font(12), fill=(148, 163, 184))
                draw.text((370, cur_y + 7), str(n.get("first_name", ""))[:16], font=_font(12), fill=(203, 213, 225))
                draw.text((540, cur_y + 7), limit_str, font=_font(12), fill=(203, 213, 225))
                draw.text((680, cur_y + 7), str(n.get("total_downloads", 0)), font=_font(12, True), fill=(129, 140, 248))
                draw.text((840, cur_y + 7), joined, font=_font(12), fill=(100, 116, 139))
                cur_y += 32
            if len(norm_users) > 8:
                draw.text((55, cur_y + 6), f"+ {len(norm_users) - 8} more normal users (view full list in member.json)", font=_font(11), fill=(100, 116, 139))
                cur_y += 24

        # Footer
        cur_y += 20
        draw.line([(40, cur_y), (width - 40, cur_y)], fill=(45, 55, 72), width=1)
        draw.text((40, cur_y + 12), "🔒 Exclusive Bot Owner Report • Godsdownload High-Speed Downloader", font=_font(11), fill=(100, 116, 139))

        # Save to member.png and data/member.png
        for path in [ROOT_MEMBER_PNG, MEMBER_PNG_FILE]:
            try:
                img.save(path, "PNG")
            except Exception as e:
                logger.warning(f"Could not save member PNG to {path}: {e}")

        return ROOT_MEMBER_PNG

    def get_or_create_user(self, user_id: int, username: str = "", first_name: str = "") -> Dict[str, Any]:
        uid = str(user_id)
        today = get_today_str()

        # Auto-register owner ID if username matches owner_username
        if username:
            clean_u = username.lstrip("@").strip().lower()
            if clean_u and clean_u == config.owner_username:
                if config.owner_id is None:
                    config.owner_id = user_id
                if user_id not in config.admin_ids:
                    config.admin_ids.append(user_id)

        if uid not in self.users:
            self.users[uid] = {
                "user_id": user_id,
                "username": username or "",
                "first_name": first_name or "",
                "total_downloads": 0,
                "daily_downloads": 0,
                "last_download_date": today,
                "is_vip": False,
                "submitted_txs": [],
                "created_at": datetime.utcnow().isoformat(),
                "last_active": datetime.utcnow().isoformat(),
            }
            self._save()
        else:
            # Check and reset daily downloads if new day
            if self.users[uid].get("last_download_date") != today:
                self.users[uid]["daily_downloads"] = 0
                self.users[uid]["last_download_date"] = today

            # Backwards compatibility
            if "total_downloads" not in self.users[uid]:
                self.users[uid]["total_downloads"] = self.users[uid].get("download_count", 0)
            if "daily_downloads" not in self.users[uid]:
                self.users[uid]["daily_downloads"] = self.users[uid].get("download_count", 0)

            # Update meta
            if username:
                self.users[uid]["username"] = username
            if first_name:
                self.users[uid]["first_name"] = first_name
            self.users[uid]["last_active"] = datetime.utcnow().isoformat()
            self._save()

        return self.users[uid]

    def find_user(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Finds a user by numeric user_id (int or string) or telegram username.
        Case-insensitive and strips leading '@'.
        """
        query_str = str(query).strip()
        if not query_str:
            return None

        # Check by direct numeric ID
        if query_str.isdigit() and query_str in self.users:
            return self.users[query_str]

        # Check by username
        clean_target = query_str.lstrip("@").strip().lower()
        if clean_target:
            for udata in self.users.values():
                u_name = udata.get("username", "").lstrip("@").strip().lower()
                if u_name and u_name == clean_target:
                    return udata
        return None

    def can_download(self, user_id: int) -> Tuple[bool, int, int, bool]:
        """
        Checks if user is allowed to download today.
        Returns (can_download, current_daily_count, daily_limit, is_vip)
        """
        if config.is_admin(user_id):
            return True, 0, config.free_download_limit, True

        user = self.get_or_create_user(user_id)
        is_vip = user.get("is_vip", False)

        # Check and enforce monthly subscription expiry
        if is_vip:
            vip_until_str = user.get("vip_until")
            if vip_until_str:
                try:
                    vip_until_dt = datetime.fromisoformat(vip_until_str)
                    if datetime.utcnow() > vip_until_dt:
                        # Monthly subscription expired
                        user["is_vip"] = False
                        self._save()
                        is_vip = False
                        logger.info(f"User {user_id} monthly VIP subscription expired on {vip_until_str}.")
                except Exception:
                    pass

        today_count = user.get("daily_downloads", 0)
        limit = config.free_download_limit

        if is_vip:
            return True, today_count, limit, True

        allowed = today_count < limit
        return allowed, today_count, limit, False

    def increment_download(self, user_id: int) -> int:
        """Increments daily and total download counts by 1."""
        user = self.get_or_create_user(user_id)
        user["daily_downloads"] = user.get("daily_downloads", 0) + 1
        user["total_downloads"] = user.get("total_downloads", 0) + 1
        user["last_download_date"] = get_today_str()
        user["last_active"] = datetime.utcnow().isoformat()
        self._save()
        return user["daily_downloads"]

    def set_vip(self, user_id: int, is_vip: bool = True, days: int = 30) -> Dict[str, Any]:
        """
        Grants or revokes VIP status with monthly subscription duration (default 30 days).
        If user already has active VIP, extends existing subscription by 'days'.
        """
        user = self.get_or_create_user(user_id)
        user["is_vip"] = is_vip
        now = datetime.utcnow()

        if is_vip:
            existing_until = user.get("vip_until")
            if existing_until:
                try:
                    parsed = datetime.fromisoformat(existing_until)
                    if parsed > now:
                        new_expiry = parsed + timedelta(days=days)
                    else:
                        new_expiry = now + timedelta(days=days)
                except Exception:
                    new_expiry = now + timedelta(days=days)
            else:
                new_expiry = now + timedelta(days=days)

            user["vip_until"] = new_expiry.isoformat()
            logger.info(f"Set monthly VIP for user {user_id} for {days} days until {user['vip_until']}")
        else:
            user["vip_until"] = None
            logger.info(f"Revoked VIP for user {user_id}")

        self._save()
        return user

    def get_vip_info(self, user_id: int) -> Dict[str, Any]:
        """Returns details about user's VIP monthly subscription."""
        if config.is_admin(user_id):
            return {
                "is_vip": True,
                "is_admin": True,
                "vip_until_str": "Lifetime (Admin)",
                "days_left": 9999,
                "expired": False,
            }

        user = self.get_or_create_user(user_id)
        is_vip = user.get("is_vip", False)
        vip_until_str = user.get("vip_until")
        now = datetime.utcnow()

        if is_vip and vip_until_str:
            try:
                vip_until = datetime.fromisoformat(vip_until_str)
                if now > vip_until:
                    user["is_vip"] = False
                    self._save()
                    return {"is_vip": False, "is_admin": False, "vip_until_str": None, "days_left": 0, "expired": True}

                days_left = max(1, (vip_until - now).days)
                formatted_date = vip_until.strftime("%Y-%m-%d %H:%M UTC")
                return {
                    "is_vip": True,
                    "is_admin": False,
                    "vip_until_str": formatted_date,
                    "days_left": days_left,
                    "expired": False,
                }
            except Exception:
                pass

        return {
            "is_vip": is_vip,
            "is_admin": False,
            "vip_until_str": None,
            "days_left": 0,
            "expired": False,
        }

    def submit_tx(self, user_id: int, tx_hash: str) -> None:
        """Records submitted transaction proof."""
        user = self.get_or_create_user(user_id)
        if "submitted_txs" not in user:
            user["submitted_txs"] = []
        user["submitted_txs"].append({
            "tx_hash": tx_hash.strip(),
            "submitted_at": datetime.utcnow().isoformat(),
            "status": "pending"
        })
        self._save()

    def get_stats(self) -> Dict[str, int]:
        """Calculates global stats with active VIP subscriptions."""
        now = datetime.utcnow()
        total_users = len(self.users)
        active_vips = 0
        for u in self.users.values():
            if u.get("is_vip", False):
                until = u.get("vip_until")
                if until:
                    try:
                        if datetime.fromisoformat(until) > now:
                            active_vips += 1
                    except Exception:
                        active_vips += 1
                else:
                    active_vips += 1

        total_downloads = sum(u.get("total_downloads", u.get("download_count", 0)) for u in self.users.values())
        return {
            "total_users": total_users,
            "total_vips": active_vips,
            "total_downloads": total_downloads,
        }

    def get_all_user_ids(self) -> List[int]:
        """Returns all registered user IDs."""
        return [int(uid) for uid in self.users.keys() if uid.isdigit()]

    def _load_reports(self):
        """Loads community problem reports from JSON."""
        if os.path.exists(REPORTS_FILE):
            try:
                with open(REPORTS_FILE, "r", encoding="utf-8") as f:
                    self.reports = json.load(f)
            except Exception as e:
                logger.warning(f"Could not load reports from {REPORTS_FILE}: {e}")
                self.reports = []
        else:
            self.reports = []

    def _save_reports(self):
        """Persists community problem reports to JSON files."""
        try:
            for p in [REPORTS_FILE, ROOT_REPORTS_FILE]:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(self.reports, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to persist reports: {e}")

    def add_report(self, user_id: int, username: str, first_name: str, text: str) -> Dict[str, Any]:
        """Records a new community problem report."""
        report = {
            "id": len(self.reports) + 1,
            "user_id": user_id,
            "username": f"@{username.lstrip('@')}" if username else "NoUsername",
            "first_name": first_name or "User",
            "text": text,
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "resolved": False,
        }
        self.reports.append(report)
        self._save_reports()
        return report

    def get_reports(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Returns the most recent reports (newest first)."""
        return list(reversed(self.reports[-limit:]))

    def resolve_report(self, report_id: int) -> Optional[Dict[str, Any]]:
        """Marks a report as resolved and returns the updated report."""
        for r in self.reports:
            if r.get("id") == report_id:
                r["resolved"] = True
                self._save_reports()
                return r
        return None


# Global tracker instance
tracker = UserTracker()

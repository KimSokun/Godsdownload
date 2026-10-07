import sys
import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Import real telegram packages if installed, otherwise mock for minimal test environments
try:
    import telegram
    import telegram.ext
except ImportError:
    if "telegram" not in sys.modules:
        sys.modules["telegram"] = MagicMock()
    if "telegram.ext" not in sys.modules:
        sys.modules["telegram.ext"] = MagicMock()

try:
    import qrcode
except ImportError:
    if "qrcode" not in sys.modules:
        sys.modules["qrcode"] = MagicMock()

from bot.config import Config
from bot.tracker import UserTracker
from bot.code_watcher import detect_file_changes, get_project_files_snapshot
from bot.handlers.admin_handlers import owner_only, setvip_command


class TestOwnerAndAuthorization(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.cfg.owner_username = "skun20000"
        self.cfg.owner_id = 999999
        self.cfg.admin_usernames = ["otheradmin"]
        self.cfg.admin_ids = [111111]

    def test_owner_matches_username(self):
        # Case insensitive and strips '@'
        self.assertTrue(self.cfg.is_owner(12345, "skun20000"))
        self.assertTrue(self.cfg.is_owner(12345, "@skun20000"))
        self.assertTrue(self.cfg.is_owner(12345, "@SKUN20000"))
        self.assertFalse(self.cfg.is_owner(12345, "someone_else"))

    def test_owner_matches_owner_id(self):
        self.assertTrue(self.cfg.is_owner(999999, None))
        self.assertFalse(self.cfg.is_owner(888888, None))

    def test_owner_is_automatically_admin(self):
        self.assertTrue(self.cfg.is_admin(12345, "@skun20000"))
        self.assertTrue(self.cfg.is_admin(999999, None))

    def test_non_owner_admin_is_not_owner(self):
        self.assertTrue(self.cfg.is_admin(111111, "otheradmin"))
        self.assertFalse(self.cfg.is_owner(111111, "otheradmin"))


class TestUserTrackerLookup(unittest.TestCase):
    def setUp(self):
        self.tracker = UserTracker()
        # Create test users
        self.tracker.get_or_create_user(555123, username="testvip", first_name="VipTester")
        self.tracker.get_or_create_user(555456, username="skun20000", first_name="Owner")

    def test_find_user_by_id(self):
        u = self.tracker.find_user("555123")
        self.assertIsNotNone(u)
        self.assertEqual(u["user_id"], 555123)

        u2 = self.tracker.find_user(555123)
        self.assertIsNotNone(u2)
        self.assertEqual(u2["user_id"], 555123)

    def test_find_user_by_username(self):
        u = self.tracker.find_user("testvip")
        self.assertIsNotNone(u)
        self.assertEqual(u["user_id"], 555123)

        u_at = self.tracker.find_user("@testvip")
        self.assertIsNotNone(u_at)
        self.assertEqual(u_at["user_id"], 555123)

        u_case = self.tracker.find_user("@TESTVIP")
        self.assertIsNotNone(u_case)
        self.assertEqual(u_case["user_id"], 555123)

    def test_find_nonexistent_user(self):
        u = self.tracker.find_user("nonexistent_user_xyz")
        self.assertIsNone(u)


class TestCodeWatcherChangeDetection(unittest.TestCase):
    def test_no_changes(self):
        snap1 = {"/path/to/main.py": (100.0, 500)}
        snap2 = {"/path/to/main.py": (100.0, 500)}
        changed = detect_file_changes(snap1, snap2)
        self.assertEqual(len(changed), 0)

    def test_mtime_modified(self):
        snap1 = {"/path/to/main.py": (100.0, 500)}
        snap2 = {"/path/to/main.py": (105.0, 500)}
        changed = detect_file_changes(snap1, snap2)
        self.assertIn("/path/to/main.py", changed)

    def test_size_modified(self):
        snap1 = {"/path/to/main.py": (100.0, 500)}
        snap2 = {"/path/to/main.py": (100.0, 600)}
        changed = detect_file_changes(snap1, snap2)
        self.assertIn("/path/to/main.py", changed)

    def test_new_file_added(self):
        snap1 = {"/path/to/main.py": (100.0, 500)}
        snap2 = {
            "/path/to/main.py": (100.0, 500),
            "/path/to/bot/new.py": (200.0, 300)
        }
        changed = detect_file_changes(snap1, snap2)
        self.assertIn("/path/to/bot/new.py", changed)


class TestOwnerSetVipCommand(unittest.TestCase):
    def setUp(self):
        from bot.tracker import tracker
        self.tracker = tracker
        self.tracker.get_or_create_user(777001, username="vipclient", first_name="Client")

    def test_setvip_grants_vip_to_user_id(self):
        update = MagicMock()
        update.effective_user = MagicMock(id=999999, username="skun20000", first_name="Owner")
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.args = ["777001", "45"]
        context.bot = MagicMock()
        context.bot.send_message = AsyncMock()

        # Run setvip
        asyncio.run(setvip_command(update, context))

        # Check tracker
        vip_info = self.tracker.get_vip_info(777001)
        self.assertTrue(vip_info["is_vip"])
        self.assertGreaterEqual(vip_info["days_left"], 44)

        # Check message to owner
        update.message.reply_text.assert_called_once()
        owner_reply = update.message.reply_text.call_args[0][0]
        self.assertIn("VIP Status Activated", owner_reply)
        self.assertIn("45 Days", owner_reply)

        # Check notification to user
        context.bot.send_message.assert_called_once()
        user_alert = context.bot.send_message.call_args[1]["text"]
        self.assertIn("UNLIMITED video downloads for 45 days", user_alert)
        # Ensure owner's raw username is NOT leaked into the user message text!
        self.assertNotIn("@skun20000", user_alert)

    def test_setvip_access_denied_for_non_owner(self):
        update = MagicMock()
        # Normal admin or random user
        update.effective_user = MagicMock(id=111111, username="regular_admin", first_name="Admin")
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.args = ["777001", "30"]

        asyncio.run(setvip_command(update, context))

        update.message.reply_text.assert_called_once()
        deny_text = update.message.reply_text.call_args[0][0]
        self.assertIn("Access Denied", deny_text)
        self.assertIn("Only the Bot Owner", deny_text)


class TestFiftyMegabyteLimitRule(unittest.TestCase):
    def test_default_limit_is_50mb(self):
        from bot.config import config
        from bot.platforms.base import get_telegram_limit_bytes, TELEGRAM_FILE_LIMIT_BYTES
        self.assertEqual(config.telegram_direct_limit_mb, 50.0)
        self.assertEqual(get_telegram_limit_bytes(), 50 * 1024 * 1024)
        self.assertEqual(TELEGRAM_FILE_LIMIT_BYTES, 50 * 1024 * 1024)


class TestDiagnosticAndFormatFallback(unittest.TestCase):
    def test_clean_bilibili_url(self):
        from bot.platforms.bilibili import clean_bilibili_url
        url1 = "https://www.bilibili.com/video/BV1citH6TE9n?spm_id_from=333.999.0.0"
        self.assertEqual(clean_bilibili_url(url1), "https://www.bilibili.com/video/BV1citH6TE9n")
        url2 = "https://www.bilibili.com/video/av12345678?vd_source=abc"
        self.assertEqual(clean_bilibili_url(url2), "https://www.bilibili.com/video/av12345678")

    def test_build_video_ydl_opts_fallback_without_ffmpeg(self):
        from bot.platforms.bilibili import BilibiliPlatform
        bp = BilibiliPlatform()
        with patch("bot.platforms.base.get_ffmpeg_path", return_value=None):
            opts = bp.build_video_ydl_opts("out.%(ext)s", use_cookies=False)
            self.assertIn("best[vcodec!=none][acodec!=none]", opts["format"])
            self.assertNotIn("ffmpeg_location", opts)

    def test_diag_command_output(self):
        from bot.handlers.admin_handlers import diag_command
        update = MagicMock()
        update.effective_user = MagicMock(id=999999, username="skun20000", first_name="Owner")
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        asyncio.run(diag_command(update, context))
        update.message.reply_text.assert_called_once()
        report = update.message.reply_text.call_args[0][0]
        self.assertIn("Godsdownload Server Diagnostic Report", report)
        self.assertIn("FFmpeg", report)
        self.assertIn("yt-dlp", report)


class TestMemberExport(unittest.TestCase):
    def setUp(self):
        self.tracker = UserTracker()
        self.tracker.users = {
            "10001": {
                "user_id": 10001,
                "username": "vipuser",
                "first_name": "VIP Person",
                "total_downloads": 25,
                "daily_downloads": 3,
                "is_vip": True,
                "vip_until": "2027-01-01T00:00:00",
                "created_at": "2026-09-01T00:00:00",
                "last_active": "2026-09-04T12:00:00"
            },
            "10002": {
                "user_id": 10002,
                "username": "freeuser",
                "first_name": "Free Person",
                "total_downloads": 5,
                "daily_downloads": 1,
                "is_vip": False,
                "created_at": "2026-09-02T00:00:00",
                "last_active": "2026-09-04T12:00:00"
            }
        }

    def test_export_member_data_breakdown(self):
        data = self.tracker.export_member_data()
        self.assertIn("summary", data)
        self.assertEqual(data["summary"]["total_members"], 2)
        self.assertEqual(data["summary"]["total_subscription_users"], 1)
        self.assertEqual(data["summary"]["total_normal_users"], 1)

        # Check subscription user fields
        sub = data["subscription_users"][0]
        self.assertEqual(sub["id"], 10001)
        self.assertEqual(sub["username"], "@vipuser")
        self.assertEqual(sub["status"], "VIP")
        self.assertGreater(sub["days_left"], 0)
        self.assertEqual(sub["total_downloads"], 25)

        # Check normal user fields
        norm = data["normal_users"][0]
        self.assertEqual(norm["id"], 10002)
        self.assertEqual(norm["username"], "@freeuser")
        self.assertEqual(norm["status"], "FREE")
        self.assertEqual(norm["total_downloads"], 5)

    def test_members_admin_command(self):
        from bot.handlers.admin_handlers import members_command
        update = MagicMock()
        update.effective_user = MagicMock(id=999999, username="skun20000", first_name="Owner")
        update.effective_chat = MagicMock(id=999999)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = []
        context.bot = MagicMock()
        context.bot.send_document = AsyncMock()

        asyncio.run(members_command(update, context))
        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        self.assertIn("Member Directory", text)
        self.assertIn("Total Members", text)
        self.assertIn("VIP Subscribers", text)

    def test_members_command_denied_for_non_owner(self):
        from bot.handlers.admin_handlers import members_command
        update = MagicMock()
        update.effective_user = MagicMock(id=12345, username="normal_user", first_name="User")
        update.effective_chat = MagicMock(id=12345)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = []

        asyncio.run(members_command(update, context))
        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        self.assertIn("Access Denied", text)
    def test_members_png_command(self):
        import os
        from bot.handlers.admin_handlers import members_command
        update = MagicMock()
        update.effective_user = MagicMock(id=999999, username="skun20000", first_name="Owner")
        update.effective_chat = MagicMock(id=999999)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = ["png"]
        context.bot = MagicMock()
        context.bot.send_photo = AsyncMock()

        asyncio.run(members_command(update, context))
        context.bot.send_photo.assert_called_once()
        caption = context.bot.send_photo.call_args[1]["caption"]
        self.assertIn("Member Analytics Dashboard (PNG)", caption)
        self.assertIn("VIP Subscribers", caption)

class TestNewFeatures(unittest.TestCase):
    def test_stream_15_minute_expiry(self):
        from bot.web_player import (
            register_stream,
            create_web_player_app,
            STREAM_EXPIRY_SECONDS,
            _ACTIVE_STREAMS
        )
        from aiohttp.test_utils import TestClient, TestServer
        self.assertEqual(STREAM_EXPIRY_SECONDS, 900)

        token = register_stream(
            filepath=None,
            direct_url="https://example.com/stream.mp4",
            title="Test Video",
            uploader="Tester",
            filesize_mb=75.0,
        )
        self.assertIn(token, _ACTIVE_STREAMS)

        # Simulate expiration by setting created_at 901 seconds ago
        _ACTIVE_STREAMS[token]["created_at"] -= 901

        async def check_expiry():
            app = create_web_player_app()
            async with TestClient(TestServer(app)) as client:
                resp = await client.get(f"/stream?v={token}")
                self.assertEqual(resp.status, 404)
                text = await resp.text()
                self.assertIn("expired", text.lower())

                w_resp = await client.get(f"/watch?v={token}")
                self.assertEqual(w_resp.status, 404)
                w_text = await w_resp.text()
                self.assertIn("expired", w_text.lower())

        asyncio.run(check_expiry())

    def test_get_stream_url_format(self):
        from bot.web_player import get_stream_url
        async def check_url():
            url = await get_stream_url("mytoken123")
            self.assertIn("/stream?v=mytoken123", url)
        asyncio.run(check_url())

    def test_buy_click_notifies_owner(self):
        from bot.handlers import payment_handlers
        mock_update = MagicMock()
        mock_msg = MagicMock()
        mock_msg.reply_text = AsyncMock()
        mock_update.effective_message = mock_msg
        mock_update.effective_chat.id = 12345
        mock_update.callback_query = None

        mock_user = MagicMock()
        mock_user.id = 88776655
        mock_user.username = "buyer_username"
        mock_user.first_name = "Buyer"
        mock_update.effective_user = mock_user

        mock_context = MagicMock()
        mock_context.bot.send_message = AsyncMock()
        mock_context.bot.send_photo = AsyncMock()

        asyncio.run(payment_handlers.upgrade_command(mock_update, mock_context))

        self.assertTrue(mock_context.bot.send_message.called)
        call_kwargs = mock_context.bot.send_message.call_args[1]
        sent_text = call_kwargs.get("text", "")
        self.assertIn("User Clicked Buy / Upgrade VIP", sent_text)
        self.assertIn("88776655", sent_text)
        self.assertIn("buyer_username", sent_text)

    def test_paid_done_notifies_owner_with_30_day_allowance(self):
        from bot.handlers import payment_handlers
        mock_update = MagicMock()
        mock_query = MagicMock()
        mock_query.answer = AsyncMock()
        mock_query.message.reply_text = AsyncMock()
        mock_update.callback_query = mock_query

        mock_user = MagicMock()
        mock_user.id = 55443322
        mock_user.username = "paid_user"
        mock_user.first_name = "PaidPerson"
        mock_update.effective_user = mock_user

        mock_context = MagicMock()
        mock_context.bot.send_message = AsyncMock()

        asyncio.run(payment_handlers.handle_paid_done_callback(mock_update, mock_context))

        self.assertTrue(mock_context.bot.send_message.called)
        call_kwargs = mock_context.bot.send_message.call_args[1]
        sent_text = call_kwargs.get("text", "")
        self.assertIn("55443322", sent_text)
        self.assertIn("paid_user", sent_text)

        kb = call_kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        btn_texts = []
        for row in kb.inline_keyboard:
            for btn in row:
                t = getattr(btn, "text", None)
                if isinstance(t, str):
                    btn_texts.append(t)
                elif hasattr(btn, "call_args") and btn.call_args:
                    btn_texts.append(str(btn.call_args[0][0]))
                else:
                    btn_texts.append(str(t or btn))
        self.assertTrue(any("Allow 30 Days VIP" in b for b in btn_texts))

    def test_report_problem_flow(self):
        from bot.handlers import user_handlers
        mock_update = MagicMock()
        mock_msg = MagicMock()
        mock_msg.reply_text = AsyncMock()
        mock_update.effective_message = mock_msg

        mock_user = MagicMock()
        mock_user.id = 11223344
        mock_user.username = "troubled_user"
        mock_user.first_name = "Troubled"
        mock_update.effective_user = mock_user

        mock_context = MagicMock()
        mock_context.args = ["Video", "from", "bilibili", "failed", "to", "stream"]
        mock_context.user_data = {}
        mock_context.bot.send_message = AsyncMock()

        asyncio.run(user_handlers.report_command(mock_update, mock_context))

        self.assertTrue(mock_context.bot.send_message.called)
        call_kwargs = mock_context.bot.send_message.call_args[1]
        sent_text = call_kwargs.get("text", "")
        self.assertIn("Community Problem Report", sent_text)
        self.assertIn("11223344", sent_text)
        self.assertIn("troubled_user", sent_text)
        self.assertIn("failed to stream", sent_text)


if __name__ == "__main__":
    unittest.main()

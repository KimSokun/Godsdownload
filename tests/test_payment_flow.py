import os
import sys
import io
import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import telegram
    import telegram.ext
except ImportError:
    for mod in ["telegram", "telegram.ext", "telegram.error", "telegram.request"]:
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

try:
    import qrcode
except ImportError:
    mock_qr = MagicMock()
    mock_qr.QRCode.return_value.make_image.return_value.save = lambda bio, fmt: bio.write(b"mock_png_data_" * 20)
    sys.modules["qrcode"] = mock_qr

from bot.config import config
from bot.tracker import tracker
from bot.handlers.payment_handlers import (
    generate_usdt_qr_bytes,
    upgrade_command,
    handle_paid_done_callback,
    admin_payment_callback,
)
from bot.handlers.admin_handlers import setvip_command


class TestPaymentAndVIPFlow(unittest.TestCase):
    """Tests the VIP upgrade, QR generation, payment flow, and owner VIP approval."""

    def setUp(self):
        self.test_user_id = 999111222
        self.test_username = "vip_tester"
        self.test_first_name = "VIP Tester"
        u = tracker.get_or_create_user(self.test_user_id, self.test_username, self.test_first_name)
        u["is_vip"] = False
        u["vip_until"] = None
        tracker._save()

    def test_01_qr_code_generation(self):
        """Verify USDT QR code byte stream generation."""
        bio = generate_usdt_qr_bytes(config.usdt_tron_address)
        self.assertIsNotNone(bio)
        self.assertTrue(bio.getbuffer().nbytes > 100)
        self.assertEqual(bio.name, "usdt_qr.png")

    def test_02_upgrade_command_content(self):
        """Verify upgrade command tells user to contact owner and send ID & username."""
        update = MagicMock()
        update.effective_user.id = self.test_user_id
        update.effective_user.username = self.test_username
        update.effective_user.first_name = self.test_first_name
        update.effective_chat.id = self.test_user_id
        update.callback_query = None

        context = MagicMock()
        context.bot.send_photo = AsyncMock()

        asyncio.run(upgrade_command(update, context))

        self.assertTrue(context.bot.send_photo.called)
        call_kwargs = context.bot.send_photo.call_args[1]
        caption = call_kwargs.get("caption", "")

        # Verify required user guidance
        self.assertIn(str(self.test_user_id), caption)
        self.assertIn(self.test_username, caption)
        self.assertIn(config.usdt_tron_address, caption)
        self.assertIn(config.primary_admin_username, caption)
        self.assertIn("TO BUY VIP, YOU NEED TO CONTACT THE OWNER", caption)

    def test_03_paid_done_alerts_owner(self):
        """Verify user clicking 'I have paid' instructs user to send ID and alerts admin."""
        update = MagicMock()
        query = MagicMock()
        query.answer = AsyncMock()
        query.message.reply_text = AsyncMock()
        update.callback_query = query
        update.effective_user.id = self.test_user_id
        update.effective_user.username = self.test_username
        update.effective_user.first_name = self.test_first_name

        context = MagicMock()
        context.bot.send_message = AsyncMock()

        with patch.object(config, "get_admin_chat_ids", return_value=[777777]):
            asyncio.run(handle_paid_done_callback(update, context))

        # Check client response tells user to send ID & username
        self.assertTrue(query.message.reply_text.called)
        user_msg = query.message.reply_text.call_args[0][0]
        self.assertIn(str(self.test_user_id), user_msg)
        self.assertIn("send your **User ID", user_msg)

        # Check admin was notified with user ID & allow button
        self.assertTrue(context.bot.send_message.called)
        admin_call = context.bot.send_message.call_args[1]
        admin_text = admin_call.get("text", "")
        self.assertIn(str(self.test_user_id), admin_text)
        self.assertIn("Waiting for 30D VIP", admin_text)

    def test_04_owner_setvip_activation(self):
        """Verify owner running /setvip activates 30-day VIP and gives unlimited quota."""
        # 1. Initially user is free
        can_dl, count, limit, is_vip = tracker.can_download(self.test_user_id)
        self.assertFalse(is_vip)

        # 2. Owner grants 30 days
        expiry_dt = tracker.set_vip(self.test_user_id, days=30)
        self.assertIsNotNone(expiry_dt)

        # 3. User now has VIP unlimited
        can_dl, count, limit, is_vip = tracker.can_download(self.test_user_id)
        self.assertTrue(is_vip)
        self.assertTrue(can_dl)

        vip_info = tracker.get_vip_info(self.test_user_id)
        self.assertTrue(vip_info["is_vip"])
        self.assertGreaterEqual(vip_info["days_left"], 29)

    def test_05_admin_payment_callback_approval(self):
        """Verify admin clicking 'Allow 30 Days VIP' button activates VIP."""
        test_uid = 555666777
        tracker.get_or_create_user(test_uid, "buyer_user", "Buyer")

        update = MagicMock()
        query = MagicMock()
        query.data = f"approve_vip:{test_uid}:paid"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        update.effective_user.first_name = "Owner Admin"
        update.effective_user.id = 123456789
        update.effective_user.username = config.owner_username
        query.from_user = update.effective_user

        context = MagicMock()
        context.bot.send_message = AsyncMock()

        asyncio.run(admin_payment_callback(update, context))

        # Check user is now VIP in tracker
        vip_info = tracker.get_vip_info(test_uid)
        self.assertTrue(vip_info["is_vip"])
        self.assertGreaterEqual(vip_info["days_left"], 29)

        # Check user was notified
        self.assertTrue(context.bot.send_message.called)
        sent_user_text = context.bot.send_message.call_args[1].get("text", "")
        self.assertIn("UNLIMITED video downloads for 30 days", sent_user_text)


if __name__ == "__main__":
    unittest.main()

import os
import sys
import atexit
import asyncio
import logging
from telegram import BotCommand
from telegram.error import Conflict
from telegram.request import HTTPXRequest
from telegram.ext import ApplicationBuilder, ContextTypes

from bot.config import config
from bot.auto_updater import start_auto_updater
from bot.code_watcher import start_code_watcher
from bot.handlers.user_handlers import setup_user_handlers
from bot.handlers.payment_handlers import setup_payment_handlers
from bot.handlers.admin_handlers import setup_admin_handlers

# Configure root logger
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("Godsdownload")


class SingleInstanceLock:
    """Ensures only ONE instance of the bot runs at any time to prevent 409 Conflict."""
    def __init__(self, lockfile="godsdownload.lock"):
        self.lockfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), lockfile)
        self.fp = None

    def acquire(self) -> bool:
        try:
            self.fp = open(self.lockfile, "w")
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.fp.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fp.write(str(os.getpid()))
            self.fp.flush()
            atexit.register(self.release)
            return True
        except (IOError, OSError, PermissionError):
            return False

    def release(self):
        if self.fp:
            try:
                if os.name == "nt":
                    import msvcrt
                    try:
                        msvcrt.locking(self.fp.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                self.fp.close()
                if os.path.exists(self.lockfile):
                    os.remove(self.lockfile)
            except Exception:
                pass


async def setup_bot_profile(bot):
    """
    Automatically configures Telegram bot description & menu commands
    for a rich profile appearance.
    """
    description_text = (
        "⚡ Godsdownload - Fast Video & Audio Downloader ⚡\n\n"
        "Supported Platforms (No Watermark & Full HD):\n"
        "• 🎵 TikTok & Douyin (抖音) (No Watermark Video & Photos)\n"
        "• 📺 YouTube & Shorts (HD/1080p Video & MP3)\n"
        "• 📸 Instagram & Reels (Videos, Photos & Carousels)\n"
        "• 📘 Facebook & FB Watch\n"
        "• 📺 Bilibili (哔哩哔哩 HD)\n"
        "• 🧵 Threads & 🐦 X (Twitter) (Videos & Photos)\n"
        "• 🤖 Reddit & 📌 Pinterest (Videos & HD Photos)\n"
        "• 🌐 1000+ Websites & Direct MP4 / MP3 Links\n\n"
        "🆓 Free Tier: 10 downloads per day\n"
        "💎 VIP: Unlimited downloads ($2 USDT / Month)\n"
        "💳 Payment: USDT TRON (TRC-20) ONLY\n"
        "👉 To Buy VIP: Contact Owner (@skun20000) and send your User ID & Username!"
    )

    short_description = "⚡ Fast Video & Image Downloader (TikTok, YouTube, IG, FB & 1000+ Sites) | VIP: USDT TRON ONLY"

    user_commands = [
        BotCommand("start", "🚀 Main Menu & Supported Platforms"),
        BotCommand("upgrade", f"💎 Upgrade VIP (Contact @{config.primary_admin_username})"),
        BotCommand("status", "📊 Daily Quota & VIP Subscription Status"),
        BotCommand("help", "📖 User Guide, Platform Scope & Help"),
        BotCommand("id", "🆔 Copy Your Telegram User ID"),
        BotCommand("report", "🚨 Report Problem or Link Issue"),
    ]

    try:
        from telegram import BotCommandScopeDefault, BotCommandScopeChat
        await bot.set_my_description(description=description_text)
        await bot.set_my_short_description(short_description=short_description)
        await bot.set_my_commands(commands=user_commands, scope=BotCommandScopeDefault())
        logger.info("✅ Bot description & public user commands configured.")

        # Admin commands registered exclusively for admin chat IDs
        admin_commands = user_commands + [
            BotCommand("admin", "👑 Admin Control Dashboard"),
            BotCommand("stats", "📊 Bot Analytics & Usage"),
            BotCommand("setvip", "💎 Grant or Revoke VIP"),
            BotCommand("reports", "📋 Review Bug Reports"),
            BotCommand("maintenance", "🔧 System Maintenance Audit"),
            BotCommand("restart", "🔄 Restart Bot Service"),
        ]
        for admin_id in config.get_admin_chat_ids():
            try:
                await bot.set_my_commands(commands=admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
            except Exception as admin_cmd_err:
                logger.debug(f"Could not set admin commands for chat {admin_id}: {admin_cmd_err}")
    except Exception as e:
        logger.warning(f"Could not update bot profile: {e}")


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catches all unhandled exceptions and prevents error leaks to Telegram."""
    if isinstance(context.error, Conflict):
        logger.error("⚠️ 409 Conflict: Another bot instance is already polling with this Bot Token!")
        return
    logger.error(f"Telegram Exception caught: {context.error}")


async def start_storage_cleaner(interval_minutes: int = 30, max_age_hours: float = 1.0) -> None:
    """Periodically cleans orphaned temp files and expired media cache."""
    from bot.maintenance import audit_storage_and_cleanup
    while True:
        try:
            await asyncio.sleep(interval_minutes * 60)
            res = audit_storage_and_cleanup(max_age_hours=max_age_hours)
            if res.get("cleaned_files", 0) > 0:
                logger.info(f"🧹 Storage auto-cleaner freed {res['cleaned_mb']} MB ({res['cleaned_files']} files).")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Storage auto-cleaner error: {e}")


async def main(lock=None):
    """Main application lifecycle."""
    if not config.bot_token:
        logger.error("❌ BOT_TOKEN is missing! Please configure it in your .env file.")
        sys.exit(1)

    # 1. Start Background yt-dlp Auto-Updater
    asyncio.create_task(start_auto_updater(interval_hours=6))

    # 1b. Start Background Storage Auto-Cleaner (runs every 30 minutes)
    asyncio.create_task(start_storage_cleaner(interval_minutes=30, max_age_hours=1.0))

    # 2. Configure HTTP Request timeouts (300 seconds for large uploads)
    request = HTTPXRequest(
        connection_pool_size=16,
        read_timeout=300.0,
        write_timeout=300.0,
        connect_timeout=30.0,
    )

    # 3. Build the Telegram Application (Supports Local Bot API Server for 2 GB uploads)
    builder = (
        ApplicationBuilder()
        .token(config.bot_token)
        .request(request)
        .concurrent_updates(True)
    )
    if config.telegram_api_url:
        file_url = config.telegram_file_url or config.telegram_api_url.rstrip("/bot").rstrip("/") + "/file/bot"
        builder = (
            builder
            .base_url(config.telegram_api_url)
            .base_file_url(file_url)
            .local_mode(True)
        )
        logger.info(f"🚀 Using Local Telegram Bot API Server: {config.telegram_api_url} (Up to 2000 MB direct uploads enabled!)")
    application = builder.build()

    # 4. Register Global Error Handler
    application.add_error_handler(global_error_handler)

    # 5. Register Feature Handlers
    setup_user_handlers(application)
    setup_payment_handlers(application)
    setup_admin_handlers(application)


    # 5c. Start Live Code Auto-Restart Watcher (watches for uploaded code changes)
    root_dir = os.path.dirname(os.path.abspath(__file__))
    asyncio.create_task(start_code_watcher(root_dir=root_dir, application=application, lock=lock, check_interval=2.0))

    # 6. Start Polling & Configure Bot Profile
    try:
        await application.initialize()
        await application.start()
        await setup_bot_profile(application.bot)
        await application.updater.start_polling(drop_pending_updates=True)
        logger.info("🚀 Bot is now LIVE and listening for messages!")
        
        # Keep running until cancelled
        while True:
            await asyncio.sleep(3600)

    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        logger.info("Shutting down bot...")
    finally:
        try:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
        except Exception as e:
            logger.warning(f"Error during bot shutdown: {e}")
        logger.info("Godsdownload stopped cleanly.")


if __name__ == "__main__":
    lock = SingleInstanceLock()
    if not lock.acquire():
        logger.warning("⚠️ Another Godsdownload Bot instance is ALREADY RUNNING. Exiting to prevent 409 Conflict.")
        sys.exit(0)

    try:
        asyncio.run(main(lock=lock))
    except KeyboardInterrupt:
        pass
    finally:
        lock.release()

import os
import html
import secrets
import logging
import uuid
import aiohttp
import aiofiles
from typing import Dict
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.config import config
from bot.tracker import tracker
from bot.downloader import download_video, download_audio, download_images, scan_url, extract_first_url, cleanup_files
from bot.platforms.router import detect_platform
from bot.platforms.base import ensure_ios_compatible_video
from bot.web_player import register_stream, get_player_url, get_stream_url, get_download_url

logger = logging.getLogger(__name__)

# In-memory short-lived cache for pending URL requests (Token -> URL)
pending_urls: Dict[str, str] = {}


def get_member_header(user_id: int) -> str:
    """Generates a stylish top header displaying member subscription status and total members using the bot."""
    can_dl, count, limit, is_vip = tracker.can_download(user_id)
    stats = tracker.get_stats()
    total_users = stats.get("total_users", 0)

    if is_vip:
        vip_info = tracker.get_vip_info(user_id)
        if vip_info.get("is_admin"):
            tier_badge = "👑 <b>[ VIP ADMIN • UNLIMITED ]</b>"
        else:
            days_left = vip_info.get("days_left", 30)
            tier_badge = f"👑 <b>[ VIP MEMBER • {days_left}D LEFT ]</b>"
    else:
        remaining = max(0, limit - count)
        tier_badge = f"🆓 <b>[ FREE MEMBER • {remaining}/{limit} TODAY ]</b>"

    return f"{tier_badge} • 👥 <b>{total_users:,} Members</b>"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command."""
    user = update.effective_user
    tracker.get_or_create_user(user.id, user.username or "", user.first_name or "")
    
    can_dl, count, limit, is_vip = tracker.can_download(user.id)
    first_name_safe = html.escape(user.first_name or "User")

    vip_info = tracker.get_vip_info(user.id)
    if is_vip:
        days_left = vip_info.get("days_left", 30)
        status_text = f"💎 <b>VIP Member (Monthly - {days_left} days remaining)</b>"
    else:
        remaining = max(0, limit - count)
        status_text = f"🆓 <b>Free Tier: {remaining}/{limit} downloads remaining today</b> (10 free downloads daily)"

    stats = tracker.get_stats()
    total_users = stats.get("total_users", 0)
    header = get_member_header(user.id)

    welcome_text = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 Hello <b>{first_name_safe}</b>! Welcome to <b>Godsdownload Bot</b> ⚡\n\n"
        f"🎬 <b>Supported Platforms (No Watermark & Full HD):</b>\n"
        f"• 🎵 <b>TikTok & Douyin (抖音)</b> (No Watermark)\n"
        f"• 📺 <b>YouTube & Shorts</b> (HD/1080p)\n"
        f"• 🎬 <b>WeTV & Tencent Video (腾讯视频)</b>\n"
        f"• 📸 <b>Instagram</b> (Reels, Posts, Stories)\n"
        f"• 📘 <b>Facebook & FB Watch</b>\n"
        f"• 📺 <b>Bilibili (哔哩哔哩)</b>\n"
        f"• 🧵 <b>Threads & 🐦 X (Twitter)</b>\n"
        f"• 📕 <b>Xiaohongshu (小红书 - RED)</b>\n"
        f"• 👻 <b>Kuaishou (快手) & Kwai</b>\n"
        f"• 🤖 <b>Reddit & 📌 Pinterest</b>\n"
        f"• 🌐 <b>1000+ Websites & Direct Video MP4 Links</b>\n\n"
        f"🎵 <b>Supports both Video (MP4) and Audio (MP3)!</b>\n\n"
        f"📊 <b>Your Status:</b> {status_text}\n"
        f"👥 <b>Total Community:</b> <b>{total_users:,} Members Using Godsdownload</b>\n\n"
        f"🚀 <b>How to use:</b> Simply send or forward any video link here!"
    )

    admin_uname = config.primary_admin_username
    keyboard = []
    if not is_vip:
        keyboard.append([InlineKeyboardButton(f"💎 Upgrade to VIP (Contact @{admin_uname})", callback_data="show_upgrade")])
    else:
        keyboard.append([InlineKeyboardButton(f"🔄 Extend VIP (Contact @{admin_uname})", callback_data="show_upgrade")])
    keyboard.append([
        InlineKeyboardButton("📊 My Account Status", callback_data="show_status"),
        InlineKeyboardButton("🚨 Report Problem", callback_data="btn_report_problem")
    ])
    if config.is_admin(user.id, user.username):
        keyboard.append([InlineKeyboardButton("👑 Admin Dashboard", callback_data="open_admin_panel")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /help command."""
    help_text = (
        "📖 <b>Godsdownload Bot Help</b>\n\n"
        "1. <b>Download Media</b>: Send any link directly in chat, then pick <b>🎬 Video (MP4)</b> or <b>🎵 Audio (MP3)</b>.\n"
        "2. <b>Instant Telegram Delivery</b>: Media up to 50 MB is delivered directly into your Telegram player.\n"
        "3. <b>Free Tier</b>: Includes <b>10 free downloads every day</b>.\n"
        "4. <b>VIP Subscription</b>: Pay <b>$2.00 USDT / Month (TRON / TRC-20)</b> for 30 days unlimited downloads with no daily limits!\n"
        "5. <b>Report Problems</b>: Use <b>/report &lt;details&gt;</b> or tap <b>🚨 Report Problem</b> to report issues directly to the owner!\n\n"
        "📌 <b>Commands:</b>\n"
        "/start - Start or restart the bot\n"
        "/status - View your daily download quota & VIP status\n"
        "/upgrade - Subscribe to VIP ($2 USD / Month)\n"
        "/report - Report an issue or bug directly to the owner\n"
        "/help - Display this help message"
    )
    keyboard = [
        [InlineKeyboardButton("🚨 Report Problem / Issue", callback_data="btn_report_problem")]
    ]
    await update.message.reply_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /status command."""
    user = update.effective_user
    can_dl, count, limit, is_vip = tracker.can_download(user.id)
    user_data = tracker.get_or_create_user(user.id)
    total_count = user_data.get("total_downloads", count)
    first_name_safe = html.escape(user.first_name or "User")
    vip_info = tracker.get_vip_info(user.id)

    header = get_member_header(user.id)

    stats = tracker.get_stats()
    total_users = stats.get("total_users", 0)
    total_downloads = stats.get("total_downloads", 0)

    if is_vip:
        expiry_str = vip_info.get("vip_until_str") or "Active"
        days_left = vip_info.get("days_left", 30)
        text = (
            f"{header}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👑 <b>Subscription Status: VIP Member</b>\n\n"
            f"👤 User: {first_name_safe} (<code>{user.id}</code>)\n"
            f"📅 <b>Valid Until:</b> {expiry_str} ({days_left} days left)\n"
            f"📥 Your Downloads: <b>{total_count}</b>\n"
            f"✨ Daily Quota: <b>UNLIMITED</b>\n\n"
            f"👥 <b>Total Members Using Bot:</b> <b>{total_users:,}</b>\n"
            f"⚡ <b>Total Processed:</b> <b>{total_downloads:,} downloads</b>\n\n"
            f"Thank you for supporting Godsdownload!"
        )
        admin_uname = config.primary_admin_username
        admin_url = config.admin_contact_url
        keyboard = [
            [InlineKeyboardButton(f"🔄 Extend VIP Subscription (Contact @{admin_uname})", callback_data="show_upgrade")],
            [
                InlineKeyboardButton(f"💬 Contact Admin (@{admin_uname})", url=admin_url),
                InlineKeyboardButton("🚨 Report Problem", callback_data="btn_report_problem")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
    else:
        admin_uname = config.primary_admin_username
        admin_url = config.admin_contact_url
        remaining = max(0, limit - count)
        text = (
            f"{header}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Subscription Status: Free Member</b>\n\n"
            f"👤 User: {first_name_safe} (<code>{user.id}</code>)\n"
            f"📥 Today's Downloads: <b>{count} / {limit} used</b>\n"
            f"⏳ Remaining Today: <b>{remaining} free downloads</b>\n"
            f"🔄 Daily Reset: <b>Every day at 00:00 UTC</b>\n\n"
            f"👥 <b>Total Members Using Bot:</b> <b>{total_users:,}</b>\n"
            f"⚡ <b>Total Processed:</b> <b>{total_downloads:,} downloads</b>\n\n"
            f"💡 Upgrade to VIP for <b>$2.00 USD / Month</b> to unlock unlimited downloads for 30 days!\n"
            f"👉 Contact Admin <b>@{admin_uname}</b> to activate your VIP access."
        )
        keyboard = [
            [InlineKeyboardButton(f"💎 Upgrade to VIP (Contact @{admin_uname})", callback_data="show_upgrade")],
            [
                InlineKeyboardButton(f"💬 Contact Admin (@{admin_uname})", url=admin_url),
                InlineKeyboardButton("🚨 Report Problem", callback_data="btn_report_problem")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")


async def send_image_gallery(context: ContextTypes.DEFAULT_TYPE, chat_id: int, image_urls: list, title: str = "Photo Album", uploader: str = "Creator") -> bool:
    """
    Sends images directly in Telegram in media group albums of up to 10 photos.
    Uses direct CDN URLs first (0 server disk usage).
    Falls back to temporary download + immediate cleanup if Telegram cannot fetch direct URL.
    """
    if not image_urls:
        return False

    safe_title = html.escape(title or "Photo Album")
    safe_uploader = html.escape(uploader or "Creator")
    total_imgs = len(image_urls)

    chunks = [image_urls[i:i + 10] for i in range(0, total_imgs, 10)]

    for chunk_idx, chunk in enumerate(chunks):
        caption = None
        if chunk_idx == 0:
            caption = f"📸 <b>{safe_title}</b> ({total_imgs} Images)\n👤 <b>Creator:</b> {safe_uploader}"

        sent = False
        # Method 1: Direct CDN URLs (Zero Server Disk Storage)
        try:
            media_group = []
            for idx, img_url in enumerate(chunk):
                if idx == 0 and caption:
                    media_group.append(InputMediaPhoto(media=img_url, caption=caption, parse_mode="HTML"))
                else:
                    media_group.append(InputMediaPhoto(media=img_url))
            await context.bot.send_media_group(chat_id=chat_id, media=media_group)
            sent = True
        except Exception as e:
            logger.debug(f"Direct URL send_media_group failed ({e}), attempting local streaming fallback...")

        # Method 2: Temporary download with INSTANT deletion
        if not sent:
            temp_paths = []
            try:
                media_group = []
                img_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"}
                async with aiohttp.ClientSession(headers=img_headers) as session:
                    for idx, img_url in enumerate(chunk):
                        temp_file = os.path.join(config.temp_dir, f"img_{uuid.uuid4().hex}.jpg")
                        async with session.get(img_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                            if resp.status == 200:
                                async with aiofiles.open(temp_file, "wb") as f:
                                    await f.write(await resp.read())
                                # Ensure image is a valid standard RGB JPEG for Apple Photos compatibility
                                try:
                                    with Image.open(temp_file) as img:
                                        if img.format != "JPEG" or img.mode != "RGB":
                                            rgb_img = img.convert("RGB")
                                            rgb_img.save(temp_file, "JPEG", quality=95)
                                except Exception:
                                    pass
                                temp_paths.append(temp_file)
                                fp = open(temp_file, "rb")
                                if idx == 0 and caption:
                                    media_group.append(InputMediaPhoto(media=fp, caption=caption, parse_mode="HTML"))
                                else:
                                    media_group.append(InputMediaPhoto(media=fp))

                if media_group:
                    await context.bot.send_media_group(chat_id=chat_id, media=media_group)
                    sent = True
            except Exception as e:
                logger.error(f"Failed to send media group chunk {chunk_idx}: {e}")
            finally:
                for p in temp_paths:
                    try:
                        cleanup_files(p)
                    except Exception:
                        pass

    return True


async def handle_video_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming text messages with video URLs and offers MP4 / MP3 options."""
    message = update.effective_message
    user = update.effective_user

    if not message or not message.text:
        return

    # Check if user is submitting a problem report
    user_data = getattr(context, "user_data", None)
    if isinstance(user_data, dict) and user_data.get("waiting_for_report") is True:
        await _send_report_to_owner(update, context, user, message.text.strip())
        return

    url = extract_first_url(message.text)
    if not url:
        report_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚨 Report Problem", callback_data="btn_report_problem")]
        ])
        await message.reply_text(
            "⚠️ Please send a valid video URL (e.g. YouTube, TikTok, Facebook, Instagram, Twitter, etc.)\n\n"
            "If you found a problem or a video failed, click below to report it to the owner:",
            reply_markup=report_kb
        )
        return

    # Check daily free limit (10 per day)
    can_dl, count, limit, is_vip = tracker.can_download(user.id)
    if not can_dl:
        admin_uname = config.primary_admin_username
        admin_url = config.admin_contact_url
        limit_text = (
            f"⚠️ <b>Daily Free Limit Reached ({count}/{limit} today)</b>\n\n"
            f"You have used all <b>10 free downloads for today</b>!\n"
            f"Your daily quota will automatically reset tomorrow at 00:00 UTC.\n\n"
            f"🚀 <b>Want Unlimited Downloads Right Now?</b>\n"
            f"Upgrade to <b>VIP Monthly Subscription</b> for only <b>$2.00 USD / Month</b>:\n"
            f"• ♾️ 30 days of unlimited downloads without daily limits\n"
            f"• ⚡ High-speed priority processing\n\n"
            f"👉 <b>To upgrade, please contact our Admin directly on Telegram:</b>\n"
            f"👑 <b>Admin:</b> @{admin_uname}\n"
            f"🆔 <b>Your User ID:</b> <code>{user.id}</code> <i>(Tap to copy and send to Admin)</i>"
        )
        keyboard = [
            [InlineKeyboardButton(f"💬 Contact Admin (@{admin_uname}) to Upgrade", url=admin_url)],
            [InlineKeyboardButton("📊 My Account Status", callback_data="show_status")]
        ]
        await message.reply_text(limit_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    # Detect platform
    platform = detect_platform(url)

    # Fast Scan First! (Ultra-low latency image carousel probe)
    # Note: For Threads, do NOT auto-send images, allowing user to choose Video, Audio, or Images!
    scan_info = await scan_url(url)
    if platform.platform_id != "threads" and scan_info and scan_info.get("type") == "images" and scan_info.get("image_urls"):
        img_urls = scan_info["image_urls"]
        status_msg = await message.reply_text(
            f"⚡ <b>Detected {len(img_urls)} HD Images from {platform.display_name}!</b>\n"
            f"📸 <i>Sending photos now...</i>",
            parse_mode="HTML"
        )
        ok = await send_image_gallery(
            context,
            chat_id=update.effective_chat.id,
            image_urls=img_urls,
            title=scan_info.get("title", "Photos"),
            uploader=scan_info.get("uploader", "Creator")
        )
        try:
            await status_msg.delete()
        except Exception:
            pass

        if ok:
            tracker.increment_download(user.id)
            token = secrets.token_hex(8)
            pending_urls[token] = url
            extra_buttons = []
            if scan_info.get("audio_url"):
                extra_buttons.append([InlineKeyboardButton("🎵 Download Background Audio (MP3)", callback_data=f"dl_media:audio:{token}")])
            extra_buttons.append([InlineKeyboardButton("🚨 Report Problem", callback_data="btn_report_problem")])
            await message.reply_text(
                f"✅ <b>Successfully delivered {len(img_urls)} HD photos!</b>",
                reply_markup=InlineKeyboardMarkup(extra_buttons),
                parse_mode="HTML"
            )
            return

    # Store URL in pending cache
    token = secrets.token_hex(8)
    pending_urls[token] = url

    remaining = max(0, limit - count)
    quota_badge = "💎 <b>VIP (Unlimited)</b>" if is_vip else f"🆓 <b>Today's Quota: {remaining}/{limit} left</b>"

    # Dynamic images button text
    has_imgs = bool(scan_info and scan_info.get("image_urls"))
    img_count = len(scan_info["image_urls"]) if has_imgs else 0
    img_btn_text = f"🖼️ Photos ({img_count} HD)" if has_imgs else "🖼️ Photos / Gallery"

    # Prompt user to choose Video (MP4), Audio (MP3), or Images
    keyboard = [
        [
            InlineKeyboardButton("🎬 Video (MP4)", callback_data=f"dl_media:video:{token}"),
            InlineKeyboardButton("🎵 Audio (MP3)", callback_data=f"dl_media:audio:{token}")
        ],
        [
            InlineKeyboardButton(img_btn_text, callback_data=f"dl_media:images:{token}"),
            InlineKeyboardButton("🚨 Report Problem", callback_data="btn_report_problem")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    header = get_member_header(user.id)
    await message.reply_text(
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>Platform Detected:</b> {platform.display_name}\n"
        f"⚡ <b>Choose Download Format:</b>\n\n"
        f"Click a button below to download:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )


async def media_download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles user format selection (MP4 vs MP3)."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    chat_id = update.effective_chat.id
    data = query.data.split(":")
    media_type = data[1]  # "video" or "audio"
    token = data[2]

    url = pending_urls.get(token)
    if not url:
        await query.message.reply_text("⚠️ Request expired or invalid. Please send the link again.")
        return

    # Check daily limit again
    can_dl, count, limit, is_vip = tracker.can_download(user.id)
    if not can_dl:
        admin_uname = config.primary_admin_username
        admin_url = config.admin_contact_url
        limit_text = (
            f"⚠️ <b>Daily Free Limit Reached ({count}/{limit} today)</b>\n\n"
            f"You have used your <b>10 free downloads for today</b>! Resets tomorrow.\n\n"
            f"🚀 <b>Upgrade to VIP Monthly Subscription ($2.00 USD / Month)</b> for 30 days of unlimited downloads without waiting!\n\n"
            f"👉 <b>To upgrade, please contact our Admin directly on Telegram:</b>\n"
            f"👑 <b>Admin:</b> @{admin_uname}\n"
            f"🆔 <b>Your User ID:</b> <code>{user.id}</code> <i>(Tap to copy and send to Admin)</i>"
        )
        keyboard = [
            [InlineKeyboardButton(f"💬 Contact Admin (@{admin_uname}) to Upgrade", url=admin_url)],
            [InlineKeyboardButton("📊 My Account Status", callback_data="show_status")]
        ]
        await query.message.reply_text(limit_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    max_size_mb = config.max_file_size_mb

    platform = detect_platform(url)
    header = get_member_header(user.id)

    if media_type in ("image", "images"):
        status_msg = await query.message.reply_text(
            f"{header}\n⏳ <b>Scanning and extracting images from {platform.display_name}...</b>",
            parse_mode="HTML"
        )
        result = await download_images(url)
        if result.success and result.image_urls:
            await send_image_gallery(
                context,
                chat_id=chat_id,
                image_urls=result.image_urls,
                title=result.title,
                uploader=result.uploader
            )
            tracker.increment_download(user.id)
            try:
                await status_msg.delete()
            except Exception:
                pass
            await query.message.reply_text(
                f"✅ <b>Successfully delivered {len(result.image_urls)} HD images!</b>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚨 Report Problem", callback_data="btn_report_problem")]]),
                parse_mode="HTML"
            )
            return
        else:
            await status_msg.edit_text("⚠️ No images found in this link.", parse_mode="HTML")
            return

    format_name = "🎵 MP3 Audio" if media_type == "audio" else "🎬 MP4 Video"
    status_msg = await query.message.reply_text(
        f"{header}\n"
        f"⏳ <b>Downloading {format_name} from {platform.display_name}...</b>\n"
        f"⚡ <i>Extracting highest quality stream, please wait...</i>",
        parse_mode="HTML"
    )

    if media_type == "audio":
        result = await download_audio(url, max_size_mb=max_size_mb)
    else:
        result = await download_video(url, max_size_mb=max_size_mb)

    if not result.success or (not result.filepath and not result.is_over_50mb):
        err_msg = result.error or "Unknown download error."
        
        # Log complete technical details to server log
        logger.error(f"❌ [Server Log] Download error for user {user.id} ({user.first_name}) on {url}:\n{err_msg}")
        
        if "NO_VIDEO" in err_msg or "NO_AUDIO" in err_msg:
            photo_btn = InlineKeyboardMarkup([
                [InlineKeyboardButton("🖼️ Download Photos Now", callback_data=f"dl_media:images:{token}")]
            ])
            await status_msg.edit_text(
                "⚠️ <b>This Threads post contains photos only (no video).</b>\n\n"
                "Please tap below to download the photos:",
                reply_markup=photo_btn,
                parse_mode="HTML"
            )
            return

        if "FILE_TOO_LARGE" in err_msg:
            parts = err_msg.split(":")
            actual_size = parts[1] if len(parts) > 1 else "Unknown"
            allowed_size = parts[2] if len(parts) > 2 else str(max_size_mb)

            too_large_msg = (
                f"⚠️ <b>File Exceeds Maximum Limit ({actual_size} MB)</b>\n\n"
                f"Maximum supported file size is <b>{allowed_size} MB</b>."
            )
            await status_msg.edit_text(too_large_msg, parse_mode="HTML")
            return

        if any(k in err_msg for k in ["NOTE_NOT_FOUND", "页面不见了", "initial state", "Video unavailable", "Private video", "This video is unavailable", "not available"]):
            await status_msg.edit_text(
                "⚠️ <b>Content Not Available</b>\n\n"
                "This video or post appears to have been deleted, set to private, or is restricted by the platform.",
                parse_mode="HTML"
            )
            return

        if "412" in err_msg or "Precondition" in err_msg:
            retry_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Retry Download", callback_data=f"dl:{media_type}:{token}")]
            ])
            await status_msg.edit_text(
                "⏳ <b>Platform Temporarily Busy</b>\n\n"
                "The platform is temporarily rate-limiting requests. Please tap below to retry in a moment:",
                reply_markup=retry_keyboard,
                parse_mode="HTML"
            )
            return

        if "ffmpeg" in err_msg.lower() and ("not installed" in err_msg.lower() or "not found" in err_msg.lower()):
            await status_msg.edit_text(
                "⚠️ <b>Server Codec Notice</b>\n\n"
                "FFmpeg is required on the server to merge this format. Please notify the administrator to run <code>apt-get install -y ffmpeg</code>.",
                parse_mode="HTML"
            )
            return

        # Show clean friendly message on Telegram (no raw stack traces)
        await status_msg.edit_text(
            "❌ <b>Download Failed</b>\n\n"
            "Unable to process this link at the moment. Please make sure the link is public and accessible, then try again.",
            parse_mode="HTML"
        )
        return

    safe_title = html.escape(result.title or "Media")
    safe_uploader = html.escape(result.uploader or "Creator")

    # For video downloads, guarantee Apple iOS Photos compatibility (H.264 + AAC + FastStart + creation_time=now)
    if result.filepath and os.path.exists(result.filepath) and media_type != "audio":
        result.filepath = await ensure_ios_compatible_video(result.filepath)
        if os.path.exists(result.filepath):
            result.filesize_bytes = os.path.getsize(result.filepath)
            result.filesize_mb = round(result.filesize_bytes / (1024 * 1024), 2)

    # Check actual file size on disk if available
    actual_file_bytes = 0
    if result.filepath and os.path.exists(result.filepath):
        try:
            actual_file_bytes = os.path.getsize(result.filepath)
        except Exception:
            pass

    # Strictly enforce 50 MB limit for direct in-app Telegram uploads
    # Videos 50 MB and over are served via Direct Stream (CDN) with 15-minute expiration
    is_over_50mb = (
        result.is_over_50mb
        or (result.filesize_mb is not None and result.filesize_mb >= 50.0)
        or (actual_file_bytes >= 50 * 1024 * 1024)
        or (result.filesize_mb and result.filesize_mb > config.telegram_direct_limit_mb)
    )

    if is_over_50mb:
        stream_token = register_stream(
            filepath=result.filepath,
            direct_url=result.direct_url,
            title=result.title,
            uploader=result.uploader,
            filesize_mb=result.filesize_mb,
            headers=getattr(result, "headers", None),
            is_audio=(media_type == "audio"),
        )
        stream_url = await get_stream_url(stream_token)
        download_url = await get_download_url(stream_token)
        player_url = await get_player_url(stream_token)

        header = get_member_header(user.id)
        new_daily_count = tracker.increment_download(user.id)
        filesize_display = f"{result.filesize_mb:.1f} MB" if result.filesize_mb else "50+ MB"

        # Direct download link via server proxy guarantees anti-hotlinking headers (Bilibili/TikTok Referer)
        # and stays valid for 2 full hours across all platforms
        direct_download_url = download_url
        direct_cdn_section = (
            f"⚡ <b>High-Speed Direct Link:</b>\n"
            f"🔗 <a href=\"{direct_download_url}\">Click here to Download HD</a> <i>(Valid for 2 Hours)</i>\n\n"
        )

        large_file_msg = (
            f"{header}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎬 <b>{safe_title}</b>\n\n"
            f"👤 <b>Channel:</b> {safe_uploader}\n"
            f"📦 <b>Size:</b> <b>{filesize_display}</b> (Exceeds Telegram 50 MB Limit)\n\n"
            f"{direct_cdn_section}"
            f"⚡ <i>Telegram limits direct file delivery to 50 MB. Click below for instant high-speed HD download, watch in browser, or download MP3!</i>"
        )
        keyboard = [
            [InlineKeyboardButton("⚡ HD Download (Valid 2 Hours)", url=direct_download_url)],
            [InlineKeyboardButton("🎬 ▶️ Watch in Web Player", url=player_url)],
            [InlineKeyboardButton("🎵 MP3 Download", callback_data=f"dl_media:audio:{token}")],
            [InlineKeyboardButton("🚨 Report Problem", callback_data="btn_report_problem")]
        ]

        await status_msg.edit_text(large_file_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    try:
        bot_username = (await context.bot.get_me()).username
        new_daily_count = tracker.increment_download(user.id)
        
        if is_vip:
            quota_line = "💎 <b>VIP: Unlimited Downloads (Monthly)</b>"
        else:
            left = max(0, limit - new_daily_count)
            quota_line = f"🆓 <b>Free Downloads: {left}/{limit} remaining today</b>"

        # Register stream in web player for browser viewing
        stream_token = register_stream(
            filepath=result.filepath,
            direct_url=result.direct_url,
            title=result.title,
            uploader=result.uploader,
            filesize_mb=result.filesize_mb,
            headers=getattr(result, "headers", None),
            is_audio=(media_type == "audio"),
        )
        player_url = await get_player_url(stream_token)
        player_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 ▶️ Watch in Web Player", url=player_url)]
        ])

        # File <= 50MB -> Send directly in Telegram
        header = get_member_header(user.id)
        await status_msg.edit_text(
            f"{header}\n"
            f"📤 <b>Uploading {format_name} to Telegram...</b>\n"
            f"📦 Size: <b>{result.filesize_mb} MB</b>",
            parse_mode="HTML"
        )

        # Send typing / uploading action
        chat_action = "upload_voice" if media_type == "audio" else "upload_video"
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=chat_action)
        except Exception:
            pass

        # Safe Thumbnail Handling
        thumb_file = None
        if result.thumbnail_path and os.path.exists(result.thumbnail_path):
            try:
                thumb_file = open(result.thumbnail_path, "rb")
            except Exception:
                thumb_file = None

        try:
            with open(result.filepath, "rb") as media_file:
                if media_type == "audio":
                    caption = (
                        f"{header}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎵 <b>{safe_title}</b>\n"
                        f"👤 Artist: {safe_uploader}\n"
                        f"📦 Size: {result.filesize_mb} MB\n"
                        f"⚡ @{bot_username}"
                    )
                    try:
                        await context.bot.send_audio(
                            chat_id=chat_id,
                            audio=media_file,
                            caption=caption,
                            title=result.title[:64],
                            performer=result.uploader[:64],
                            duration=result.duration,
                            thumbnail=thumb_file,
                            reply_markup=player_markup,
                            read_timeout=300,
                            write_timeout=300,
                            parse_mode="HTML"
                        )
                    except Exception as send_err:
                        logger.warning(f"send_audio with thumb failed ({send_err}), retrying without thumbnail...")
                        media_file.seek(0)
                        await context.bot.send_audio(
                            chat_id=chat_id,
                            audio=media_file,
                            caption=caption,
                            title=result.title[:64],
                            performer=result.uploader[:64],
                            duration=result.duration,
                            thumbnail=None,
                            reply_markup=player_markup,
                            read_timeout=300,
                            write_timeout=300,
                            parse_mode="HTML"
                        )
                else:
                    caption = (
                        f"{header}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎬 <b>{safe_title}</b>\n"
                        f"📦 Size: {result.filesize_mb} MB\n"
                        f"⚡ Downloaded via @{bot_username}\n"
                        f"💡 <i>Tip for iPhone: Tap video ➡️ ••• ➡️ 'Save Video' to save to Photos</i>"
                    )
                    try:
                        await context.bot.send_video(
                            chat_id=chat_id,
                            video=media_file,
                            caption=caption,
                            duration=result.duration,
                            thumbnail=thumb_file,
                            supports_streaming=True,
                            reply_markup=player_markup,
                            read_timeout=300,
                            write_timeout=300,
                            parse_mode="HTML"
                        )
                    except Exception as send_err:
                        logger.warning(f"send_video with thumb failed ({send_err}), retrying without thumbnail...")
                        media_file.seek(0)
                        await context.bot.send_video(
                            chat_id=chat_id,
                            video=media_file,
                            caption=caption,
                            duration=result.duration,
                            thumbnail=None,
                            supports_streaming=True,
                            reply_markup=player_markup,
                            read_timeout=300,
                            write_timeout=300,
                            parse_mode="HTML"
                        )
        finally:
            if thumb_file:
                try:
                    thumb_file.close()
                except Exception:
                    pass

        try:
            await status_msg.delete()
        except Exception:
            pass

        # Cleanup thumbnail immediately; video file is preserved for web player and auto-cleaned after 2h
        cleanup_files(result.thumbnail_path)

        # If user just reached 10 downloads today, send a helpful upgrade reminder
        if not is_vip and new_daily_count >= limit:
            upgrade_alert = (
                f"🎉 <b>You've used all 10 free downloads for today!</b>\n\n"
                f"Your free limit resets tomorrow at 00:00 UTC.\n"
                f"Upgrade to <b>VIP Monthly Subscription</b> for only <b>$2.00 USD / Month</b> to download without any daily limits!"
            )
            keyboard = [
                [InlineKeyboardButton("💎 Pay with USDT-TRC20 ($2 USD / Month)", callback_data="pay_usdt")],
                [InlineKeyboardButton("💬 Contact Support", url=config.admin_contact_url)]
            ]
            await context.bot.send_message(
                chat_id=chat_id,
                text=upgrade_alert,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"Error sending downloaded media: {e}", exc_info=True)
        # If Telegram rejected because file is too big, serve via browser URL
        err_lower = str(e).lower()
        if "too big" in err_lower or "entity too large" in err_lower or "file_too_big" in err_lower:
            try:
                stream_token = register_stream(
                    filepath=result.filepath,
                    direct_url=result.direct_url,
                    title=result.title,
                    uploader=result.uploader,
                    filesize_mb=result.filesize_mb,
                    headers=getattr(result, "headers", None),
                    is_audio=(media_type == "audio"),
                )
                stream_url = await get_stream_url(stream_token)
                download_url = await get_download_url(stream_token)
                player_url = await get_player_url(stream_token)
                direct_download_url = download_url
                direct_cdn_section = (
                    f"⚡ <b>High-Speed Direct Link:</b>\n"
                    f"🔗 <a href=\"{direct_download_url}\">Click here to Download HD</a> <i>(Valid for 2 Hours)</i>\n\n"
                )
                fallback_msg = (
                    f"🎬 <b>{safe_title}</b>\n\n"
                    f"👤 <b>Channel:</b> {safe_uploader}\n"
                    f"📦 <b>Size:</b> <b>{result.filesize_mb or '50+'} MB</b> (Exceeds Telegram Direct Limit)\n\n"
                    f"{direct_cdn_section}"
                    f"⚡ <i>Telegram limits direct file delivery to 50 MB. Click below for instant high-speed HD download, watch in browser, or download MP3!</i>"
                )
                fallback_kb = [
                    [InlineKeyboardButton("⚡ HD Download (Valid 2 Hours)", url=direct_download_url)],
                    [InlineKeyboardButton("🎬 ▶️ Watch in Web Player", url=player_url)],
                    [InlineKeyboardButton("🎵 MP3 Download", callback_data=f"dl_media:audio:{token}")],
                    [InlineKeyboardButton("🚨 Report Problem", callback_data="btn_report_problem")]
                ]
                await status_msg.edit_text(fallback_msg, reply_markup=InlineKeyboardMarkup(fallback_kb), parse_mode="HTML")
                return
            except Exception:
                pass

        cleanup_files(result.filepath, result.thumbnail_path)
        try:
            fail_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🚨 Report Problem", callback_data="btn_report_problem")]
            ])
            await status_msg.edit_text(
                "❌ An error occurred while uploading media. Please try again or report the problem to the owner.",
                reply_markup=fail_kb,
                parse_mode="HTML"
            )
        except Exception:
            pass


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /report command. If owner/admin, opens Owner Report Dashboard in Telegram."""
    user = update.effective_user
    args = context.args or []

    # If owner/admin runs /report without user submit text, open Owner Report Control Dashboard
    if config.is_admin(user.id, user.username) and (not args or args[0].lower() in ["list", "view", "panel", "admin", "file", "json"]):
        from bot.handlers.admin_handlers import reports_command
        await reports_command(update, context)
        return

    if not args:
        context.user_data["waiting_for_report"] = True
        cancel_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_report")]
        ])
        await update.message.reply_text(
            "📝 **Community & Problem Report**\n\n"
            "Please reply to this message with details of the problem you encountered (e.g., the video link that failed or what went wrong).\n\n"
            "Your report will be sent directly to the owner so it can be fixed! 🛠️",
            reply_markup=cancel_kb,
            parse_mode="Markdown"
        )
        return

    report_text = " ".join(args).strip()
    await _send_report_to_owner(update, context, user, report_text)


async def report_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles 'btn_report_problem' button click."""
    query = update.callback_query
    await query.answer()
    context.user_data["waiting_for_report"] = True
    cancel_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_report")]
    ])
    await query.message.reply_text(
        "📝 **Community & Problem Report**\n\n"
        "Please reply with a description of the issue or send the video link that had problems.\n\n"
        "Your report goes directly to the bot owner so we can fix it! 🛠️",
        reply_markup=cancel_kb,
        parse_mode="Markdown"
    )


async def cancel_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels pending problem report."""
    query = update.callback_query
    await query.answer()
    context.user_data["waiting_for_report"] = False
    await query.message.reply_text("Problem report canceled.")


async def _send_report_to_owner(update: Update, context: ContextTypes.DEFAULT_TYPE, user, report_text: str):
    """Helper to dispatch community problem report to the bot owner."""
    context.user_data["waiting_for_report"] = False
    user_label = f"@{user.username}" if user.username else "NoUsername"

    # Persist report in tracker database
    saved_report = tracker.add_report(user.id, user.username or "", user.first_name or "", report_text)
    report_id = saved_report.get("id", "NEW")

    # Confirm to user
    msg_target = update.effective_message or (update.callback_query.message if update.callback_query else None)
    if msg_target:
        await msg_target.reply_text(
            f"✅ **Thank you! Your report #{report_id} has been sent directly to the owner.**\n\n"
            "The owner will review the issue and fix it promptly! 🛠️",
            parse_mode="Markdown"
        )

    # Forward to owner / admins
    for admin_id in config.get_admin_chat_ids():
        try:
            admin_msg = (
                f"🚨 **New Community Problem Report #{report_id}**\n\n"
                f"👤 **User:** {user.first_name} ({user_label})\n"
                f"🆔 **User ID:** `{user.id}`\n\n"
                f"💬 **Problem Report:**\n{report_text}"
            )
            contact_url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"
            admin_keyboard = [
                [
                    InlineKeyboardButton("✅ Allow 30 Days VIP", callback_data=f"approve_vip:{user.id}:report"),
                    InlineKeyboardButton("💬 Contact User", url=contact_url)
                ]
            ]
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_msg,
                reply_markup=InlineKeyboardMarkup(admin_keyboard),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to forward report to admin {admin_id}: {e}")


async def my_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the user's numeric Telegram ID and role."""
    user = update.effective_user
    tracker.get_or_create_user(user.id, user.username or "", user.first_name or "")

    role = "Free Member"
    vip_info = tracker.get_vip_info(user.id)
    if config.is_owner(user.id, user.username):
        role = "👑 Bot Owner"
    elif config.is_admin(user.id, user.username):
        role = "🛡️ Admin"
    elif vip_info.get("is_vip"):
        role = f"💎 VIP ({vip_info.get('days_left')}d left)"

    text = (
        f"🆔 <b>Your Telegram ID:</b> <code>{user.id}</code>\n"
        f"👤 <b>Username:</b> @{user.username or 'None'}\n"
        f"🏷️ <b>Name:</b> {html.escape(user.first_name or 'User')}\n"
        f"👑 <b>Status:</b> {role}\n\n"
        f"<i>💡 To receive owner alerts, ensure your Telegram ID is in .env (<code>OWNER_ID={user.id}</code>).</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


def setup_user_handlers(application):
    """Registers command and message handlers."""
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("id", my_id_command))
    application.add_handler(CommandHandler("myid", my_id_command))
    application.add_handler(CallbackQueryHandler(status_command, pattern="^show_status$"))
    application.add_handler(CallbackQueryHandler(report_button_callback, pattern="^btn_report_problem$"))
    application.add_handler(CallbackQueryHandler(cancel_report_callback, pattern="^cancel_report$"))
    application.add_handler(CallbackQueryHandler(media_download_callback, pattern=r"^dl_media:(video|audio|images?):[a-f0-9]+$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_video_message))

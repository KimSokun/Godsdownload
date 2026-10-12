import os
import datetime
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from bot.config import config
from bot.tracker import tracker
from bot.platforms.base import get_cookie_file
from bot.auto_updater import (
    run_package_update,
    run_update,
    get_current_version,
    get_tracked_packages_versions,
)

logger = logging.getLogger(__name__)


def _get_target_message(update: Update):
    """Returns the message to reply to, supporting both regular commands and callback queries."""
    if getattr(update, "message", None) is not None:
        return update.message
    if getattr(update, "callback_query", None) and getattr(update.callback_query, "message", None):
        return update.callback_query.message
    return getattr(update, "effective_message", None)


def admin_only(func):
    """Decorator to ensure only bot admins can run administrative commands."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or not config.is_admin(user.id, user.username):
            if update.callback_query:
                await update.callback_query.answer("⛔ Access Denied: Admin privileges required.", show_alert=True)
            elif update.message:
                await update.message.reply_text("⛔ **Access Denied**: Admin privileges required.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def owner_only(func):
    """Decorator to ensure only the bot owner (@skun20000) can run owner-only commands."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or not config.is_owner(user.id, user.username):
            await update.message.reply_text(
                "⛔ **Access Denied**: Only the Bot Owner can use this command.",
                parse_mode="Markdown"
            )
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


@admin_only
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays bot usage statistics and installed package versions."""
    stats = tracker.get_stats()
    versions = get_tracked_packages_versions()
    version_lines = "\n".join([f"• `{pkg}`: `{ver}`" for pkg, ver in versions.items()])

    admin_list = [f"@{u}" for u in config.admin_usernames] + [str(i) for i in config.admin_ids]
    admin_str = ", ".join(admin_list) if admin_list else "None configured"

    text = (
        f"📊 **Godsdownload Admin Statistics**\n\n"
        f"👑 **Admins:** {admin_str}\n"
        f"👥 **Total Users:** {stats['total_users']}\n"
        f"💎 **VIP (Paid) Users:** {stats['total_vips']}\n"
        f"📥 **Total Videos Downloaded:** {stats['total_downloads']}\n"
        f"⚡ **Free Limit:** {config.free_download_limit} downloads\n"
        f"💰 **VIP Price:** ${config.vip_price_usd:.2f} USDT / Month\n\n"
        f"📦 **Installed Package Versions:**\n"
        f"{version_lines}\n\n"
        f"💡 _Tip: Use /upgrade_packages to automatically update all packages._"
    )
    msg = _get_target_message(update)
    await msg.reply_text(text, parse_mode="Markdown")


@owner_only
async def setvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Exclusive owner-only command to grant, extend, or revoke VIP membership.
    Only @skun20000 can invoke this command.
    Usage:
      /setvip <user_id or @username> [days]
    Examples:
      /setvip 123456789 30
      /setvip @someone 60
      /setvip 123456789 0  (revokes VIP)
    """
    stats = tracker.get_stats()
    total_vips = stats.get("total_vips", 0)
    total_users = stats.get("total_users", 0)

    if not context.args:
        usage_text = (
            f"👑 **Godsdownload Owner VIP Management**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Usage:**\n"
            f"• `/setvip <user_id> [days]` (e.g. `/setvip 123456789 30`)\n"
            f"• `/setvip <@username> [days]` (e.g. `/setvip @username 30`)\n\n"
            f"**Options:**\n"
            f"• If `[days]` is omitted, it defaults to **30 days**.\n"
            f"• Set `days` to `0` to revoke VIP status.\n\n"
            f"📊 **Current Active VIP Members:** `{total_vips}`\n"
            f"👥 **Total Registered Users:** `{total_users}`"
        )
        await update.message.reply_text(usage_text, parse_mode="Markdown")
        return

    target_input = context.args[0].strip()
    days = 30
    if len(context.args) > 1:
        try:
            days = int(context.args[1])
        except ValueError:
            await update.message.reply_text(
                "⚠️ Invalid duration format. Please provide days as a number (e.g. `/setvip 123456 30`).",
                parse_mode="Markdown"
            )
            return

    target_user_id = None
    target_username = None

    if target_input.isdigit():
        target_user_id = int(target_input)
        u_info = tracker.get_or_create_user(target_user_id)
        target_username = u_info.get("username")
    else:
        found = tracker.find_user(target_input)
        if found:
            target_user_id = int(found["user_id"])
            target_username = found.get("username")
        else:
            await update.message.reply_text(
                f"⚠️ Could not find user `{target_input}` in bot database.\n"
                "Tip: Ask the user for their numeric Telegram User ID and run `/setvip <id> [days]`.",
                parse_mode="Markdown"
            )
            return

    if days <= 0:
        tracker.set_vip(target_user_id, False)
        new_stats = tracker.get_stats()
        await update.message.reply_text(
            f"⚠️ User `{target_user_id}` (@{target_username or 'NoUsername'}) VIP status has been **revoked**.\n"
            f"👥 **Total Active VIP Members:** `{new_stats.get('total_vips', 0)}`",
            parse_mode="Markdown"
        )
        return

    tracker.set_vip(target_user_id, True, days=days)
    vip_info = tracker.get_vip_info(target_user_id)
    expiry_str = vip_info.get("vip_until_str") or f"{days} days"
    new_stats = tracker.get_stats()
    new_vips = new_stats.get("total_vips", 0)

    user_label = f"@{target_username}" if target_username else f"`{target_user_id}`"

    owner_confirm = (
        f"✅ **VIP Status Activated Successfully!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **Target User:** {user_label} (`{target_user_id}`)\n"
        f"💎 **Duration:** {days} Days (Monthly Subscription)\n"
        f"📅 **Subscription Active Until:** `{expiry_str}`\n"
        f"👥 **Total Active VIP Members:** `{new_vips}`"
    )
    await update.message.reply_text(owner_confirm, parse_mode="Markdown")

    # Alert target user with Contact Support button (hiding owner's raw username)
    try:
        user_alert = (
            f"🎉 **Congratulations! Your VIP Subscription is Active!**\n\n"
            f"You now have **UNLIMITED video downloads for {days} days**! 🚀\n"
            f"📅 **Subscription Active Until:** `{expiry_str}`\n\n"
            "Send any video link to start downloading without limits!"
        )
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Contact Support", url=config.admin_contact_url)],
            [InlineKeyboardButton("📊 My Account Status", callback_data="show_status")]
        ])
        await context.bot.send_message(
            chat_id=target_user_id,
            text=user_alert,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_user_id}: {e}")


@admin_only
async def grant_vip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Grants VIP status manually: /grant <user_id> [days (default 30)]"""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ Usage: `/grant <user_id> [days]` (e.g. `/grant 123456789 30`)", parse_mode="Markdown")
        return

    target_user_id = int(context.args[0])
    days = 30
    if len(context.args) > 1 and context.args[1].isdigit():
        days = int(context.args[1])

    tracker.set_vip(target_user_id, True, days=days)
    vip_info = tracker.get_vip_info(target_user_id)
    expiry_str = vip_info.get("vip_until_str") or f"{days} days"

    await update.message.reply_text(f"✅ User `{target_user_id}` granted VIP status for {days} days (Until {expiry_str}).", parse_mode="Markdown")

    # Notify target user
    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"🎉 **An administrator has granted you {days} days of VIP Unlimited status!**\n\n"
                 f"📅 **Subscription Active Until:** {expiry_str} 🚀\n"
                 "Enjoy unlimited video downloads!",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_user_id}: {e}")


@admin_only
async def revoke_vip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revokes VIP status manually: /revoke <user_id>"""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ Usage: `/revoke <user_id>`", parse_mode="Markdown")
        return

    target_user_id = int(context.args[0])
    tracker.set_vip(target_user_id, False)

    await update.message.reply_text(f"⚠️ User `{target_user_id}` VIP status has been revoked.", parse_mode="Markdown")


@admin_only
async def update_ytdlp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggers an immediate update of yt-dlp."""
    status_msg = await update.message.reply_text("⏳ Checking and upgrading `yt-dlp` package...", parse_mode="Markdown")
    success, msg = await run_update()
    await status_msg.edit_text(msg, parse_mode="Markdown")


@admin_only
async def upgrade_packages_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggers an immediate automatic package upgrade.
    Usage:
      /upgrade_packages       - Upgrades all dependencies (pip, yt-dlp, requirements.txt)
      /upgrade_packages <pkg> - Upgrades a specific package (e.g. /upgrade_packages yt-dlp)
    """
    target_pkg = context.args[0] if context.args else None
    target_label = f"`{target_pkg}`" if target_pkg else "all packages (`pip`, `yt-dlp`, `requirements.txt`)"
    msg = _get_target_message(update)
    status_msg = await msg.reply_text(
        f"⏳ **Upgrading {target_label}...**\nRunning background pip upgrade, please wait...",
        parse_mode="Markdown"
    )
    success, report = await run_package_update(package_name=target_pkg)
    await status_msg.edit_text(report, parse_mode="Markdown")


@admin_only
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcasts a message to all users: /broadcast <message>"""
    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/broadcast <message to send>`", parse_mode="Markdown")
        return

    broadcast_text = " ".join(context.args)
    user_ids = tracker.get_all_user_ids()
    total = len(user_ids)
    sent = 0
    failed = 0

    status_msg = await update.message.reply_text(f"📢 Starting broadcast to {total} users...")

    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📢 **Announcement:**\n\n{broadcast_text}",
                parse_mode="Markdown"
            )
            sent += 1
        except Exception:
            failed += 1

    await status_msg.edit_text(f"✅ Broadcast finished!\nSent: {sent}\nFailed: {failed}")


async def show_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles 'show_status' callback queries from inline buttons."""
    from bot.handlers.user_handlers import status_command
    await status_command(update, context)


@admin_only
async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggers an immediate graceful restart of the bot."""
    from bot.code_watcher import restart_bot
    msg = _get_target_message(update)
    await msg.reply_text(
        "🔄 **Restarting Godsdownload Bot...**\nPlease wait 3-5 seconds for the bot to reload.",
        parse_mode="Markdown"
    )
    await restart_bot(application=context.application)


@admin_only
async def diag_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin diagnostic command to inspect FFmpeg, yt-dlp, cookies, disk, and logs directly from Telegram."""
    import os
    import shutil
    import platform
    from bot.platforms.base import get_ffmpeg_path, get_cookie_file

    ffmpeg_exe = get_ffmpeg_path()
    cookie_file = get_cookie_file()

    # Disk space check
    try:
        total, used, free = shutil.disk_usage(config.temp_dir)
        disk_str = f"{free // (2**30)} GB free / {total // (2**30)} GB total"
    except Exception:
        disk_str = "Unknown"

    # yt-dlp version
    try:
        from yt_dlp.version import __version__ as ytdlp_ver
    except Exception:
        ytdlp_ver = "Unknown"

    # FFmpeg status
    if ffmpeg_exe:
        ffmpeg_str = f"✅ Available (`{ffmpeg_exe}`)"
    else:
        ffmpeg_str = "❌ **NOT FOUND** (Run `apt-get install -y ffmpeg` on server!)"

    # Cookies status
    if cookie_file and os.path.exists(cookie_file):
        cookie_size = os.path.getsize(cookie_file)
        cookie_str = f"✅ Present (`{cookie_size} bytes`)"
    else:
        cookie_str = "⚠️ Not configured / empty (Public mode active)"

    # Proxy status
    if config.proxy_url:
        proxy_str = f"🌐 Active (`{config.proxy_url}`)"
    else:
        proxy_str = "None (Direct connection)"

    # Read last errors from stderr log if available
    recent_errors = "No recent errors logged."
    for candidate_log in ["/var/log/godsdownload/stderr.log", os.path.join(os.path.dirname(config.temp_dir), "stderr.log")]:
        if os.path.exists(candidate_log):
            try:
                with open(candidate_log, "r", encoding="utf-8", errors="ignore") as f:
                    lines = [l.strip() for l in f.readlines() if l.strip()]
                    if lines:
                        recent_errors = "\n".join(lines[-4:])
                        break
            except Exception:
                pass

    import html
    safe_log = html.escape(recent_errors[:500]) if recent_errors else "No recent errors."

    html_text = (
        "🩺 <b>Godsdownload Server Diagnostic Report</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🖥️ <b>OS:</b> <code>{platform.system()} {platform.release()}</code>\n"
        f"🎬 <b>FFmpeg:</b> {ffmpeg_str}\n"
        f"📦 <b>yt-dlp:</b> <code>{ytdlp_ver}</code>\n"
        f"🍪 <b>Cookies:</b> {cookie_str}\n"
        f"🌐 <b>Proxy:</b> {proxy_str}\n"
        f"💾 <b>Disk Free:</b> <code>{disk_str}</code>\n"
        f"📁 <b>Temp Dir:</b> <code>{config.temp_dir}</code>\n\n"
        "📋 <b>Recent Log Output:</b>\n"
        f"<pre>{safe_log}</pre>\n\n"
        "💡 <i>To install FFmpeg on Linux: <code>apt-get update && apt-get install -y ffmpeg</code></i>"
    )
    msg = _get_target_message(update)
    try:
        await msg.reply_text(html_text, parse_mode="HTML")
    except Exception:
        plain_text = (
            "🩺 Godsdownload Server Diagnostic Report\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🖥️ OS: {platform.system()} {platform.release()}\n"
            f"🎬 FFmpeg: {ffmpeg_str}\n"
            f"📦 yt-dlp: {ytdlp_ver}\n"
            f"🍪 Cookies: {cookie_str}\n"
            f"🌐 Proxy: {proxy_str}\n"
            f"💾 Disk Free: {disk_str}\n"
            f"📁 Temp Dir: {config.temp_dir}\n\n"
            f"📋 Recent Log Output:\n{recent_errors[:500]}\n\n"
            "💡 To install FFmpeg on Linux: apt-get update && apt-get install -y ffmpeg"
        )
        await msg.reply_text(plain_text)


@owner_only
async def members_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Displays member breakdown and sends member.json, member.jsonl, member.txt, or member.png.
    Usage:
      /members        - Sends summary and member.json file
      /members png    - Sends member.png visual dashboard infographic
      /members jsonl  - Sends member.jsonl file
      /members txt    - Sends member.txt file
    """
    import os
    data = tracker.export_member_data()
    summary = data.get("summary", {})
    total_m = summary.get("total_members", 0)
    total_norm = summary.get("total_normal_users", 0)
    total_sub = summary.get("total_subscription_users", 0)
    total_dl = summary.get("total_downloads", 0)

    arg = context.args[0].lower() if context.args else "json"
    from bot.tracker import ROOT_MEMBER_JSON, ROOT_MEMBER_JSONL, ROOT_MEMBER_TXT, ROOT_MEMBER_PNG

    # 1. Visual PNG Infographic Dashboard option
    if any(k in arg for k in ["png", "pic", "photo", "image", "img"]):
        png_path = tracker.export_member_png(data)
        caption = (
            "👑 **Godsdownload Member Analytics Dashboard (PNG)**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Total Members:** `{total_m}`\n"
            f"💎 **VIP Subscribers:** `{total_sub}`\n"
            f"🆓 **Normal (Free) Users:** `{total_norm}`\n"
            f"⚡ **Total Video Downloads:** `{total_dl}`"
        )
        if os.path.exists(png_path):
            try:
                with open(png_path, "rb") as photo:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=photo,
                        caption=caption,
                        parse_mode="Markdown"
                    )
                return
            except Exception as e:
                logger.warning(f"Could not send member photo, falling back to document: {e}")

    # 2. Document file options (jsonl, txt, json)
    if "jsonl" in arg:
        target_file = ROOT_MEMBER_JSONL
        label = "member.jsonl"
    elif "txt" in arg:
        target_file = ROOT_MEMBER_TXT
        label = "member.txt"
    else:
        target_file = ROOT_MEMBER_JSON
        label = "member.json"

    text = (
        "👥 **Godsdownload Member Directory**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Total Members:** `{total_m}`\n"
        f"👑 **VIP Subscribers:** `{total_sub}`\n"
        f"🆓 **Normal (Free) Users:** `{total_norm}`\n"
        f"⚡ **Total Video Downloads:** `{total_dl}`\n\n"
        f"📁 Sending `{label}`...\n"
        "💡 _Tip: Run `/members png` to receive a visual PNG dashboard card!_"
    )
    msg = _get_target_message(update)
    await msg.reply_text(text, parse_mode="Markdown")

    if os.path.exists(target_file):
        try:
            with open(target_file, "rb") as doc:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=doc,
                    filename=label,
                    caption=f"👥 Godsdownload Members List ({total_m} members: {total_sub} VIP, {total_norm} Normal)"
                )
        except Exception as e:
            logger.warning(f"Could not send member document: {e}")


@admin_only
async def reports_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Displays or exports community problem reports submitted by users.
    Usage:
      /reports        - Displays interactive reports control dashboard
      /reports file   - Sends reports.json document
    """
    args = context.args or []
    if args and args[0].lower() in ["file", "json", "doc"]:
        from bot.tracker import REPORTS_FILE, ROOT_REPORTS_FILE
        target_file = REPORTS_FILE if os.path.exists(REPORTS_FILE) else ROOT_REPORTS_FILE
        if os.path.exists(target_file):
            try:
                with open(target_file, "rb") as doc:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=doc,
                        filename="reports.json",
                        caption="📋 Godsdownload Community Reports (JSON)"
                    )
                return
            except Exception as e:
                logger.warning(f"Could not send reports document: {e}")

    await show_owner_reports_dashboard(update, context, is_callback=False)


async def show_owner_reports_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback: bool = False):
    """Displays interactive Owner Report Control Dashboard with one-click action buttons."""
    recent = tracker.get_reports(limit=6)
    total = len(tracker.reports)
    pending = [r for r in tracker.reports if not r.get("resolved")]
    fixed = [r for r in tracker.reports if r.get("resolved")]

    if not recent:
        empty_text = (
            "👑 **Owner Report Control Dashboard**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📋 No problem reports have been submitted yet. Everything is running smoothly! ✅"
        )
        if is_callback and update.callback_query:
            await update.callback_query.edit_message_text(empty_text, parse_mode="Markdown")
        else:
            await update.effective_message.reply_text(empty_text, parse_mode="Markdown")
        return

    lines = [
        "👑 **Owner Report Control Dashboard**",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 **Total:** `{total}` | 🚨 **Pending:** `{len(pending)}` | ✅ **Fixed:** `{len(fixed)}`\n"
    ]

    keyboard = []
    for r in recent:
        rid = r.get("id")
        status_icon = "✅ Fixed" if r.get("resolved") else "🚨 Pending"
        lines.append(
            f"• **Report #{rid}** ({status_icon})\n"
            f"  👤 {r.get('first_name')} ({r.get('username')}) (`{r.get('user_id')}`)\n"
            f"  📅 `{r.get('created_at')}`\n"
            f"  💬 \"{r.get('text')}\"\n"
        )
        # For pending reports, provide quick action buttons directly in Telegram
        if not r.get("resolved"):
            uid = r.get("user_id")
            uname = r.get("username", "").lstrip("@")
            chat_url = f"https://t.me/{uname}" if uname and uname != "NoUsername" else f"tg://user?id={uid}"
            keyboard.append([
                InlineKeyboardButton(f"✅ 30D VIP #{rid}", callback_data=f"approve_vip:{uid}:report"),
                InlineKeyboardButton(f"✔️ Mark Fixed #{rid}", callback_data=f"mark_fixed:{rid}"),
                InlineKeyboardButton(f"💬 Chat #{rid}", url=chat_url)
            ])

    keyboard.append([
        InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="refresh_reports"),
        InlineKeyboardButton("📁 Export reports.json", callback_data="export_reports_doc")
    ])

    text = "\n".join(lines)
    reply_markup = InlineKeyboardMarkup(keyboard)

    if is_callback and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")


async def mark_fixed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Marks a report as resolved and notifies the user."""
    query = update.callback_query
    user = update.effective_user
    if not config.is_admin(user.id, user.username):
        await query.answer("❌ Admin authorization required.", show_alert=True)
        return

    data = query.data.split(":")
    report_id = int(data[1])
    report = tracker.resolve_report(report_id)

    if report:
        await query.answer(f"✅ Report #{report_id} marked as Fixed!")
        # Notify user that their problem has been fixed
        target_uid = report.get("user_id")
        if target_uid:
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=f"🎉 **Great News! Your Reported Issue #{report_id} Has Been Fixed!**\n\n"
                         "The bot owner has resolved the issue. Thank you for helping improve Godsdownload!\n\n"
                         "Feel free to send your video link again.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.debug(f"Could not notify user of fixed report: {e}")
        # Refresh dashboard
        await show_owner_reports_dashboard(update, context, is_callback=True)
    else:
        await query.answer("⚠️ Report not found.", show_alert=True)


async def refresh_reports_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refreshes the reports dashboard."""
    query = update.callback_query
    await query.answer("🔄 Dashboard refreshed!")
    await show_owner_reports_dashboard(update, context, is_callback=True)


async def export_reports_doc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends reports.json file to the admin in Telegram."""
    query = update.callback_query
    await query.answer()
    from bot.tracker import REPORTS_FILE, ROOT_REPORTS_FILE
    target_file = REPORTS_FILE if os.path.exists(REPORTS_FILE) else ROOT_REPORTS_FILE
    if os.path.exists(target_file):
        try:
            with open(target_file, "rb") as doc:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=doc,
                    filename="reports.json",
                    caption="📋 Godsdownload Community Reports (JSON File)"
                )
        except Exception as e:
            logger.warning(f"Could not send reports document: {e}")


@admin_only
async def maintenance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs full monthly maintenance & system audit and outputs report."""
    msg = _get_target_message(update)
    status_msg = await msg.reply_text("🔧 *Running Monthly Maintenance & Health Audit...*", parse_mode="Markdown")
    from bot.maintenance import run_monthly_maintenance
    try:
        report_data, summary = await run_monthly_maintenance()
        await status_msg.edit_text(summary, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Maintenance execution error: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Error running maintenance: `{e}`", parse_mode="Markdown")


@admin_only
async def admin_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Interactive Admin Dashboard.
    Strictly restricted to Bot Administrators and Owner.
    Regular users cannot access this menu.
    """
    stats = tracker.get_stats()
    user = update.effective_user
    text = (
        f"👑 <b>Godsdownload Admin Dashboard</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Admin:</b> {user.first_name} (<code>{user.id}</code>)\n"
        f"👥 <b>Total Registered Users:</b> <code>{stats.get('total_users', 0):,}</code>\n"
        f"💎 <b>Active VIP Members:</b> <code>{stats.get('total_vips', 0)}</code>\n"
        f"📥 <b>Total Downloads Processed:</b> <code>{stats.get('total_downloads', 0):,}</code>\n\n"
        f"⚡ <b>Quick Controls:</b>\n"
        f"• <code>/setvip &lt;user_id&gt; [days]</code> - Grant VIP access\n"
        f"• <code>/stats</code> - Analytics & package versions\n"
        f"• <code>/reports</code> - Review user bug reports\n"
        f"• <code>/maintenance</code> - Run storage & system health audit\n"
        f"• <code>/diag</code> - Server & network diagnostics\n"
        f"• <code>/upgrade_packages</code> - Auto-upgrade pip, yt-dlp\n"
        f"• <code>/restart</code> - Safely restart bot process"
    )
    keyboard = [
        [
            InlineKeyboardButton("📊 System Stats", callback_data="admin_action:stats"),
            InlineKeyboardButton("💎 VIP Members", callback_data="admin_action:members")
        ],
        [
            InlineKeyboardButton("📋 User Reports", callback_data="admin_action:reports"),
            InlineKeyboardButton("🔧 Maintenance Audit", callback_data="admin_action:maintenance")
        ],
        [
            InlineKeyboardButton("🩺 Server Diagnostics", callback_data="admin_action:diagnostics"),
            InlineKeyboardButton("📦 Upgrade Packages", callback_data="admin_action:upgrade_pkg")
        ],
        [
            InlineKeyboardButton("🔄 Restart Bot", callback_data="admin_action:restart")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = _get_target_message(update)
    if update.callback_query:
        await update.callback_query.answer()
        await msg.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await msg.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")


@admin_only
async def cookies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Checks the status of cookies.txt and provides instructions to upload a new one."""
    cookie_file = get_cookie_file()
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    target_path = os.path.join(root_dir, config.cookies_file or "cookies.txt")

    file_exists = os.path.exists(target_path)
    file_size = os.path.getsize(target_path) if file_exists else 0
    mtime_str = "Never"
    yt_count = 0
    total_cookies = 0

    if file_exists:
        mtime = os.path.getmtime(target_path)
        mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip() and not line.startswith("#") and "\t" in line:
                        total_cookies += 1
                        if "youtube" in line.lower():
                            yt_count += 1
        except Exception:
            pass

    status_icon = "✅ Active" if cookie_file else "⚠️ Missing or Inactive"
    text = (
        f"🍪 <b>Cookies Status & Management</b>\n\n"
        f"<b>Status:</b> {status_icon}\n"
        f"<b>Path:</b> <code>{target_path}</code>\n"
        f"<b>File Exists:</b> {'Yes' if file_exists else 'No'} ({file_size:,} bytes)\n"
        f"<b>Last Modified:</b> {mtime_str}\n"
        f"<b>Total Cookies:</b> {total_cookies:,}\n"
        f"<b>YouTube Cookies:</b> {yt_count:,}\n\n"
        f"📤 <b>How to update cookies:</b>\n"
        f"Simply send your exported <code>cookies.txt</code> file directly as a Document/File in this chat. The bot will validate and apply it automatically without needing server access!"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")


@admin_only
async def upload_cookies_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows admins to upload a new cookies.txt file directly to update YouTube/platform cookies."""
    if not update.message or not update.message.document:
        return

    doc = update.message.document
    filename = (doc.file_name or "").lower()
    caption = (update.message.caption or "").lower()

    if not (filename == "cookies.txt" or "cookie" in filename or "cookie" in caption):
        return

    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    target_path = os.path.join(root_dir, config.cookies_file or "cookies.txt")

    status_msg = await update.message.reply_text("⏳ <i>Processing and validating cookies file...</i>", parse_mode="HTML")
    try:
        tg_file = await doc.get_file()
        content_bytes = await tg_file.download_as_bytearray()
        text_content = content_bytes.decode("utf-8", errors="ignore")

        lines = [l for l in text_content.splitlines() if l.strip() and not l.startswith("#") and "\t" in l]
        if len(lines) < 2:
            await status_msg.edit_text(
                "❌ <b>Invalid Cookie File</b>: The uploaded file does not appear to be a valid Netscape tab-separated cookies.txt file.",
                parse_mode="HTML"
            )
            return

        with open(target_path, "wb") as f:
            f.write(content_bytes)

        yt_cookies = sum(1 for l in lines if "youtube" in l.lower())
        await status_msg.edit_text(
            f"✅ <b>Cookies Successfully Updated!</b>\n\n"
            f"• <b>Saved to:</b> <code>{target_path}</code>\n"
            f"• <b>Total Cookies:</b> {len(lines):,}\n"
            f"• <b>YouTube Cookies:</b> {yt_cookies:,}\n"
            f"• <b>File Size:</b> {len(content_bytes):,} bytes\n\n"
            f"yt-dlp will now automatically use this updated cookies file for YouTube downloads.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to save uploaded cookies file: {e}")
        await status_msg.edit_text(f"❌ <b>Error saving cookies file:</b> {e}", parse_mode="HTML")


async def admin_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles admin dashboard interactive button clicks with strict admin check."""
    query = update.callback_query
    user = update.effective_user
    if not user or not config.is_admin(user.id, user.username):
        await query.answer("⛔ Access denied: Admin privileges required.", show_alert=True)
        return

    data = query.data
    action = data.split(":")[1] if ":" in data else data
    if action == "stats":
        await query.answer()
        await stats_command(update, context)
    elif action == "members":
        await query.answer()
        await members_command(update, context)
    elif action == "reports":
        await query.answer()
        await reports_command(update, context)
    elif action == "maintenance":
        await query.answer()
        await maintenance_command(update, context)
    elif action in ("diagnostics", "diag", "check"):
        await query.answer()
        await diag_command(update, context)
    elif action == "upgrade_pkg":
        await query.answer()
        await upgrade_packages_command(update, context)
    elif action == "restart":
        await query.answer()
        await restart_command(update, context)
    elif action in ("menu", "panel"):
        await admin_menu_command(update, context)


def setup_admin_handlers(app):
    """Registers admin commands."""
    app.add_handler(CommandHandler("admin", admin_menu_command))
    app.add_handler(CommandHandler("panel", admin_menu_command))
    app.add_handler(CallbackQueryHandler(admin_menu_command, pattern="^open_admin_panel$"))
    app.add_handler(CallbackQueryHandler(admin_action_callback, pattern=r"^admin_action:"))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("cookies", cookies_command))
    app.add_handler(CommandHandler("cookie", cookies_command))
    app.add_handler(CommandHandler("maintenance", maintenance_command))
    app.add_handler(CommandHandler("monthly_maintenance", maintenance_command))
    app.add_handler(CommandHandler("setvip", setvip_command))
    app.add_handler(CommandHandler("grant", grant_vip_command))
    app.add_handler(CommandHandler("revoke", revoke_vip_command))
    app.add_handler(CommandHandler("reports", reports_command))
    app.add_handler(CommandHandler("report_list", reports_command))
    app.add_handler(CallbackQueryHandler(mark_fixed_callback, pattern=r"^mark_fixed:\d+$"))
    app.add_handler(CallbackQueryHandler(refresh_reports_callback, pattern="^refresh_reports$"))
    app.add_handler(CallbackQueryHandler(export_reports_doc_callback, pattern="^export_reports_doc$"))
    app.add_handler(CommandHandler("update_ytdlp", update_ytdlp_command))
    app.add_handler(CommandHandler("upgrade_packages", upgrade_packages_command))
    app.add_handler(CommandHandler("upgrade_package", upgrade_packages_command))
    app.add_handler(CommandHandler("update_packages", upgrade_packages_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("diag", diag_command))
    app.add_handler(CommandHandler("check", diag_command))
    app.add_handler(CommandHandler("members", members_command))
    app.add_handler(CommandHandler("member", members_command))
    app.add_handler(CallbackQueryHandler(show_status_callback, pattern="^show_status$"))
    app.add_handler(MessageHandler(filters.Document.ALL & ~filters.COMMAND, upload_cookies_document))


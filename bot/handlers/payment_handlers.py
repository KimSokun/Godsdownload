import io
import logging
import qrcode
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from bot.config import config
from bot.tracker import tracker

logger = logging.getLogger(__name__)

WAITING_FOR_PAYMENT_PROOF = 1


def generate_usdt_qr_bytes(address: str) -> io.BytesIO:
    """Generates a QR code for USDT wallet in memory."""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(address)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = io.BytesIO()
    bio.name = "usdt_qr.png"
    img.save(bio, "PNG")
    bio.seek(0)
    return bio


async def upgrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays VIP USDT-TRON payment details and QR code."""
    user = update.effective_user
    can_dl, count, limit, is_vip = tracker.can_download(user.id)
    stats = tracker.get_stats()
    total_users = stats.get("total_users", 0)

    if is_vip:
        vip_info = tracker.get_vip_info(user.id)
        days_left = vip_info.get("days_left", 30)
        header = f"👑 **[ VIP MEMBER • {days_left}D LEFT ]** • 👥 **{total_users:,} Members**"
    else:
        remaining = max(0, limit - count)
        header = f"🆓 **[ FREE MEMBER • {remaining}/{limit} TODAY ]** • 👥 **{total_users:,} Members**"

    user_label = f"@{user.username}" if user.username else "NoUsername"
    text = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 **Godsdownload VIP Subscription (Monthly)**\n\n"
        f"💵 **Price:** **${config.vip_price_usd:.2f} USDT / Month** (30 Days Unlimited Access)\n"
        f"🌐 **Network:** **TRON (TRC-20)**\n\n"
        f"💳 **USDT TRON Wallet Address (Click to Copy):**\n"
        f"`{config.usdt_tron_address}`\n\n"
        f"👤 **Your Account Info:**\n"
        f"🆔 **Your User ID:** `{user.id}` (Click to copy)\n"
        f"👤 **Your Username:** {user_label}\n\n"
        f"📌 **How to Activate (30 Days VIP):**\n"
        f"1. Send **${config.vip_price_usd:.2f} USDT** via TRC-20 to the address above.\n"
        f"2. Click **'✅ I Have Paid (Alert Owner)'** below.\n"
        f"3. Send your **User ID (`{user.id}`)** and **Username** to the owner so the owner can verify and allow 30 Days VIP!"
    )

    keyboard = [
        [InlineKeyboardButton("📝 Submit USDT TxID", callback_data="btn_submit_proof:USDT")],
        [InlineKeyboardButton("✅ I Have Paid (Alert Owner)", callback_data="btn_paid_done")],
        [
            InlineKeyboardButton("💬 Contact Owner / Support", url=config.admin_contact_url),
            InlineKeyboardButton("📊 My Account Status", callback_data="show_status")
        ],
        [InlineKeyboardButton("🚨 Report Problem", callback_data="btn_report_problem")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    chat_id = update.effective_chat.id
    qr_bytes = generate_usdt_qr_bytes(config.usdt_tron_address)

    if update.callback_query:
        await update.callback_query.answer()
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=qr_bytes,
            caption=text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=qr_bytes,
            caption=text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    # Auto-notify owner when user clicks buy / views upgrade
    user_label = f"@{user.username}" if user.username else "NoUsername"
    contact_url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"
    admin_ids = config.get_admin_chat_ids()
    if not admin_ids:
        logger.warning("⚠️ No admin/owner chat IDs configured to receive buy click alert. Please set OWNER_ID or ADMIN_IDS in .env.")
    for admin_id in admin_ids:
        try:
            admin_msg = (
                f"🛒 **User Clicked Buy / Upgrade VIP**\n\n"
                f"👤 **User:** {user.first_name} ({user_label})\n"
                f"🆔 **User ID:** `{user.id}`\n"
                f"💰 **VIP Price:** ${config.vip_price_usd:.2f} USDT / Month\n"
                f"⚡ **Action:** User opened VIP buy page"
            )
            admin_kb = [
                [
                    InlineKeyboardButton("✅ Allow 30 Days VIP", callback_data=f"approve_vip:{user.id}:buy"),
                    InlineKeyboardButton("💬 Contact User", url=contact_url)
                ]
            ]
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_msg,
                reply_markup=InlineKeyboardMarkup(admin_kb),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id} of buy click: {e}")


async def show_usdt_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Alias for upgrade_command."""
    await upgrade_command(update, context)


async def prompt_submit_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompts user to send USDT transaction hash."""
    query = update.callback_query
    if query:
        await query.answer()
        context.user_data["payment_method"] = "USDT (TRON)"

        await query.message.reply_text(
            "📝 **Please reply with your USDT TRON (TRC-20) Transaction Hash (TxID):**\n\n"
            "Example: `4f9e8a7b6c5d...`\n"
            "(Or type /cancel to abort)",
            parse_mode="Markdown"
        )
    return WAITING_FOR_PAYMENT_PROOF


async def receive_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives and forwards payment proof to admins."""
    user = update.effective_user
    proof_text = update.message.text.strip()
    pay_method = context.user_data.get("payment_method", "USDT (TRON)")

    if len(proof_text) < 4:
        await update.message.reply_text("⚠️ Please enter a valid transaction hash or type /cancel.")
        return WAITING_FOR_PAYMENT_PROOF

    tracker.submit_tx(user.id, f"[{pay_method}] {proof_text}")

    user_label = f"@{user.username}" if user.username else "NoUsername"
    client_keyboard = [
        [InlineKeyboardButton("💬 Send ID to Owner (Fast VIP)", url=config.admin_contact_url)],
        [InlineKeyboardButton("📊 My Account Status", callback_data="show_status")],
        [InlineKeyboardButton("🚨 Report Problem", callback_data="btn_report_problem")]
    ]
    await update.message.reply_text(
        f"✅ **{pay_method} Details Received!**\n\n"
        f"🔗 **TxID:** `{proof_text}`\n"
        f"🆔 **Your User ID:** `{user.id}` (Click to copy)\n"
        f"👤 **Your Username:** {user_label}\n\n"
        f"📌 **How to get approved fast:**\n"
        f"Send your **User ID (`{user.id}`)** to the owner below. The owner will check the wallet and allow your **30 Days VIP**! 🚀\n\n"
        f"Click below to message the owner directly:",
        reply_markup=InlineKeyboardMarkup(client_keyboard),
        parse_mode="Markdown"
    )

    # Notify admins
    user_label = f"@{user.username}" if user.username else "NoUsername"
    contact_url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"
    for admin_id in config.get_admin_chat_ids():
        try:
            admin_msg = (
                f"🚨 **New VIP Payment Submission ({pay_method})**\n\n"
                f"👤 **User:** {user.first_name} ({user_label})\n"
                f"🆔 **User ID:** `{user.id}`\n"
                f"💳 **Method:** {pay_method}\n"
                f"💰 **Amount:** ${config.vip_price_usd} USDT\n"
                f"🔗 **TxID:** `{proof_text}`"
            )
            admin_keyboard = [
                [
                    InlineKeyboardButton("✅ Allow 30 Days VIP", callback_data=f"approve_vip:{user.id}:{proof_text[:8]}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject_vip:{user.id}:{proof_text[:8]}")
                ],
                [
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
            logger.error(f"Failed to notify admin {admin_id}: {e}")

    return ConversationHandler.END


async def pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct command /pay <tx_hash>"""
    user = update.effective_user
    args = context.args

    if not args:
        await update.message.reply_text(
            "⚠️ Usage: `/pay <your_usdt_txid>`\n\nExample: `/pay 4f9e8a7b6c5d...`",
            parse_mode="Markdown"
        )
        return

    proof_text = " ".join(args).strip()
    tracker.submit_tx(user.id, f"[USDT-TRON] {proof_text}")

    user_label = f"@{user.username}" if user.username else "NoUsername"
    client_keyboard = [
        [InlineKeyboardButton("💬 Send ID to Owner (Fast VIP)", url=config.admin_contact_url)],
        [InlineKeyboardButton("📊 My Account Status", callback_data="show_status")],
        [InlineKeyboardButton("🚨 Report Problem", callback_data="btn_report_problem")]
    ]
    await update.message.reply_text(
        f"✅ **USDT-TRON TxID Received!**\n\n"
        f"🔗 **TxID:** `{proof_text}`\n"
        f"🆔 **Your User ID:** `{user.id}` (Click to copy)\n"
        f"👤 **Your Username:** {user_label}\n\n"
        f"📌 **How to get approved fast:**\n"
        f"Send your **User ID (`{user.id}`)** to the owner below. The owner will check the wallet and allow your **30 Days VIP**! 🚀\n\n"
        f"Click below to message the owner directly:",
        reply_markup=InlineKeyboardMarkup(client_keyboard),
        parse_mode="Markdown"
    )

    # Notify admins
    user_label = f"@{user.username}" if user.username else "NoUsername"
    contact_url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"
    for admin_id in config.get_admin_chat_ids():
        try:
            admin_msg = (
                f"🚨 **New VIP Payment Submission (/pay)**\n\n"
                f"👤 **User:** {user.first_name} ({user_label})\n"
                f"🆔 **User ID:** `{user.id}`\n"
                f"💰 **Amount:** ${config.vip_price_usd} USDT\n"
                f"🔗 **TxID:** `{proof_text}`"
            )
            admin_keyboard = [
                [
                    InlineKeyboardButton("✅ Allow 30 Days VIP", callback_data=f"approve_vip:{user.id}:{proof_text[:8]}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject_vip:{user.id}:{proof_text[:8]}")
                ],
                [
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
            logger.error(f"Failed to notify admin {admin_id}: {e}")


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels ongoing conversation."""
    await update.message.reply_text("Action canceled.")
    return ConversationHandler.END


async def admin_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles admin approve/reject buttons."""
    query = update.callback_query
    admin_user = update.effective_user

    if not config.is_admin(admin_user.id, admin_user.username):
        await query.answer("❌ Admin authorization required.", show_alert=True)
        return

    data = query.data
    action, target_user_id, short_ref = data.split(":")
    target_user_id = int(target_user_id)

    if action == "approve_vip":
        tracker.set_vip(target_user_id, True, days=30)
        vip_info = tracker.get_vip_info(target_user_id)
        expiry_str = vip_info.get("vip_until_str") or "30 days"
        await query.answer("✅ VIP Approved (30 Days)!")
        await query.edit_message_text(
            f"{query.message.text}\n\n━━━━━━━━━━━━━━━\n✅ **APPROVED (Monthly Subscription) by Admin ({update.effective_user.first_name})**",
            parse_mode="Markdown"
        )
        # Notify user with Contact Support button (hiding owner's raw username)
        try:
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Contact Support", url=config.admin_contact_url)],
                [InlineKeyboardButton("📊 My Account Status", callback_data="show_status")]
            ])
            await context.bot.send_message(
                chat_id=target_user_id,
                text="🎉 **Congratulations! Your VIP Payment has been approved!**\n\n"
                     f"You now have **UNLIMITED video downloads for 30 days (Monthly Subscription)**! 🚀\n"
                     f"📅 **Subscription Valid Until:** {expiry_str}\n\n"
                     "Send any video link to start downloading without limits.",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send VIP notification to user {target_user_id}: {e}")

    elif action == "reject_vip":
        await query.answer("❌ VIP Rejected.")
        await query.edit_message_text(
            f"{query.message.text}\n\n━━━━━━━━━━━━━━━\n❌ **REJECTED by Admin ({update.effective_user.first_name})**",
            parse_mode="Markdown"
        )
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text="❌ **Payment Verification Notice**\n\n"
                     "Your submitted payment details could not be verified in the wallet. If you believe this is a mistake, please contact support below:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Contact Support", url=config.admin_contact_url)]
                ]),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send rejection notification to user {target_user_id}: {e}")


async def handle_paid_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles user reporting payment done without TxID or asking admin to check wallet."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    tracker.get_or_create_user(user.id, user.username or "", user.first_name or "")

    user_label = f"@{user.username}" if user.username else "No Username set"
    client_alert = (
        f"⏳ **Payment Done Reported!**\n\n"
        f"👤 **Your Account Info:**\n"
        f"🆔 **Your User ID:** `{user.id}` (Click to copy)\n"
        f"👤 **Your Username:** {user_label}\n\n"
        f"📌 **NEXT STEP TO ACTIVATE 30 DAYS VIP:**\n"
        f"Please send your **User ID (`{user.id}`)** and **Username ({user_label})** to the owner / support so the owner can verify and immediately grant you **30 Days VIP**! 👑\n\n"
        f"Click **'💬 Send ID & Username to Owner'** below to message the owner directly:"
    )
    keyboard = [
        [InlineKeyboardButton("💬 Send ID & Username to Owner", url=config.admin_contact_url)],
        [InlineKeyboardButton("📝 Submit USDT TxID (Optional)", callback_data="btn_submit_proof:USDT")],
        [InlineKeyboardButton("📊 My Account Status", callback_data="show_status")],
        [InlineKeyboardButton("🚨 Report Problem", callback_data="btn_report_problem")]
    ]
    await query.message.reply_text(
        client_alert,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

    # Notify admins with interactive buttons: owner will allow 30 for user
    contact_url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"
    admin_ids = config.get_admin_chat_ids()
    if not admin_ids:
        logger.warning("⚠️ No admin/owner chat IDs configured to receive 'I already paid' alert. Please set OWNER_ID or ADMIN_IDS in .env.")
    for admin_id in admin_ids:
        try:
            admin_msg = (
                f"🚨 **User Clicked \"I Already Paid\" (Waiting for 30D VIP)**\n\n"
                f"👤 **User:** {user.first_name} ({user_label})\n"
                f"🆔 **User ID:** `{user.id}`\n"
                f"💰 **Expected Amount:** ${config.vip_price_usd:.2f} USDT (TRC-20)\n\n"
                f"💡 User was instructed to send you their ID: `{user.id}` and username.\n"
                f"Click below to allow 30 Days VIP:"
            )
            admin_keyboard = [
                [
                    InlineKeyboardButton("✅ Allow 30 Days VIP", callback_data=f"approve_vip:{user.id}:paid"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject_vip:{user.id}:paid")
                ],
                [
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
            logger.error(f"Failed to notify admin {admin_id}: {e}")


def setup_payment_handlers(app):
    """Registers payment commands and interactive handlers."""
    app.add_handler(CommandHandler("upgrade", upgrade_command))
    app.add_handler(CommandHandler("pay", pay_command))
    app.add_handler(CallbackQueryHandler(upgrade_command, pattern="^(show_upgrade|pay_usdt)$"))
    app.add_handler(CallbackQueryHandler(handle_paid_done_callback, pattern="^btn_paid_done$"))

    # Admin action callbacks
    app.add_handler(CallbackQueryHandler(admin_payment_callback, pattern="^(approve_vip|reject_vip):"))

    # Conversation handler for payment proof text reply
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(prompt_submit_proof, pattern="^btn_submit_proof:")],
        states={
            WAITING_FOR_PAYMENT_PROOF: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_payment_proof)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
        per_chat=True,
        per_user=True,
    )
    app.add_handler(conv_handler)

"""Bot handlers package."""
from bot.handlers.user_handlers import setup_user_handlers
from bot.handlers.payment_handlers import setup_payment_handlers
from bot.handlers.admin_handlers import setup_admin_handlers

__all__ = ["setup_user_handlers", "setup_payment_handlers", "setup_admin_handlers"]
